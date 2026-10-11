"""BrainVision loading with manually selected R/LR block schedules.

Repeat timing is reconstructed from block tables, with video time zero at the
recording's first sample. EEG markers are not required or used for alignment.
The old session-order helpers remain for historical diagnostics only.
"""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np


SESSION_ORDER_NAME = "cond seq.txt"
SCHEDULE_NAME = "mri_eeg_order_{video:02d}_conn_microrepeats.csv"
VIDEO_START_MARKER = "S 3"
VIDEO_END_MARKER = "S 4"
VIDEO_DURATION_S = 334.0
VIDEO_DURATION_TOLERANCE_S = 0.5
VIDEO_OPTIONS = tuple(f"Video{n}{family}" for family in ("R", "LR") for n in (1, 2, 3))
BLOCK_CUE_S = 2.0
REPEAT_DURATION_S = 4.0

CONDITION_ALIASES = {
    "om": "overt",
    "overt": "overt",
    "real": "overt",
    "qm": "quasi",
    "quasi": "quasi",
    "im": "imagery",
    "mi": "imagery",
    "imag": "imagery",
    "imagery": "imagery",
}


@dataclass(frozen=True)
class SessionEntry:
    run: int
    session_label: str
    condition: str
    video: int


@dataclass(frozen=True)
class Schedule:
    path: Path
    events: dict[str, tuple[np.ndarray, np.ndarray]]
    source_paths: tuple[Path, ...] = ()
    duration_s: float = VIDEO_DURATION_S


def read_video_schedule(video: str, hint: str | Path | None = None,
                        wanted: Iterable[str] | None = None) -> Schedule:
    """Derive four-second repeats after each block's two-second hand cue.

    The companion conn_blocks table supplies actual rest/fixation intervals.
    No microrepeat CSV or automatic session-order lookup is used.
    """
    if video not in VIDEO_OPTIONS:
        raise ValueError(f"Select a video explicitly: {', '.join(VIDEO_OPTIONS)}")
    family = "right_only" if video.endswith("R") and not video.endswith("LR") else "lr"
    prefix = f"mri_eeg_{family}_10_14_16_18_{int(video[5]):02d}"
    root = Path(hint) if hint is not None else Path(__file__).resolve().parent.parent / "schedules"
    path = root / f"{prefix}_blocks.csv"
    companion = root / f"{prefix}_conn_blocks.csv"
    block_events = read_schedule(companion).events
    repeats = {"left": [], "right": []}
    blocks = {"left": [], "right": []}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"block_number", "onset", "duration", "hand_condition", "microrepeats"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path.name}: missing block columns")
        for index, row in enumerate(reader, 1):
            hand = row["hand_condition"].strip()
            onset, duration = float(row["onset"]), float(row["duration"])
            count = int(row["microrepeats"])
            if (hand not in blocks or int(row["block_number"]) != index
                    or not np.isfinite(onset) or onset < 0 or count <= 0
                    or not np.isfinite(duration)
                    or not np.isclose(duration, BLOCK_CUE_S + count * REPEAT_DURATION_S)):
                raise ValueError(f"{path.name}: invalid block {index}")
            blocks[hand].append((onset, duration))
            repeats[hand].extend(onset + BLOCK_CUE_S + np.arange(count) * REPEAT_DURATION_S)
    expected_hands = {"right"} if family == "right_only" else {"left", "right"}
    if {hand for hand, values in blocks.items() if values} != expected_hands:
        raise ValueError(f"{path.name}: hand conditions do not match {video}")
    expected_conditions = {f"{hand}_task_block" for hand in expected_hands} | {
        "rest", "initial_fixation", "final_fixation"}
    if set(block_events) != expected_conditions:
        raise ValueError(f"{companion.name}: unexpected or missing block conditions")
    for hand in expected_hands:
        actual = np.asarray(blocks[hand]).T
        expected = np.asarray(block_events[f"{hand}_task_block"])
        if actual.shape != expected.shape or not np.allclose(actual, expected):
            raise ValueError(f"{path.name}: {hand} blocks disagree with {companion.name}")
    # Together the block and rest/fixation intervals must cover the video once.
    intervals = sorted((float(t), float(t + d)) for onsets, durations in block_events.values()
                       for t, d in zip(onsets, durations))
    end = 0.0
    for start, stop in intervals:
        if not np.isclose(start, end):
            raise ValueError(f"{companion.name}: gap or overlap at {start:g}s")
        end = stop
    events = {name: block_events[name] for name in ("rest", "initial_fixation", "final_fixation")}
    events.update({f"{hand}_microrepeat": (np.asarray(values), np.full(len(values), REPEAT_DURATION_S))
                   for hand, values in repeats.items() if values})
    if wanted is not None:
        wanted = set(wanted)
        missing = wanted - events.keys()
        if missing:
            raise KeyError(f"{video}: missing conditions {sorted(missing)}")
        events = {name: values for name, values in events.items() if name in wanted}
    return Schedule(path, events, (path, companion), end)


