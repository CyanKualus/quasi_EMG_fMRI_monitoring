"""TCP replay, BrainVision read-back, and recording-to-analysis regression checks."""
from contextlib import contextmanager
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import mne
import numpy as np

from emgcasting.single_file import analyze_file
from settings import file_config
from shared.brainvision import video_start
from shared.file_ready import snapshot
from shared.rda import GUID, HEADER
from shared.recording import RDARecorder


def packet(kind, body=b""):
    return HEADER.pack(GUID, HEADER.size + len(body), kind) + body


def start_packet(names=("F1", "F2", "ECG"), resolutions=(.1, .5, 2.), fs=5000.):
    body = struct.pack("<Id", len(names), 1e6 / fs)
    body += np.asarray(resolutions, dtype="<f8").tobytes()
    body += b"".join(name.encode("utf-8") + b"\0" for name in names)
    return packet(1, body)


def data_packet(number, data, kind=4, markers=()):
    data = np.asarray(data, dtype="<i2" if kind == 2 else "<f4")
    body = struct.pack("<III", number, len(data), len(markers)) + data.tobytes()
    for position, length, channel, marker_kind, description in markers:
        texts = marker_kind.encode("utf-8") + b"\0" + description.encode("utf-8") + b"\0"
        body += struct.pack("<IIIi", 16 + len(texts), position, length, channel) + texts
    return packet(kind, body)


