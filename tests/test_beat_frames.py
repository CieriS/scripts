"""Tests of the pure functions of audio/beat-frames.py (librosa is not needed)."""

import importlib.util
import pathlib
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "beat_frames", pathlib.Path(__file__).parent.parent / "audio" / "beat-frames.py"
)
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)


class NormalizeBpm(unittest.TestCase):
    def test_half_time_is_doubled(self):
        self.assertEqual(bf.normalize_bpm(75), 150)

    def test_very_slow_reading_doubled_until_threshold(self):
        self.assertEqual(bf.normalize_bpm(40), 160)

    def test_tempo_at_threshold_kept(self):
        self.assertEqual(bf.normalize_bpm(95), 95)

    def test_zero_threshold_disables_doubling(self):
        self.assertEqual(bf.normalize_bpm(75, min_bpm=0), 75)

    def test_zero_tempo_rejected(self):
        with self.assertRaises(ValueError):
            bf.normalize_bpm(0)


class FramesPerBeat(unittest.TestCase):
    def test_120_bpm_at_30_fps_is_15_frames(self):
        self.assertAlmostEqual(bf.frames_per_beat(120, 30), 15)

    def test_invalid_fps_rejected(self):
        with self.assertRaises(ValueError):
            bf.frames_per_beat(120, 0)


class Report(unittest.TestCase):
    def test_lists_beats_in_frames(self):
        text = bf.report(150, 30)
        self.assertIn("30 FPS", text)
        self.assertIn("150.00 BPM", text)
        self.assertIn("12.00 frame (consigliato: 12 frame)", text)
        self.assertIn("24.00 frame (consigliato: 24 frame)", text)
        self.assertIn("6.00 frame (consigliato: 6 frame)", text)

    def test_suggestion_never_below_one_frame(self):
        self.assertIn("consigliato: 1 frame", bf.report(400, 1))


class Main(unittest.TestCase):
    def test_success_uses_detected_tempo(self):
        with mock.patch.object(bf, "detect_bpm", return_value=75.0), \
             mock.patch.object(bf.os.path, "isfile", return_value=True), \
             mock.patch("builtins.print") as out:
            self.assertEqual(bf.main(["song.mp3", "24"]), 0)
        self.assertIn("150.00 BPM", out.call_args.args[0])

    def test_missing_file_exits_1(self):
        with mock.patch.object(bf, "detect_bpm") as detect, mock.patch("builtins.print"):
            self.assertEqual(bf.main(["/no/such/song.mp3"]), 1)
        detect.assert_not_called()

    def test_no_tempo_exits_1(self):
        with mock.patch.object(bf, "detect_bpm", return_value=0.0), \
             mock.patch.object(bf.os.path, "isfile", return_value=True), \
             mock.patch("builtins.print"):
            self.assertEqual(bf.main(["silence.mp3"]), 1)


if __name__ == "__main__":
    unittest.main()
