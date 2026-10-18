"""Tests of media/sort-media.py with a fake classifier: torch, CLIP and Pillow are not needed."""

import csv
import importlib.util
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "sort_media", pathlib.Path(__file__).parent.parent / "media" / "sort-media.py"
)
sm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm)

Path = pathlib.Path


def probs(**kw):
    base = {c: 0.0 for c in sm.CATEGORIES}
    base.update(kw)
    return base


class FakeScorer:
    """Category chosen by a word in the file name: cibo_1.jpg -> cibo. 'rotto' is unreadable."""

    def __init__(self):
        self.calls = []

    def __call__(self, item):
        self.calls.append(item.path.name)
        name = item.path.name.lower()
        if "rotto" in name:
            raise sm.Unreadable("illeggibile: test")
        for category in sm.CATEGORIES:
            if category in name:
                return probs(**{category: 0.9, "altro": 0.1 if category != "altro" else 0.9})
        return probs(ambiente=0.3, cibo=0.3, persone=0.4)  # unsure


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.src = self.tmp / "src"
        self.out = self.tmp / "out"
        self.src.mkdir()

    def touch(self, rel, content=None):
        path = self.src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((content if content is not None else rel).encode())
        return path

    def sort(self, **kw):
        items, ignored = sm.scan([self.src], self.out)
        return sm.run(items, ignored, self.out, FakeScorer(), **kw)

    def files_under(self, root):
        return sorted(
            str(p.relative_to(root)) for p in root.rglob("*")
            if p.is_file() and not p.name.startswith("report-")
        )


class MediaKind(unittest.TestCase):
    def test_photos_videos_and_raw(self):
        self.assertEqual(sm.media_kind("a.JPG"), "photo")
        self.assertEqual(sm.media_kind("a.heic"), "photo")
        self.assertEqual(sm.media_kind("a.DNG"), "photo")
        self.assertEqual(sm.media_kind("a.MOV"), "video")
        self.assertEqual(sm.media_kind("a.insv"), "video")

    def test_other_files(self):
        self.assertIsNone(sm.media_kind("notes.txt"))
        self.assertIsNone(sm.media_kind("README"))
        self.assertIsNone(sm.media_kind("song.mp3"))


class Sidecars(unittest.TestCase):
    def test_recognised_names(self):
        media = Path("d/IMG_1.jpg")
        for name in ["IMG_1.xmp", "IMG_1.AAE", "IMG_1.jpg.xmp", "IMG_1.jpg.json",
                     "IMG_1.jpg.supplemental-metadata.json", "img_1.XMP"]:
            self.assertTrue(sm.is_sidecar_of(Path("d") / name, media), name)

    def test_unrelated_names(self):
        media = Path("d/IMG_1.jpg")
        for path in ["d/IMG_10.xmp", "d/IMG_1.txt", "e/IMG_1.xmp", "d/IMG_1.jpg.txt"]:
            self.assertFalse(sm.is_sidecar_of(Path(path), media), path)

    def test_sidecar_follows_renamed_media(self):
        self.assertEqual(sm.sidecar_name("IMG_1.xmp", "IMG_1.jpg", "IMG_1 (1).jpg"),
                         "IMG_1 (1).xmp")
        self.assertEqual(sm.sidecar_name("IMG_1.jpg.json", "IMG_1.jpg", "IMG_1 (1).jpg"),
                         "IMG_1 (1).jpg.json")
        self.assertEqual(sm.sidecar_name("IMG_1.xmp", "IMG_1.jpg", "IMG_1.jpg"), "IMG_1.xmp")


class Decide(unittest.TestCase):
    def test_confident_category(self):
        self.assertEqual(sm.decide(probs(cibo=0.8, persone=0.2), 0.5), ("cibo", 0.8))

    def test_below_threshold_is_uncertain(self):
        self.assertEqual(sm.decide(probs(cibo=0.4, persone=0.35), 0.5), ("incerti", 0.4))

    def test_at_threshold_is_kept(self):
        self.assertEqual(sm.decide(probs(persone=0.5), 0.5)[0], "persone")

    def test_no_scores_is_uncertain(self):
        self.assertEqual(sm.decide({}, 0.5), ("incerti", 0.0))

    def test_zero_threshold_always_picks_a_category(self):
        self.assertEqual(sm.decide(probs(ambiente=0.26, cibo=0.25), 0.0)[0], "ambiente")

    def test_tie_picks_first_category(self):
        self.assertEqual(sm.decide(probs(ambiente=0.5, cibo=0.5), 0.5)[0], "ambiente")


