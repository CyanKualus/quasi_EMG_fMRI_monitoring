"""Compare block-derived timing with independent stimulus event-table exports."""
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

from emgcasting.core import ProcessingConfig
from shared.brainvision import VIDEO_OPTIONS, read_video_schedule, video_start


class VideoScheduleTests(unittest.TestCase):
    def test_all_six_match_stimulus_event_tables(self):
        expected = json.loads((Path(__file__).parent / "fixtures/video_events.json").read_text(encoding="utf-8"))
        self.assertEqual(set(expected), set(VIDEO_OPTIONS))
        for video in VIDEO_OPTIONS:
            with self.subTest(video=video):
                schedule = read_video_schedule(video)
                self.assertEqual(schedule.duration_s, 334)
                self.assertEqual(set(schedule.events), set(expected[video]["events"]))
                for name, values in expected[video]["events"].items():
                    np.testing.assert_array_equal(np.asarray(schedule.events[name]).T, values)
                self.assertEqual(len(schedule.source_paths), 2)
                self.assertTrue(all(p.name.endswith("blocks.csv") for p in schedule.source_paths))

    def test_no_implicit_old_schedule(self):
        for video in (None, 1, 2, 3, "Video1", "Video4R"):
            with self.subTest(video=video), self.assertRaises(ValueError):
                read_video_schedule(video)
        with self.assertRaisesRegex(ValueError, "explicitly"):
            ProcessingConfig(".", ["recording.vhdr"], file_type="brainvision").validate()

    def test_right_only_requires_right_channels(self):
        with self.assertRaisesRegex(ValueError, "right-hand channels"):
            ProcessingConfig(".", ["recording.vhdr"], file_type="brainvision",
                             video="Video1R", right_channels=None).validate()

    def test_companion_mismatch_is_rejected(self):
        schedule = read_video_schedule("Video1LR")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for path in schedule.source_paths:
                shutil.copy2(path, root / path.name)
            path = root / schedule.path.name
            path.write_text(path.read_text().replace("1,14,18,right,4", "1,15,18,right,4"))
            with self.assertRaisesRegex(ValueError, "disagree"):
                read_video_schedule("Video1LR", root)

    def test_end_marker_uses_selected_schedule_duration(self):
        raw = SimpleNamespace(annotations=SimpleNamespace(
            description=["Stimulus/S 3", "Stimulus/S 4"], onset=[3., 337.]))
        self.assertEqual(video_start(raw, expected_duration_s=334), 3.)
        with self.assertRaisesRegex(ValueError, "expected about 300s"):
            video_start(raw, expected_duration_s=300)


if __name__ == "__main__":
    unittest.main()
