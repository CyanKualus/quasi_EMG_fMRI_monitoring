"""Record a raw BrainVision stream, then process one completed recording."""
from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
from pathlib import Path

# Windowed executables have no console streams; MNE and other scientific
# libraries still expect file-like objects when initializing their loggers.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from PyQt6 import QtCore, QtGui, QtWidgets

from emgcasting.core import parse_optional_pair
from emg_view import EMGTab
from online_view import OnlineTab
from settings import file_config, load_settings
from shared import theme
from shared.brainvision import VIDEO_OPTIONS
from shared.app_paths import APP_DIR
from shared.recording import RDARecorder


class RecordingWorker(QtCore.QThread):
    def __init__(self, recorder, parent=None):
        super().__init__(parent)
        self.recorder = recorder

    def run(self):
        self.recorder.run(self.isInterruptionRequested)


class QuasiEMGApp(QtWidgets.QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else QtCore.QSettings(
            "NeuroCasting", "quasi_EMG_processing")
        self.setWindowTitle("quasi_EMG_processing")
        self.resize(1120, 780)
        self._monitor_geometry = None
        self._monitor_window_state = QtCore.Qt.WindowState.WindowNoState
        self.recording_worker = None
        self.recorded_path = None
        self._closing = False
        theme.apply(self)
        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.start_tab = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(self.start_tab)
        outer.setContentsMargins(28, 24, 28, 24)
        title = QtWidgets.QLabel("Quasi-fMRI · hand EMG")
        title.setStyleSheet("font-size: 23px; font-weight: 600;")
        outer.addWidget(title)
        intro = QtWidgets.QLabel(
            "Record the raw stream, stop recording, then choose a video and process the saved file.")
        intro.setWordWrap(True)
        outer.addWidget(intro)
        form = QtWidgets.QFormLayout()
        form.setVerticalSpacing(16)
        self.ed_host = QtWidgets.QLineEdit("127.0.0.1")
        self.ed_host.setToolTip("Address of the computer running BrainVision Recorder with RDA enabled")
        form.addRow("Recorder host", self.ed_host)
        self.cmb_port = QtWidgets.QComboBox()
        self.cmb_port.setEditable(True)
        self.cmb_port.addItems(["51244", "51234"])
        self.cmb_port.setToolTip("51244 = float32; 51234 = signed int16. Shared with Online Monitoring.")
        form.addRow("RDA port", self.cmb_port)
        self.cmb_video = QtWidgets.QComboBox()
        self.cmb_video.addItem("Select video after recording…", None)
        for video in VIDEO_OPTIONS:
            self.cmb_video.addItem(video, video)
        self.cmb_video.setToolTip(
            "After stopping, select the presented video. R = right hand only; LR = both hands. "
            "Each 4-second repeat is scored using block timing anchored to the first S3 marker.")
        self.cmb_video.currentIndexChanged.connect(self._refresh_busy)
        form.addRow("video schedule", self.cmb_video)
        self.lbl_video = QtWidgets.QLabel("R: right hand only. LR: left and right hands. Each repeat is scored separately.")
        self.lbl_video.setWordWrap(True)
        form.addRow("", self.lbl_video)
        defaults = load_settings()
        self.ed_left = QtWidgets.QLineEdit(
            ", ".join(defaults.get("left_channels") or ()))
        self.ed_right = QtWidgets.QLineEdit(
            ", ".join(defaults.get("right_channels") or ()))
        self.ed_left.setPlaceholderText("Leave blank to disable")
        self.ed_left.setToolTip("Used for LR videos; ignored for right-only R videos.")
        form.addRow("left hand EMG (+, −)", self.ed_left)
        form.addRow("right hand EMG (+, −)", self.ed_right)
        self.chk_mri = QtWidgets.QCheckBox("Remove MRI artifacts using MNE (AAS + cardiac OBS)")
        self.chk_mri.setToolTip(
            "Use on uncorrected BrainVision recordings. Requires an ECG channel. "
            "Applies the full pipeline from the reviewed EEG/EMG report before EMG analysis.")
        form.addRow("", self.chk_mri)
        self.lbl_mri = QtWidgets.QLabel()
        self.lbl_mri.setWordWrap(True)
        form.addRow("", self.lbl_mri)
        outer.addLayout(form)
        outer.addStretch()
        self.lbl_status = QtWidgets.QLabel("Ready to record. Enable RDA and monitoring in BrainVision Recorder.")
        self.lbl_status.setWordWrap(True)
        outer.addWidget(self.lbl_status)
        recording_buttons = QtWidgets.QHBoxLayout()
        self.btn_record = QtWidgets.QPushButton("Start recording")
        self.btn_record.clicked.connect(self.start_recording)
        self.btn_stop = QtWidgets.QPushButton("Stop recording")
        self.btn_stop.clicked.connect(self.stop_recording)
        recording_buttons.addWidget(self.btn_record)
        recording_buttons.addWidget(self.btn_stop)
        outer.addLayout(recording_buttons)
        self.btn_start = QtWidgets.QPushButton("Process file")
        self.btn_start.setObjectName("primaryButton")
        self.btn_start.clicked.connect(self.on_start)
        outer.addWidget(self.btn_start)
        self.tabs.addTab(self.start_tab, "Start")
        self.emg_tab = EMGTab(self._config)
        self.tabs.addTab(self.emg_tab, "EMG Analysis")
        self.online_tab = OnlineTab(
            self.settings, can_start=lambda: not self._busy() and self.recording_worker is None)
        self.tabs.addTab(self.online_tab, "Online Monitoring")
        self.ed_host.setText(self.online_tab.host.text())
        self.cmb_port.setCurrentText(self.online_tab.port.currentText())
        self.ed_host.textChanged.connect(self.online_tab.host.setText)
        self.online_tab.host.textChanged.connect(self.ed_host.setText)
        self.cmb_port.currentTextChanged.connect(self.online_tab.port.setCurrentText)
        self.online_tab.port.currentTextChanged.connect(self.cmb_port.setCurrentText)
        self.tabs.currentChanged.connect(self._tab_changed)
        self._exit_full_screen = QtGui.QShortcut(QtGui.QKeySequence("Escape"), self)
        self._exit_full_screen.activated.connect(self._leave_monitor_full_screen)
        self.tabs.setTabToolTip(self.tabs.indexOf(self.online_tab), "Opens full screen; press Esc to return to the window")
        self.emg_tab.status_changed.connect(self.lbl_status.setText)
        self.emg_tab.analysis_finished.connect(self._analysis_finished)
        self._busy_timer = QtCore.QTimer(self)
        self._busy_timer.setInterval(150)
        self._busy_timer.timeout.connect(self._refresh_busy)
        self._busy_timer.start()
        self.chk_mri.toggled.connect(self._mri_changed)
        self._restore()
        self._refresh_busy()

    def _config(self):
        if self.recorded_path is None or self.recording_worker is not None:
            raise ValueError("Stop a recording before processing")
        if self.cmb_video.currentData() is None:
            raise ValueError("Select Video 1, 2 or 3 before processing")
        return file_config(
            self.recorded_path,
            mri=self.chk_mri.isChecked(), video=self.cmb_video.currentData(),
            overrides={"left_channels": parse_optional_pair(self.ed_left.text()),
                       "right_channels": parse_optional_pair(self.ed_right.text())})

    def _tab_changed(self, _index):
        if self.tabs.currentWidget() is self.online_tab:
            if not self.isFullScreen():
                self._monitor_geometry = self.saveGeometry()
                self._monitor_window_state = self.windowState() & ~QtCore.Qt.WindowState.WindowMinimized
                self.showFullScreen()
        elif self._monitor_geometry is not None:
            self._leave_monitor_full_screen()

    def _leave_monitor_full_screen(self):
        if self._monitor_geometry is not None:
            geometry = self._monitor_geometry
            self._monitor_geometry = None
            self.restoreGeometry(geometry)
            self.setWindowState(self._monitor_window_state)
        elif self.isFullScreen():
            self.showNormal()

    def start_recording(self):
        if self._busy() or self.online_tab.is_running() or self.recording_worker is not None:
            return
        try:
            port = int(self.cmb_port.currentText())
            host = self.ed_host.text().strip()
            if not host or not 1 <= port <= 65535:
                raise ValueError("Enter a Recorder host and a port between 1 and 65535")
        except ValueError:
            self.lbl_status.setText("Enter a Recorder host and a port between 1 and 65535.")
            return
        self._save_settings()
        self.recorded_path = None
        self.cmb_video.setCurrentIndex(0)
        self.emg_tab.skip("Recording in progress. Stop recording, select a video, then press Process file.")
        recorder = RDARecorder(host, port, APP_DIR / "data")
        self.recording_worker = RecordingWorker(recorder, self)
        self.recording_worker.finished.connect(self._recording_finished)
        self.recording_worker.start()
        self._refresh_busy()

    def stop_recording(self):
        if self.recording_worker is not None:
            self.recording_worker.requestInterruption()
            self._refresh_busy()

    def _recording_finished(self):
        worker = self.recording_worker
        recorder = worker.recorder
        # The worker closes and flushes all companion files before enabling analysis.
        self.recorded_path = recorder.path if recorder.ready else None
        self.lbl_status.setText(recorder.message)
        self.recording_worker = None
        worker.deleteLater()
        self._refresh_busy()
        if self._closing:
            QtCore.QTimer.singleShot(0, self.close)

    def _mri_changed(self):
        self.lbl_mri.setVisible(self.chk_mri.isChecked())
        self.lbl_mri.setText(
            "Full correction: native-rate AAS → 250 Hz conditioning → cardiac OBS → EMG. "
            "Experimental for EMG: correction can introduce small burst copies and alter rest activity."
            if self.chk_mri.isChecked() else "")

    def _busy(self):
        return any(worker is not None and worker.isRunning()
                   for worker in (self.emg_tab.worker, self.emg_tab.saver))

    def _refresh_busy(self):
        busy = self._busy()
        monitoring = self.online_tab.is_running()
        recording = self.recording_worker is not None
        idle = not busy and not monitoring and not recording
        self.btn_record.setEnabled(idle)
        self.btn_stop.setEnabled(recording and not self.recording_worker.isInterruptionRequested())
        self.btn_start.setEnabled(idle and self.recorded_path is not None
                                  and self.cmb_video.currentData() is not None)
        self.cmb_video.setEnabled(idle and self.recorded_path is not None)
        for widget in (self.ed_host, self.cmb_port, self.ed_left, self.ed_right):
            widget.setEnabled(idle)
        self.ed_left.setEnabled(idle and self.cmb_video.currentData() not in (
            "Video1R", "Video2R", "Video3R"))
        self.chk_mri.setEnabled(idle and (
            self.recorded_path is None or self.recorded_path.suffix.lower() != ".xdf"))
        self.online_tab.connect_button.setEnabled(idle)
        self.online_tab.connection_box.setEnabled(idle)
        self.emg_tab.btn_save.setEnabled(self.emg_tab.batch is not None and idle)
        if recording:
            self.lbl_status.setText(
                "Stopping recording and finalizing files…"
                if self.recording_worker.isInterruptionRequested()
                else self.recording_worker.recorder.message)

    def on_start(self):
        if self._busy() or self.online_tab.is_running() or self.recording_worker is not None:
            return
        self._save_settings()
        if self.emg_tab.start():
            self._refresh_busy()
            self.tabs.setCurrentWidget(self.emg_tab)

    def _analysis_finished(self, success):
        if success:
            self.tabs.setCurrentWidget(self.emg_tab)

    def _restore(self):
        for key, widget in self._text_fields().items():
            widget.setText(self.settings.value(key, widget.text(), type=str))
        self.chk_mri.setChecked(self.settings.value("mri", False, type=bool))
        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def _text_fields(self):
        return {"left_channels": self.ed_left, "right_channels": self.ed_right}

    def _save_settings(self):
        self.online_tab.save_settings()
        for key, widget in self._text_fields().items():
            self.settings.setValue(key, widget.text())
        self.settings.setValue("mri", self.chk_mri.isChecked())
        self.settings.setValue("geometry", self._monitor_geometry
                               if self._monitor_geometry is not None else self.saveGeometry())
        self.settings.sync()

    def closeEvent(self, event):
        if self.recording_worker is not None:
            self._closing = True
            self.stop_recording()
            event.ignore()
            return
        if self.online_tab.is_running():
            self.online_tab.stop()
            event.ignore()
            QtCore.QTimer.singleShot(200, self.close)
            return
        if self._busy():
            self.lbl_status.setText("Processing or saving is still running. Close when it finishes.")
            if self.emg_tab.worker is not None:
                self.emg_tab.worker.requestInterruption()
            event.ignore()
            return
        self._save_settings()
        super().closeEvent(event)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filename", nargs="?")
    parser.add_argument("--mne-mri", action="store_true")
    parser.add_argument("--process", action="store_true", help="Process the supplied file at launch")
    parser.add_argument("--video", choices=VIDEO_OPTIONS)
    args = parser.parse_args(argv)
    qt = QtWidgets.QApplication(sys.argv[:1])
    window = QuasiEMGApp()
    if args.filename:
        window.recorded_path = Path(args.filename).resolve()
        window.lbl_status.setText(f"Ready to process {window.recorded_path}. Select the video schedule.")
        window.chk_mri.setChecked(args.mne_mri and window.recorded_path.suffix.lower() != ".xdf")
        window.cmb_video.setCurrentIndex(window.cmb_video.findData(args.video))
        window._refresh_busy()
    window.show()
    if args.process:
        QtCore.QTimer.singleShot(0, window.on_start)
    return qt.exec()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