class Average(unittest.TestCase):
    def test_mean_per_category(self):
        result = sm.average([probs(cibo=1.0), probs(persone=1.0)])
        self.assertAlmostEqual(result["cibo"], 0.5)
        self.assertAlmostEqual(result["persone"], 0.5)

    def test_empty(self):
        self.assertEqual(sm.average([]), {})
        self.assertEqual(sm.average([{}]), {})


class FrameTimes(unittest.TestCase):
    def test_evenly_spaced_inside_the_video(self):
        self.assertEqual(sm.frame_times(10, 4), [2.0, 4.0, 6.0, 8.0])

    def test_unknown_or_zero_duration_takes_first_frame(self):
        self.assertEqual(sm.frame_times(None, 5), [0.0])
        self.assertEqual(sm.frame_times(0, 5), [0.0])


class Scan(TempDirTest):
    def test_finds_media_recursively_and_reports_others(self):
        self.touch("a.jpg")
        self.touch("sub/deep/b.MP4")
        self.touch("notes.txt")
        items, ignored = sm.scan([self.src], self.out)
        self.assertEqual([i.path.name for i in items], ["a.jpg", "b.MP4"])
        self.assertEqual([(p.name, r) for p, r in ignored], [("notes.txt", "non è una foto né un video")])

    def test_skips_hidden_and_system_folders(self):
        self.touch(".DS_Store")
        self.touch("._a.jpg")
        self.touch(".thumbnails/t.jpg")
        self.touch("__MACOSX/x.jpg")
        self.touch("@eaDir/y.jpg")
        self.touch("ok.jpg")
        items, ignored = sm.scan([self.src], self.out)
        self.assertEqual([i.path.name for i in items], ["ok.jpg"])
        self.assertEqual(ignored, [])

    def test_destination_inside_source_is_not_rescanned(self):
        self.touch("a.jpg")
        self.out = self.src / "ordinati"
        (self.out / "foto" / "cibo").mkdir(parents=True)
        (self.out / "foto" / "cibo" / "old.jpg").write_bytes(b"x")
        items, _ = sm.scan([self.src], self.out)
        self.assertEqual([i.path.name for i in items], ["a.jpg"])

    def test_nested_or_repeated_sources_count_once(self):
        self.touch("sub/a.jpg")
        items, _ = sm.scan([self.src, self.src / "sub", self.src], self.out)
        self.assertEqual(len(items), 1)

    def test_symlink_is_ignored(self):
        target = self.touch("a.jpg")
        os.symlink(target, self.src / "link.jpg")
        items, ignored = sm.scan([self.src, self.src], self.out)
        self.assertEqual([i.path.name for i in items], ["a.jpg"])
        self.assertEqual([(p.name, r) for p, r in ignored], [("link.jpg", "collegamento simbolico")])

    def test_sidecars_attach_to_their_media(self):
        self.touch("IMG_1.jpg")
        self.touch("IMG_1.jpg.json")
        self.touch("IMG_1.xmp")
        self.touch("orphan.xmp")
        items, ignored = sm.scan([self.src], self.out)
        self.assertEqual(sorted(s.name for s in items[0].sidecars), ["IMG_1.jpg.json", "IMG_1.xmp"])
        self.assertEqual([p.name for p, _ in ignored], ["orphan.xmp"])

    def test_live_photo_sidecar_goes_to_the_photo(self):
        self.touch("IMG_1.MOV")
        self.touch("IMG_1.HEIC")
        self.touch("IMG_1.AAE")
        items, _ = sm.scan([self.src], self.out)
        by_name = {i.path.name: i for i in items}
        self.assertEqual([s.name for s in by_name["IMG_1.HEIC"].sidecars], ["IMG_1.AAE"])
        self.assertEqual(by_name["IMG_1.MOV"].sidecars, [])

    def test_empty_folder(self):
        self.assertEqual(sm.scan([self.src], self.out), ([], []))


class UniqueDestination(TempDirTest):
    def test_free_name_is_kept(self):
        src = self.touch("a.jpg")
        self.assertEqual(sm.unique_destination(self.out, src, set()), self.out / "a.jpg")

    def test_different_file_with_same_name_is_numbered(self):
        src = self.touch("a.jpg", "new")
        self.out.mkdir()
        (self.out / "a.jpg").write_text("old")
        (self.out / "a (1).jpg").write_text("older")
        self.assertEqual(sm.unique_destination(self.out, src, set()), self.out / "a (2).jpg")

    def test_identical_file_already_there_is_a_duplicate(self):
        src = self.touch("a.jpg", "same")
        self.out.mkdir()
        (self.out / "a.jpg").write_text("same")
        self.assertIsNone(sm.unique_destination(self.out, src, set()))

    def test_names_planned_in_this_run_are_case_insensitive(self):
        taken = set()
        first = sm.unique_destination(self.out, self.touch("x/IMG.jpg"), taken)
        second = sm.unique_destination(self.out, self.touch("y/img.JPG"), taken)
        self.assertEqual(first.name, "IMG.jpg")
        self.assertEqual(second.name, "img (1).JPG")


