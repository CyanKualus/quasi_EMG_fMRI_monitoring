"""Regression coverage for MRI conditioning at the target sample rate."""
import unittest
from unittest.mock import patch

import mne
import numpy as np

from emgcasting import mri


class MRIResamplingTests(unittest.TestCase):
    def test_conditioning_at_and_above_250_hz(self):
        for fs in (250.0, 1000.0):
            with self.subTest(fs=fs):
                times = np.arange(int(20 * fs)) / fs
                samples = np.asarray([
                    np.sin(2 * np.pi * 30 * times),
                    np.sin(2 * np.pi * 1 * times),
                ]) * 1e-5
                source = mne.io.RawArray(
                    samples, mne.create_info(["EMG", "ECG"], fs, ["emg", "ecg"]),
                    verbose="error")
                source.set_annotations(mne.Annotations([2.0], [0.0], ["S 3"]))
                # Isolate conditioning from artifact fitting and QRS detection.
                # Keep the real MNE resampler: its 1:1 filter caused the crash.
                with (
                    patch.object(mri, "gradient_channel",
                                 side_effect=lambda x, *_: (x.copy(), len(x))),
                    patch.object(mri, "detect_qrs",
                                 return_value=(np.arange(1.0, 19.0), {})) as qrs,
                    patch.object(mne.filter, "resample",
                                 wraps=mne.filter.resample) as resample,
                    patch.object(mne.io.BaseRaw, "filter", autospec=True),
                    patch.object(mne.io.BaseRaw, "notch_filter", autospec=True),
                    patch.object(mne.preprocessing, "apply_pca_obs",
                                 side_effect=lambda raw, **_: raw),
                    self.assertWarns(UserWarning),
                ):
                    result, info = mri.correct_raw(source, ["EMG"])

                self.assertEqual(result.info["sfreq"], 250.0)
                self.assertEqual(result.n_times, 5000)
                self.assertEqual(info["native_fs"], fs)
                self.assertEqual(result.annotations.onset[
                    list(result.annotations.description).index("S 3")], 2.0)
                if fs == 250.0:
                    resample.assert_not_called()
                    np.testing.assert_array_equal(result.get_data()[0], samples[0])
                    np.testing.assert_array_equal(qrs.call_args.args[0], samples[1])
                else:
                    self.assertEqual(resample.call_count, 2)
                    self.assertEqual(resample.call_args.kwargs["down"], 4.0)
                    self.assertEqual(len(qrs.call_args.args[0]), 5000)


if __name__ == "__main__":
    unittest.main()
