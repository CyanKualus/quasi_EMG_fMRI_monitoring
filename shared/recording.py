"""Record one continuous, unprocessed RDA segment as BrainVision files."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import socket
import time
from uuid import uuid4

import numpy as np

from shared.rda import BlockSequence, RDAError, parse_data, parse_start, receive_packet


def _field(value):
    """Escape BrainVision's comma separator; reject multiline metadata."""
    if any(char in value for char in "\r\n\0"):
        raise ValueError("Stream labels and markers must not contain line breaks or NULs")
    return value.replace(",", r"\1")


class BrainVisionWriter:
    """Append calibrated samples and sample-locked markers without buffering a run."""

    def __init__(self, directory, info):
        self.info = info
        self.samples = 0
        self.markers = 0
        self._data = self._markers = None
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"recording_{datetime.now():%Y%m%d_%H%M%S_%f}_{uuid4().hex[:8]}"
        self.path = directory / f"{stem}.vhdr"
        channels = "\n".join(
            f"Ch{i}={_field(name)},,{resolution:.17g},µV"
            for i, (name, resolution) in enumerate(zip(info.names, info.resolutions_uv), 1))
        try:
            # Exclusive creation prevents an existing recording from being replaced.
            self._data = self.path.with_suffix(".eeg").open("xb")
            self._markers = self.path.with_suffix(".vmrk").open(
                "x", encoding="utf-8", newline="\n")
            self._markers.write(
                "Brain Vision Data Exchange Marker File, Version 1.0\n\n"
                "[Common Infos]\nCodepage=UTF-8\n"
                f"DataFile={stem}.eeg\n\n[Marker Infos]\n")
            with self.path.open("x", encoding="utf-8", newline="\n") as header:
                header.write(
                    "Brain Vision Data Exchange Header File Version 1.0\n\n"
                    "[Common Infos]\nCodepage=UTF-8\n"
                    f"DataFile={stem}.eeg\nMarkerFile={stem}.vmrk\n"
                    "DataFormat=BINARY\nDataOrientation=MULTIPLEXED\n"
                    f"NumberOfChannels={len(info.names)}\n"
                    f"SamplingInterval={1e6 / info.sfreq:.17g}\n\n"
                    "[Binary Infos]\nBinaryFormat=IEEE_FLOAT_32\n\n"
                    f"[Channel Infos]\n{channels}\n")
            self._markers.flush()
        except Exception:
            self.close()
            raise

    def append(self, block):
        points = block.volts.shape[1]
        # Undo only the decoder's calibration to retain the original wire
        # values and channel resolutions. MNE reapplies them when reading.
        with np.errstate(over="ignore"):
            values = np.asarray(
                block.volts.T / (self.info.resolutions_uv * 1e-6), dtype="<f4")
        if not np.all(np.isfinite(values)):
            raise RDAError("Samples exceed BrainVision float32 range")
        lines = []
        for index, marker in enumerate(block.markers, self.markers + 1):
            if not -1 <= marker.channel < len(self.info.names):
                raise RDAError("Invalid RDA marker channel")
            # RDA positions/channels are zero based; BrainVision is one based,
            # with channel 0 meaning all channels (RDA uses -1).
            lines.append(
                f"Mk{index}={_field(marker.kind)},{_field(marker.description)},"
                f"{self.samples + marker.position + 1},{marker.points},"
                f"{marker.channel + 1}\n")
        self._data.write(values.tobytes(order="C"))
        self._markers.writelines(lines)
        self._data.flush()
        self._markers.flush()
        self.samples += points
        self.markers += len(lines)

    def close(self):
        failure = None
        for handle in (self._data, self._markers):
            if handle is not None and not handle.closed:
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                except OSError as exc:
                    failure = exc
                try:
                    handle.close()
                except OSError as exc:
                    failure = exc
        if failure is not None:
            raise failure


class RDARecorder:
    """One connection and one file; errors preserve the received prefix on disk."""

    def __init__(self, host, port, directory):
        self.host, self.port, self.directory = host, port, directory
        self.path = None
        self.samples = 0
        self.sfreq = 0.
        self.error = ""
        self.ready = False
        self.message = f"Connecting to {host}:{port}…"

    def run(self, cancelled=lambda: False):
        writer = None
        reason = "Recording stopped"
        try:
            with socket.create_connection((self.host, self.port), timeout=3) as sock:
                sock.settimeout(.2)
                self.message = "Connected. Waiting for BrainVision Recorder monitoring…"
                info = None
                sequence = BlockSequence()
                last_samples = time.monotonic()
                while not cancelled():
                    try:
                        kind, body = receive_packet(sock, cancelled)
                    except InterruptedError:
                        if cancelled():
                            break
                        raise
                    if kind == 1:
                        if writer is not None:
                            raise RDAError("Recorder restarted the stream during recording")
                        info = parse_start(body)
                        writer = BrainVisionWriter(self.directory, info)
                        self.path = writer.path
                        self.sfreq = info.sfreq
                        last_samples = time.monotonic()
                        self.message = "Connected. Waiting for samples…"
                    elif kind in (2, 4):
                        if info is None:
                            raise RDAError("RDA data arrived without a start header")
                        block = parse_data(body, kind, info)
                        sequence.check(block.number)
                        writer.append(block)
                        self.samples = writer.samples
                        last_samples = time.monotonic()
                        self.message = (
                            f"Recording: {self.samples / self.sfreq:.1f} s · "
                            f"{len(info.names)} channels · {self.sfreq:g} Hz\n"
                            f"Saving to {self.path}")
                    elif kind in (3, 6):
                        reason = "Recorder stopped the stream"
                        break
                    elif kind not in (5, 7, 8, 9, 10000, 100000):
                        raise RDAError(
                            f"Unsupported RDA message type: {kind} (payload {len(body)} bytes)")
                    if time.monotonic() - last_samples > 10:
                        raise TimeoutError("Recorder sent no samples for 10 seconds")
        except Exception as exc:
            self.error = str(exc)
        finally:
            if writer is not None:
                try:
                    writer.close()
                except Exception as exc:
                    self.error = f"Could not finalize the recording: {exc}"
            self.ready = bool(self.samples and not self.error)
            if self.error:
                self.message = f"Recording failed: {self.error}."
                if self.path is not None:
                    self.message += f"\nPartial recording retained at {self.path}; it is not ready for processing."
            elif self.ready:
                self.message = (
                    f"{reason}: {self.samples / self.sfreq:.1f} s saved to {self.path}.\n"
                    "Select the video, then press Process file.")
            else:
                self.message = "Recording stopped without any samples. Start recording to try again."