class Run(TempDirTest):
    def test_copies_into_kind_and_category_folders(self):
        self.touch("cibo_pizza.jpg")
        self.touch("viaggio/ambiente_mare.HEIC")
        self.touch("persone_festa.mov")
        self.touch("altro_screenshot.png")
        self.touch("boh.jpg")
        summary = self.sort()
        self.assertEqual(self.files_under(self.out), [
            "foto/altro/altro_screenshot.png",
            "foto/ambiente/ambiente_mare.HEIC",
            "foto/cibo/cibo_pizza.jpg",
            "foto/incerti/boh.jpg",
            "video/persone/persone_festa.mov",
        ])
        self.assertEqual(summary.counts["foto/incerti"], 1)
        self.assertEqual(summary.errors, 0)
        self.assertTrue((self.src / "cibo_pizza.jpg").exists(), "copy must keep the original")

    def test_copy_keeps_modification_time(self):
        src = self.touch("cibo.jpg")
        os.utime(src, (1_000_000_000, 1_000_000_000))
        self.sort()
        self.assertEqual((self.out / "foto/cibo/cibo.jpg").stat().st_mtime, 1_000_000_000)

    def test_move_removes_originals_with_their_sidecars(self):
        self.touch("cibo.jpg")
        self.touch("cibo.xmp")
        self.sort(move=True)
        self.assertEqual(self.files_under(self.out), ["foto/cibo/cibo.jpg", "foto/cibo/cibo.xmp"])
        self.assertEqual(list(self.src.iterdir()), [])

    def test_dry_run_writes_nothing(self):
        self.touch("cibo.jpg")
        summary = self.sort(dry_run=True)
        self.assertFalse(self.out.exists())
        self.assertIsNone(summary.report)
        self.assertEqual(summary.rows[0].action, "da copiare")
        self.assertEqual(summary.rows[0].destination, str(self.out / "foto/cibo/cibo.jpg"))

    def test_unreadable_file_goes_to_its_own_folder_with_reason(self):
        self.touch("rotto.jpg", "")
        summary = self.sort()
        self.assertTrue((self.out / "foto/illeggibili/rotto.jpg").exists())
        self.assertEqual(summary.rows[0].note, "illeggibile: test")
        self.assertEqual(summary.rows[0].confidence, 0.0)

    def test_video_not_analysed_goes_to_uncertain(self):
        self.touch("persone.mp4")

        def no_ffmpeg(item):
            raise sm.NotAnalysed("ffmpeg non installato: video non analizzato")

        items, ignored = sm.scan([self.src], self.out)
        summary = sm.run(items, ignored, self.out, no_ffmpeg)
        self.assertTrue((self.out / "video/incerti/persone.mp4").exists())
        self.assertIn("ffmpeg", summary.rows[0].note)

    def test_threshold_controls_uncertain(self):
        self.touch("boh.jpg")  # best score 0.4
        self.sort(threshold=0.3)
        self.assertTrue((self.out / "foto/persone/boh.jpg").exists())

    def test_running_twice_copies_nothing_new(self):
        self.touch("cibo.jpg")
        self.touch("persone.mp4")
        self.sort()
        summary = self.sort()
        self.assertEqual([r.action for r in summary.rows], ["duplicato", "duplicato"])
        self.assertEqual(summary.counts, {})
        self.assertEqual(len(self.files_under(self.out)), 2)

    def test_same_name_from_two_folders_both_kept(self):
        self.touch("a/cibo.jpg", "one")
        self.touch("b/cibo.jpg", "two")
        self.touch("b/cibo.xmp", "two-meta")
        self.sort()
        self.assertEqual(self.files_under(self.out),
                         ["foto/cibo/cibo (1).jpg", "foto/cibo/cibo (1).xmp", "foto/cibo/cibo.jpg"])

    def test_identical_copies_in_two_folders_kept_once(self):
        self.touch("a/cibo.jpg", "same")
        self.touch("b/cibo.jpg", "same")
        summary = self.sort()
        self.assertEqual(self.files_under(self.out), ["foto/cibo/cibo.jpg"])
        self.assertEqual([r.action for r in summary.rows], ["copiato", "duplicato"])

    def test_move_never_deletes_a_duplicate_source(self):
        self.touch("cibo.jpg", "same")
        (self.out / "foto/cibo").mkdir(parents=True)
        (self.out / "foto/cibo/cibo.jpg").write_text("same")
        self.sort(move=True)
        self.assertTrue((self.src / "cibo.jpg").exists())

    def test_sidecar_never_overwrites_an_existing_file(self):
        (self.out / "foto/cibo").mkdir(parents=True)
        (self.out / "foto/cibo/cibo.xmp").write_text("someone else's")
        self.touch("cibo.jpg")
        self.touch("cibo.xmp", "mine")
        self.sort()
        self.assertEqual((self.out / "foto/cibo/cibo.xmp").read_text(), "someone else's")
        self.assertEqual((self.out / "foto/cibo/cibo (1).xmp").read_text(), "mine")

    def test_transfer_error_is_recorded_and_the_run_goes_on(self):
        self.touch("cibo.jpg")
        self.touch("persone.jpg")
        real = sm.transfer

        def failing(src, dest, move):
            if src.name == "cibo.jpg":
                raise OSError("disco pieno")
            real(src, dest, move)

        with mock.patch.object(sm, "transfer", failing):
            summary = self.sort()
        self.assertEqual(summary.errors, 1)
        self.assertEqual(summary.rows[0].action, "errore")
        self.assertIn("disco pieno", summary.rows[0].note)
        self.assertTrue((self.out / "foto/persone/persone.jpg").exists())

    def test_report_lists_every_decision(self):
        self.touch("cibo.jpg")
        self.touch("notes.txt")
        summary = self.sort()
        with open(summary.report, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual([r["azione"] for r in rows], ["copiato", "ignorato"])
        self.assertEqual(rows[0]["categoria"], "cibo")
        self.assertEqual(rows[0]["confidenza"], "0.9")
        self.assertIn("cibo=0.90", rows[0]["punteggi"])

    def test_reports_of_two_runs_do_not_overwrite_each_other(self):
        self.touch("cibo.jpg")
        first = self.sort().report
        second = self.sort().report
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists() and second.exists())