@contextmanager
def replay(frames, *, hold=False, fragment=False):
    """Use a real loopback socket; hold lets the client exercise Stop mid-frame."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(5)
    release = threading.Event()
    failures = []

    def send():
        try:
            with server.accept()[0] as client:
                for frame in frames:
                    if fragment:
                        for offset in range(0, len(frame), 7):
                            client.sendall(frame[offset:offset + 7])
                    else:
                        client.sendall(frame)
                if hold:
                    release.wait(5)
        except (BrokenPipeError, ConnectionResetError):
            pass  # The client can reject a stream before the server finishes.
        except Exception as exc:
            failures.append(exc)

    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    try:
        yield server.getsockname()[1]
    finally:
        release.set()
        thread.join(6)
        server.close()
        if thread.is_alive():
            raise AssertionError("Replay server did not stop")
        if failures:
            raise failures[0]


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def record(self, frames, **options):
        with replay(frames, **options) as port:
            recorder = RDARecorder("127.0.0.1", port, self.directory.name)
            recorder.run()
        return recorder

    def test_float_and_integer_round_trip_all_channels_and_markers(self):
        for kind in (2, 4):
            with self.subTest(kind=kind):
                values = np.array([[1, -2, 3], [4, 5, -6], [-7, 8, 9], [10, -11, 12]])
                if kind == 4:
                    values = values + .125
                names = ("F1", "F2", "ECG,é")
                recorder = self.record([
                    start_packet(names=names),
                    packet(100000, b"x" * 102),
                    data_packet(2**32 - 1, values[:2], kind),
                    packet(10000), packet(5, b"state"),
                    data_packet(0, values[2:], kind, (
                        (0, 1, -1, "Stimulus", "S  3"),
                        (1, 1, 2, "Comment", "note,é"))),
                    packet(3)], fragment=True)
                self.assertTrue(recorder.ready, recorder.message)
                self.assertEqual(len(snapshot(recorder.path)), 3)
                raw = mne.io.read_raw_brainvision(recorder.path, preload=True, verbose="error")
                self.assertEqual(raw.ch_names, list(names))
                self.assertEqual(raw.info["sfreq"], 5000.)
                self.assertEqual(raw.n_times, 4)
                expected = values.T * np.array([.1, .5, 2.])[:, None] * 1e-6
                np.testing.assert_allclose(raw.get_data(), expected, rtol=1e-12, atol=0)
                # Integer and float wire values are both preserved exactly.
                np.testing.assert_array_equal(
                    np.fromfile(recorder.path.with_suffix(".eeg"), dtype="<f4").reshape(-1, 3), values)
                self.assertAlmostEqual(video_start(raw), 2 / 5000.)
                self.assertIn("Comment/note,é", raw.annotations.description)
                markers = recorder.path.with_suffix(".vmrk").read_text(encoding="utf-8")
                self.assertIn("Mk1=Stimulus,S  3,3,1,0", markers)
                self.assertIn(r"Mk2=Comment,note\1é,4,1,3", markers)

    def test_discontinuities_disconnects_and_restarts_keep_prefix_but_block_processing(self):
        first = data_packet(10, [[1, 2, 3]])
        for bad_tail in ([], [data_packet(12, [[4, 5, 6]])],
                         [data_packet(10, [[4, 5, 6]])], [start_packet()],
                         [data_packet(11, [[float("nan"), 2, 3]])], [packet(99)]):
            with self.subTest(tail=bad_tail):
                recorder = self.record([start_packet(), first, *bad_tail])
                self.assertFalse(recorder.ready)
                self.assertTrue(recorder.error)
                self.assertEqual(recorder.samples, 1)
                self.assertIn("Partial recording retained", recorder.message)
                self.assertEqual(len(snapshot(recorder.path)), 3)
                self.assertEqual(recorder.path.with_suffix(".eeg").stat().st_size, 12)

    def test_stop_during_partial_frame_keeps_only_complete_blocks(self):
        frames = [start_packet(), data_packet(1, [[1, 2, 3]]), data_packet(2, [[4, 5, 6]])[:29]]
        with replay(frames, hold=True) as port:
            recorder = RDARecorder("127.0.0.1", port, self.directory.name)
            stop = threading.Event()
            thread = threading.Thread(target=recorder.run, args=(stop.is_set,))
            thread.start()
            try:
                deadline = time.monotonic() + 3
                while not recorder.samples and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(recorder.samples, 1)
            finally:
                stop.set()
                thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertTrue(recorder.ready, recorder.message)
            self.assertEqual(recorder.path.with_suffix(".eeg").stat().st_size, 12)
            snapshot(recorder.path)  # All files have closed before readiness.

    def test_empty_stream_and_finalization_failure_do_not_become_ready(self):
        recorder = self.record([start_packet(), packet(3)])
        self.assertFalse(recorder.ready)
        self.assertIn("without any samples", recorder.message)
        with patch("shared.recording.os.fsync", side_effect=OSError("disk failed")):
            recorder = self.record([start_packet(), data_packet(1, [[1, 2, 3]]), packet(3)])
        self.assertFalse(recorder.ready)
        self.assertIn("Could not finalize", recorder.error)
        snapshot(recorder.path)

    def test_recorded_file_runs_through_existing_emg_analysis(self):
        fs = 250.
        rng = np.random.default_rng(42)
        values = rng.normal(0, 10, (int(345 * fs), 3))
        frames = [start_packet(fs=fs)]
        for number, offset in enumerate(range(0, len(values), 1000)):
            markers = ((250, 1, -1, "Stimulus", "S  3"),) if number == 0 else ()
            frames.append(data_packet(number, values[offset:offset + 1000], markers=markers))
        frames.append(packet(3))
        recorder = self.record(frames)
        self.assertTrue(recorder.ready, recorder.message)
        batch = analyze_file(file_config(recorder.path, video="Video1R",
            overrides={"left_channels": ("absent1", "absent2")}))
        self.assertEqual(len(batch.recordings), 1)
        self.assertEqual(batch.recordings[0].recording, recorder.path.stem)
        self.assertEqual(len(batch.recordings[0].hands), 1)
        self.assertEqual(batch.recordings[0].hands[0].n_trials, 40)
        self.assertEqual(batch.recordings[0].provenance["video"], "Video1R")
        self.assertEqual(batch.recordings[0].provenance["video_start_sample"], 250)
        batch_lr = analyze_file(file_config(recorder.path, video="Video2LR",
            overrides={"left_channels": ("F1", "F2")}))
        self.assertEqual([hand.n_trials for hand in batch_lr.recordings[0].hands], [20, 20])
        self.assertEqual(len(batch_lr.recordings[0].provenance["schedule_sources"]), 2)


if __name__ == "__main__":
    unittest.main()
