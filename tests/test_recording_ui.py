"""Exercise recording/processing gates with real Qt workers and a TCP stream."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets

from app import QuasiEMGApp
from shared.file_ready import snapshot
from test_recording import data_packet, packet, replay, start_packet


class RecordingUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.settings = QtCore.QSettings(str(self.root / "settings.ini"), QtCore.QSettings.Format.IniFormat)
        # A previous run's video must not silently carry over.
        self.settings.setValue("video", 3)
        self.window = QuasiEMGApp(self.settings)
        self.paths = patch("app.APP_DIR", self.root)
        self.paths.start()

    def tearDown(self):
        self.window.stop_recording()
        self.until(lambda: self.window.recording_worker is None)
        self.window.close()
        self.qt.processEvents()
        self.paths.stop()
        self.directory.cleanup()

    def until(self, predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.qt.processEvents()
            if predicate():
                return
            time.sleep(.01)
        self.fail("GUI did not reach the expected state")

    def test_record_stop_choose_video_process_and_record_again(self):
        window = self.window
        self.assertTrue(window.btn_record.isEnabled())
        self.assertFalse(window.btn_stop.isEnabled())
        self.assertFalse(window.btn_start.isEnabled())
        self.assertFalse(window.cmb_video.isEnabled())
        self.assertIsNone(window.cmb_video.currentData())
        self.assertEqual([window.cmb_video.itemText(i) for i in range(1, window.cmb_video.count())],
                         ["Video1R", "Video2R", "Video3R", "Video1LR", "Video2LR", "Video3LR"])
        self.assertFalse(hasattr(window, "ed_filename"))
        self.assertFalse(hasattr(window, "ed_participant"))
        window.show()
        self.qt.processEvents()
        self.assertEqual(window.btn_record.y(), window.btn_stop.y())
        self.assertLess(window.btn_stop.y(), window.btn_start.y())

        frames = [start_packet(), data_packet(1, [[1, 2, 3], [4, 5, 6]])]
        with replay(frames, hold=True) as port:
            window.cmb_port.setCurrentText(str(port))
            self.assertEqual(window.online_tab.port.currentText(), str(port))
            window.btn_record.click()
            self.until(lambda: window.recording_worker.recorder.samples == 2)
            self.assertFalse(window.btn_record.isEnabled())
            self.assertTrue(window.btn_stop.isEnabled())
            self.assertFalse(window.btn_start.isEnabled())
            self.assertFalse(window.cmb_video.isEnabled())
            self.assertFalse(window.online_tab.can_start())
            window.btn_stop.click()
            self.assertFalse(window.btn_stop.isEnabled())
            self.until(lambda: window.recording_worker is None)

        path = window.recorded_path
        self.assertEqual(path.parent, self.root / "data")
        snapshot(path)
        self.assertTrue(window.cmb_video.isEnabled())
        self.assertFalse(window.btn_start.isEnabled())
        window.cmb_video.setCurrentIndex(2)
        self.assertTrue(window.btn_start.isEnabled())
        self.assertEqual(window._config().video, "Video2R")
        self.assertFalse(window.ed_left.isEnabled())
        previous_left = window.ed_left.text()
        window.cmb_video.setCurrentIndex(5)
        self.assertTrue(window.ed_left.isEnabled())
        self.assertEqual(window.ed_left.text(), previous_left)
        self.assertEqual(window._config().video, "Video2LR")
        self.assertEqual(window._config().recordings, [path.name])
        with patch.object(window.emg_tab, "start", return_value=True) as process:
            window.btn_start.click()
            process.assert_called_once_with()
            self.assertIs(window.tabs.currentWidget(), window.emg_tab)

        # Starting the next capture resets selection and leaves the old backup intact.
        with replay([start_packet(), packet(3)]) as port:
            window.cmb_port.setCurrentText(str(port))
            window.start_recording()
            self.assertIsNone(window.cmb_video.currentData())
            self.assertIsNone(window.recorded_path)
            self.until(lambda: window.recording_worker is None)
        self.assertTrue(path.is_file())
        self.assertFalse(window.btn_start.isEnabled())

    def test_failure_leaves_processing_disabled_and_allows_retry(self):
        with replay([start_packet(), data_packet(1, [[1, 2, 3]])]) as port:
            self.window.cmb_port.setCurrentText(str(port))
            self.window.start_recording()
            self.until(lambda: self.window.recording_worker is None)
        self.assertIsNone(self.window.recorded_path)
        self.assertFalse(self.window.btn_start.isEnabled())
        self.assertFalse(self.window.cmb_video.isEnabled())
        self.assertTrue(self.window.btn_record.isEnabled())
        self.assertIn("Partial recording retained", self.window.lbl_status.text())

    def test_close_during_recording_waits_for_files_to_finalize(self):
        self.window.show()
        with replay([start_packet(), data_packet(1, [[1, 2, 3]])], hold=True) as port:
            self.window.cmb_port.setCurrentText(str(port))
            self.window.start_recording()
            self.until(lambda: self.window.recording_worker.recorder.samples == 1)
            self.window.close()
            self.until(lambda: self.window.recording_worker is None and not self.window.isVisible())
        snapshot(self.window.recorded_path)


if __name__ == "__main__":
    unittest.main()