def _condition(label: str) -> str:
    match = re.fullmatch(r"([A-Za-z]+)(\d+)", label.strip())
    if not match:
        raise ValueError(
            f"invalid session label {label!r}; expected e.g. om1, qm2 or im3")
    prefix = match.group(1).lower()
    if prefix not in CONDITION_ALIASES:
        raise ValueError(f"unknown condition prefix in session label {label!r}")
    return CONDITION_ALIASES[prefix]


def read_session_order(path: str | Path) -> list[SessionEntry]:
    """Read and validate the ordinal ``cond seq.txt`` mapping."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"session order file not found: {path}")
    entries: list[SessionEntry] = []
    seen_labels: set[str] = set()
    for line_number, raw in enumerate(
            path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"(?P<label>[A-Za-z]+\d+)\s+video\s*(?P<video>\d+)",
            line, flags=re.IGNORECASE)
        if not match:
            raise ValueError(
                f"{path.name}:{line_number}: expected '<condition> video1..3', "
                f"got {raw!r}")
        label = match.group("label").lower()
        video = int(match.group("video"))
        if video not in {1, 2, 3}:
            raise ValueError(
                f"{path.name}:{line_number}: video must be 1, 2 or 3")
        if label in seen_labels:
            raise ValueError(
                f"{path.name}:{line_number}: duplicate session label {label!r}")
        seen_labels.add(label)
        entries.append(SessionEntry(
            run=len(entries) + 1,
            session_label=label,
            condition=_condition(label),
            video=video,
        ))
    if not entries:
        raise ValueError(f"session order file is empty: {path}")
    return entries


def run_number(path: str | Path) -> int:
    """Extract the ordinal p-number from a reviewed BrainVision filename."""
    stem = Path(path).stem
    matches = list(re.finditer(r"(?:^|_)(?:p|block)(\d+)(?:_|$)", stem,
                               flags=re.IGNORECASE))
    if len(matches) != 1:
        raise ValueError(
            f"cannot identify one pN/blockNN run number in {Path(path).name!r}; "
            "select the video schedule explicitly")
    return int(matches[0].group(1))


def session_entry(path: str | Path,
                  order_path: str | Path | None = None) -> SessionEntry:
    """Return the session-order entry belonging to one recording."""
    path = Path(path)
    order_path = Path(order_path) if order_path else session_order_path(path)
    entries = read_session_order(order_path)
    run = run_number(path)
    if not 1 <= run <= len(entries):
        raise ValueError(
            f"{path.name}: p{run} has no line in {order_path.name} "
            f"({len(entries)} nonblank lines)")
    return entries[run - 1]


def session_order_path(path):
    path = Path(path)
    for directory in (path.parent, path.parent.parent):
        candidate = directory / SESSION_ORDER_NAME
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"No {SESSION_ORDER_NAME} beside {path.name} or in its parent folder; "
        "select Video 1, 2 or 3 explicitly")


def _candidate_schedule_dirs(hint: str | Path | None = None) -> Iterable[Path]:
    if hint:
        root = Path(hint).expanduser()
        yield root
        yield root / "Stimuli" / "Stimuli_3seq"
    project = Path(__file__).resolve().parent.parent
    yield project / "schedules"
    for ancestor in (project, *project.parents):
        yield ancestor / "LSL_recorder_MEG" / "Stimuli" / "Stimuli_3seq"


def schedule_path(video: int, hint: str | Path | None = None) -> Path:
    """Locate the canonical microrepeat schedule for video 1, 2 or 3."""
    video = int(video)
    if video not in {1, 2, 3}:
        raise ValueError("video must be 1, 2 or 3")
    name = SCHEDULE_NAME.format(video=video)
    checked: list[Path] = []
    seen: set[str] = set()
    for folder in _candidate_schedule_dirs(hint):
        key = str(folder.resolve()).casefold()
        if key in seen:
            continue
        seen.add(key)
        candidate = folder / name
        checked.append(candidate)
        if candidate.is_file():
            return candidate
    rendered = "\n  ".join(str(path) for path in checked)
    raise FileNotFoundError(
        f"stimulus schedule {name!r} was not found; checked:\n  {rendered}")


def read_schedule(path: str | Path,
                  wanted: Iterable[str] | None = None) -> Schedule:
    """Read condition onsets/durations from a ``*_microrepeats.csv`` file."""
    path = Path(path)
    wanted_set = None if wanted is None else set(wanted)
    events: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"condition_name", "onsets", "durations"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(
                f"{path.name} must contain columns {sorted(required)}")
        for row_number, row in enumerate(reader, 2):
            name = row["condition_name"].strip()
            if wanted_set is not None and name not in wanted_set:
                continue
            try:
                onsets = np.asarray(
                    [float(value) for value in row["onsets"].split()], dtype=float)
                durations = np.asarray(
                    [float(value) for value in row["durations"].split()], dtype=float)
            except ValueError as exc:
                raise ValueError(
                    f"{path.name}:{row_number}: non-numeric onset/duration") from exc
            if onsets.size == 0 or onsets.size != durations.size:
                raise ValueError(
                    f"{path.name}:{row_number}: onset/duration counts differ or are empty")
            if (not np.all(np.isfinite(onsets))
                    or not np.all(np.isfinite(durations))
                    or np.any(onsets < 0) or np.any(durations <= 0)):
                raise ValueError(
                    f"{path.name}:{row_number}: invalid onset or duration")
            if name in events:
                raise ValueError(f"{path.name}:{row_number}: duplicate condition {name!r}")
            events[name] = (onsets, durations)
    if wanted_set is not None:
        missing = wanted_set - events.keys()
        if missing:
            raise KeyError(f"{path.name}: missing conditions {sorted(missing)}")
    return Schedule(path=path, events=events)


def recording_schedule(path: str | Path, wanted: Iterable[str], *,
                       order_path: str | Path | None = None,
                       schedule_dir: str | Path | None = None) -> tuple[SessionEntry, Schedule]:
    entry = session_entry(path, order_path)
    schedule = read_schedule(schedule_path(entry.video, schedule_dir), wanted)
    return entry, schedule


def _marker_token(value: str) -> str:
    # MNE prefixes BrainVision descriptions with their marker type
    # ("Stimulus/S  3"). Matching the final component also accepts a raw "S 3".
    return re.sub(r"[^a-z0-9]", "", value.rsplit("/", 1)[-1].lower())


def marker_onsets(raw, marker: str) -> np.ndarray:
    token = _marker_token(marker)
    return np.asarray([
        float(onset) for description, onset in
        zip(raw.annotations.description, raw.annotations.onset)
        if _marker_token(str(description)) == token
    ], dtype=float)


def video_start(raw, source_name: str = "recording", *,
                expected_duration_s: float = VIDEO_DURATION_S) -> float:
    """Legacy diagnostic helper; the app aligns video to recording sample zero."""
    starts = marker_onsets(raw, VIDEO_START_MARKER)
    if starts.size == 0:
        raise KeyError(f"{source_name}: no {VIDEO_START_MARKER!r} video-start marker")
    start = float(np.min(starts))
    ends = marker_onsets(raw, VIDEO_END_MARKER)
    later = ends[ends > start]
    if later.size:
        observed = float(np.min(later) - start)
        if abs(observed - expected_duration_s) > VIDEO_DURATION_TOLERANCE_S:
            raise ValueError(
                f"{source_name}: first S4 is {observed:.3f}s after first S3; "
                f"expected about {expected_duration_s:g}s")
    return start


def _read_raw(path: str | Path, *, preload: bool):
    try:
        import mne
    except ImportError as exc:  # pragma: no cover - dependency error is explicit
        raise ImportError(
            "BrainVision input requires MNE-Python; run "
            "'python -m pip install -r requirements.txt'") from exc
    return mne.io.read_raw_brainvision(
        str(Path(path)), preload=preload, verbose="error")


def channel_labels(path: str | Path) -> list[str]:
    return list(_read_raw(path, preload=False).ch_names)


def load(path: str | Path, wanted_events: Iterable[str], *,
         pick_labels: Iterable[str] | None = None,
         target_fs: float | None = None,
         order_path: str | Path | None = None,
         schedule_dir: str | Path | None = None,
         video: str | None = None, mri_config=None):
    """Load selected channels plus reconstructed schedule events.

    Returns ``(data, events, video_onset_s, info)``. Data is in volts, shaped
    ``(samples, channels)``, and follows ``pick_labels`` order when supplied.
    """
    path = Path(path)
    raw = _read_raw(path, preload=False)
    labels = list(raw.ch_names)
    if pick_labels is None:
        indices = list(range(len(labels)))
        selected_labels = labels
    else:
        lut = {re.sub(r"[^a-z0-9]", "", label.lower()): i
               for i, label in enumerate(labels)}
        indices, missing = [], []
        selected_labels = list(pick_labels)
        for label in selected_labels:
            key = re.sub(r"[^a-z0-9]", "", label.lower())
            if key in lut:
                indices.append(lut[key])
            elif str(label).isdigit() and 1 <= int(label) <= len(labels):
                indices.append(int(label) - 1)
            else:
                missing.append(label)
        if missing:
            raise ValueError(f"{path.name}: channels not found: {missing}")
    native_fs = float(raw.info["sfreq"])
    schedule = read_video_schedule(video, schedule_dir, wanted_events)
    # Acquisition is assumed to start synchronously with the video. Do not
    # shift the schedule or require S3/S4, even if unrelated markers exist.
    anchor = 0.0
    session_info = {"session_label": path.stem, "video": video,
                    "video_selection": "explicit", "schedule_format": "blocks",
                    "video_duration_s": schedule.duration_s,
                    "block_cue_s": BLOCK_CUE_S, "repeat_duration_s": REPEAT_DURATION_S,
                    "schedule_sources": [{"path": str(source),
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
                        for source in schedule.source_paths]}
    events = {
        name: (anchor + onsets.copy(), durations.copy())
        for name, (onsets, durations) in schedule.events.items()
    }
    duration_s = raw.n_times / native_fs
    latest_end = max(float(np.max(onsets + durations))
                     for onsets, durations in events.values())
    if latest_end > duration_s + 1.0 / native_fs:
        raise ValueError(
            f"{path.name}: reconstructed schedule ends at {latest_end:.3f}s, "
            f"past recording duration {duration_s:.3f}s")
    mri_info = {"enabled": False}
    actual_labels = [labels[i] for i in indices]
    if len(set(actual_labels)) != len(actual_labels):
        raise ValueError("EMG electrodes must refer to distinct channels")
    if mri_config is not None and mri_config.remove_mri_artifacts:
        from emgcasting.mri import correct_raw
        raw, mri_info = correct_raw(raw, actual_labels,
                                   ecg_channel=mri_config.ecg_channel,
                                   tr_s=mri_config.mri_tr_s)
        indices = list(range(len(actual_labels)))
    data = raw.get_data(picks=indices).T.astype(np.float64, copy=False)
    conditioned_fs = float(raw.info["sfreq"])
    output_fs = conditioned_fs if target_fs is None else float(target_fs)
    if output_fs <= 0:
        raise ValueError("target sampling rate must be positive")
    resampled = abs(conditioned_fs - output_fs) > 1e-6
    if resampled:
        from scipy.signal import resample_poly
        from fractions import Fraction
        ratio = Fraction(output_fs / conditioned_fs).limit_denominator(1000)
        data = resample_poly(data, ratio.numerator, ratio.denominator, axis=0)
    return data, events, anchor, {
        "recording_fs": native_fs,
        "processing_fs": output_fs,
        "resampled": abs(native_fs - output_fs) > 1e-6,
        "resampling_method": ("scipy.signal.resample_poly" if resampled else
                              "mne.filter.resample/polyphase" if mri_info["enabled"] else "none"),
        "channel_labels": selected_labels,
        **session_info,
        "duration_s": duration_s,
        "mri_correction": mri_info,
        "source_header_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "schedule_file": str(schedule.path),
        "schedule_sha256": hashlib.sha256(schedule.path.read_bytes()).hexdigest(),
        "video_start_marker": None,
        "video_timing_basis": "recording_start",
        "video_timing_assumption": "Recording and video start synchronously",
        "video_start_s": anchor,
        "video_start_sample": int(round(anchor * native_fs)),
    }