class Main(TempDirTest):
    def main(self, *argv, factory=None):
        factory = factory or (lambda args, log: FakeScorer())
        with mock.patch("builtins.print"):
            return sm.main([str(a) for a in argv], scorer_factory=factory)

    def test_success(self):
        self.touch("cibo.jpg")
        self.assertEqual(self.main(self.src, "--out", self.out), 0)
        self.assertTrue((self.out / "foto/cibo/cibo.jpg").exists())

    def test_missing_source(self):
        self.assertEqual(self.main(self.tmp / "nope", "--out", self.out), 1)

    def test_source_inside_destination_is_refused(self):
        self.assertEqual(self.main(self.src, "--out", self.tmp), 1)
        self.assertEqual(self.main(self.src, "--out", self.src), 1)

    def test_destination_that_is_a_file_is_refused(self):
        self.out.write_text("x")
        self.assertEqual(self.main(self.src, "--out", self.out), 1)

    def test_no_media_does_not_load_the_model(self):
        self.touch("notes.txt")
        factory = mock.Mock()
        self.assertEqual(self.main(self.src, "--out", self.out, factory=factory), 0)
        factory.assert_not_called()

    def test_transfer_error_exits_1(self):
        self.touch("cibo.jpg")
        with mock.patch.object(sm, "transfer", side_effect=OSError("no")):
            self.assertEqual(self.main(self.src, "--out", self.out), 1)

    def test_invalid_options_exit_2(self):
        for extra in (["--threshold", "1.5"], ["--threshold", "-0.1"], ["--frames", "0"],
                      ["--device", "tpu"], []):
            argv = [self.src, *extra] if extra == [] else [self.src, "--out", self.out, *extra]
            with self.subTest(extra=extra), mock.patch("sys.stderr"):
                with self.assertRaises(SystemExit) as ctx:
                    self.main(*argv)
                self.assertEqual(ctx.exception.code, 2)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg not installed")
class VideoFrames(TempDirTest):
    def make_video(self, seconds):
        path = self.src / "clip.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=10",
             "-pix_fmt", "yuv420p", str(path)],
            check=True,
        )
        return path

    def test_duration_and_frames(self):
        video = self.make_video(4)
        self.assertAlmostEqual(sm.video_duration(video), 4.0, delta=0.2)
        frames = sm.extract_frames(video, 3, self.tmp)
        self.assertEqual(len(frames), 3)
        self.assertTrue(all(f.stat().st_size > 0 for f in frames))

    def test_broken_video_gives_no_frames(self):
        broken = self.touch("broken.mp4", "not a video")
        self.assertIsNone(sm.video_duration(broken))
        self.assertEqual(sm.extract_frames(broken, 3, self.tmp), [])


if __name__ == "__main__":
    unittest.main()
