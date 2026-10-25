"""Tests of audio/split-stems.py with a fake runner standing in for Demucs, ffmpeg and Basic
Pitch: no model, no audio, standard library only."""

import contextlib
import importlib.util
import io
import pathlib
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "split_stems", pathlib.Path(__file__).parent.parent / "audio" / "split-stems.py"
)
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


def song(name, ext=".mp3"):
    return ss.Song(pathlib.Path(f"/in/{name}{ext}"), name)


class FakeRunner:
    """Writes the files each tool would write; `fail` lists predicates of commands that fail."""

    def __init__(self, fail=()):
        self.fail = fail
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        if any(rule(command) for rule in self.fail):
            return False
        if "demucs" in command:
            out = pathlib.Path(command[command.index("-o") + 1])
            folder = out / ss.MODEL / pathlib.Path(command[-1]).stem
            folder.mkdir(parents=True, exist_ok=True)
            for stem in ss.STEMS:
                (folder / f"{stem}.wav").write_bytes(b"wav")
        elif command[0] == "ffmpeg":
            pathlib.Path(command[-1]).write_bytes(b"wav")
        elif "basic_pitch.predict" in command:
            wav = pathlib.Path(command[-1])
            (pathlib.Path(command[-2]) / f"{wav.stem}_basic_pitch.mid").write_bytes(b"midi")
        return True

    def kinds(self):
        def kind(c):
            if "demucs" in c:
                return f"demucs:{c[c.index('-d') + 1]}"
            return "ffmpeg" if c[0] == "ffmpeg" else "midi"
        return [kind(c) for c in self.calls]


def device_fails(device):
    return lambda c: "demucs" in c and c[c.index("-d") + 1] == device


class FindSongs(unittest.TestCase):
    def test_audio_files_only_top_level_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp)
            for name in ["b.MP3", "a.flac", "c.m4a", "d.wav", "note.txt", "._a.flac", ".hidden.wav"]:
                (folder / name).write_bytes(b"x")
            (folder / "sub").mkdir()
            (folder / "sub" / "e.wav").write_bytes(b"x")
            (folder / "dir.wav").mkdir()
            songs, duplicates = ss.find_songs(folder)
        self.assertEqual([s.path.name for s in songs], ["a.flac", "b.MP3", "c.m4a", "d.wav"])
        self.assertEqual([s.name for s in songs], ["a", "b", "c", "d"])
        self.assertEqual(duplicates, [])

    def test_same_name_different_extension_kept_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = pathlib.Path(tmp)
            (folder / "brano.mp3").write_bytes(b"x")
            (folder / "brano.wav").write_bytes(b"x")
            songs, duplicates = ss.find_songs(folder)
        self.assertEqual([s.path.name for s in songs], ["brano.mp3"])
        self.assertEqual([p.name for p in duplicates], ["brano.wav"])

    def test_names_with_spaces_and_accents(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "Canzone finta è.mp3").write_bytes(b"x")
            songs, _ = ss.find_songs(tmp)
        self.assertEqual(songs[0].name, "Canzone finta è")


class Commands(unittest.TestCase):
    def test_demucs_on_gpu(self):
        command = ss.demucs_command("py", song("a b"), pathlib.Path("/out"), "mps", 2)
        self.assertEqual(command, [
            "py", "-m", "demucs", "-n", "htdemucs_6s", "-d", "mps", "--shifts", "2",
            "--overlap", "0.25", "-o", "/out", "/in/a b.mp3",
        ])

    def test_demucs_on_cpu_keeps_shifts(self):
        command = ss.demucs_command("py", song("a"), pathlib.Path("/out"), "cpu", 1)
        self.assertEqual(command[5:], ["-d", "cpu", "--shifts", "1", "-o", "/out", "/in/a.mp3"])

    def test_devices(self):
        self.assertEqual(ss.devices("auto"), ["mps", "cpu"])
        self.assertEqual(ss.devices("cpu"), ["cpu"])

    def test_basic_pitch_limited_to_guitar_range(self):
        command = ss.basic_pitch_command("py", pathlib.Path("/s/guitar.wav"), "/tmp/m")
        self.assertEqual(command, [
            "py", "-m", "basic_pitch.predict", "--minimum-frequency", "75",
            "--maximum-frequency", "1400", "/tmp/m", "/s/guitar.wav",
        ])

    def test_channel_commands(self):
        commands = ss.channel_commands(pathlib.Path("/s/a/guitar.wav"))
        self.assertEqual([c[-1] for c in commands], [
            "/s/a/guitar_left.wav", "/s/a/guitar_right.wav", "/s/a/guitar_side.wav",
        ])
        self.assertEqual([c[c.index("-af") + 1] for c in commands], [
            "pan=mono|c0=c0", "pan=mono|c0=c1", "pan=mono|c0=0.5*c0-0.5*c1",
        ])

    def test_midi_path(self):
        self.assertEqual(
            ss.midi_path(pathlib.Path("/out"), song("a b"), "other"), pathlib.Path("/out/midi/a b_other.mid")
        )


class ParseArgs(unittest.TestCase):
    def test_defaults(self):
        args = ss.parse_args(["in"])
        self.assertEqual((args.shifts, args.device, args.midi, args.channels, args.output), (2, "auto", ["guitar"], True, None))

    def test_midi_list_and_none(self):
        self.assertEqual(ss.parse_args(["in", "--midi", " guitar , other"]).midi, ["guitar", "other"])
        self.assertEqual(ss.parse_args(["in", "--midi", ""]).midi, [])

    def test_invalid_values(self):
        for argv in (["in", "--midi", "chitarra"], ["in", "--shifts", "0"], ["in", "--device", "cuda"]):
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    ss.parse_args(argv)


class Main(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = pathlib.Path(self.tmp.name)
        (self.folder / "Uno.mp3").write_bytes(b"x")
        (self.folder / "Due.wav").write_bytes(b"x")
        patch = mock.patch.object(ss.shutil, "which", return_value="/usr/bin/ffmpeg")
        patch.start()
        self.addCleanup(patch.stop)
        self.out = self.folder / "stems"

    def run_main(self, *args, runner=None):
        runner = runner or FakeRunner()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ss.main([str(self.folder), *args], runner=runner)
        return code, out.getvalue(), err.getvalue(), runner

    def test_full_run(self):
        code, out, err, runner = self.run_main()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(runner.kinds(), ["demucs:mps", "ffmpeg", "ffmpeg", "ffmpeg", "midi"] * 2)
        for name in ("Uno", "Due"):
            folder = self.out / "htdemucs_6s" / name
            self.assertTrue((folder / "guitar_side.wav").is_file())
            self.assertTrue((self.out / "midi" / f"{name}_guitar.mid").is_file())
        self.assertEqual(sorted(p.name for p in (self.out / "midi").iterdir()), ["Due_guitar.mid", "Uno_guitar.mid"])
        self.assertIn("Fatto. Stem in:", out)

    def test_second_run_skips_everything(self):
        self.run_main()
        code, out, _, runner = self.run_main()
        self.assertEqual((code, runner.calls), (0, []))
        self.assertEqual(out.count("già fatto"), 2)

    def test_only_missing_midi_is_redone(self):
        self.run_main()
        (self.out / "midi" / "Uno_guitar.mid").unlink()
        _, _, _, runner = self.run_main()
        self.assertEqual(runner.kinds(), ["midi"])

    def test_new_midi_stem_on_existing_stems(self):
        self.run_main()
        _, _, _, runner = self.run_main("--midi", "guitar,other")
        self.assertEqual(runner.kinds(), ["midi", "midi"])
        self.assertTrue((self.out / "midi" / "Uno_other.mid").is_file())

    def test_gpu_failure_falls_back_to_cpu(self):
        code, _, err, runner = self.run_main("--midi", "", "--no-channels", runner=FakeRunner([device_fails("mps")]))
        self.assertEqual(code, 0)
        self.assertEqual(runner.kinds(), ["demucs:mps", "demucs:cpu"] * 2)
        self.assertIn("separazione su mps fallita", err)

    def test_separation_failure_continues_with_next_song(self):
        fail_due = lambda c: "demucs" in c and c[-1].endswith("Due.wav")  # noqa: E731
        code, out, err, runner = self.run_main(runner=FakeRunner([fail_due]))
        self.assertEqual(code, 1)
        self.assertIn("Errore: Due: separazione fallita", err)
        self.assertTrue((self.out / "midi" / "Uno_guitar.mid").is_file())
        self.assertEqual(runner.kinds().count("demucs:cpu"), 1)

    def test_midi_failure_reported_and_temp_dir_removed(self):
        base = pathlib.Path(self.tmp.name) / "tmpbase"
        base.mkdir()
        fail_midi = lambda c: "basic_pitch.predict" in c  # noqa: E731
        with mock.patch.object(tempfile, "tempdir", str(base)):
            code, _, err, _ = self.run_main("--no-channels", runner=FakeRunner([fail_midi]))
        self.assertEqual(code, 1)
        self.assertIn("Uno: MIDI di guitar non creato", err)
        self.assertEqual(list(base.iterdir()), [])

    def test_existing_midi_is_kept(self):
        self.run_main()
        target = self.out / "midi" / "Uno_guitar.mid"
        target.write_bytes(b"old")
        (self.out / "midi" / "Due_guitar.mid").unlink()
        self.run_main()
        self.assertEqual(target.read_bytes(), b"old")  # not missing: kept as it is

    def test_channels_failure_reported(self):
        fail_ffmpeg = lambda c: c[0] == "ffmpeg"  # noqa: E731
        code, _, err, _ = self.run_main("--midi", "", runner=FakeRunner([fail_ffmpeg]))
        self.assertEqual(code, 1)
        self.assertIn("Uno: canali della chitarra non creati", err)

    def test_no_channels(self):
        _, _, _, runner = self.run_main("--no-channels")
        self.assertNotIn("ffmpeg", runner.kinds())

    def test_custom_output_folder(self):
        with tempfile.TemporaryDirectory() as other:
            self.run_main(other, "--midi", "", "--no-channels")
            self.assertTrue((pathlib.Path(other) / "htdemucs_6s" / "Uno" / "guitar.wav").is_file())
        self.assertFalse(self.out.exists())

    def test_duplicate_name_skipped_with_message(self):
        (self.folder / "Uno.flac").write_bytes(b"x")
        _, _, err, runner = self.run_main("--midi", "", "--no-channels")
        self.assertIn("Salto Uno.mp3", err)
        self.assertEqual(len(runner.calls), 2)

    def test_empty_folder(self):
        for f in self.folder.iterdir():
            f.unlink()
        code, _, err, runner = self.run_main()
        self.assertEqual((code, runner.calls), (0, []))
        self.assertIn("Nessun brano", err)

    def main_with(self, path):
        out, err = io.StringIO(), io.StringIO()
        runner = FakeRunner()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ss.main([str(path), "--midi", "", "--no-channels"], runner=runner)
        return code, err.getvalue(), runner

    def test_missing_path(self):
        code, err, runner = self.main_with(self.folder / "manca.mp3")
        self.assertEqual((code, runner.calls), (ss.EXIT_USAGE, []))
        self.assertIn("file o cartella non trovati", err)

    def test_single_song_with_accent_and_spaces(self):
        song_file = self.folder / "34_Brano finto -  MARILÙ (Video).mp3"
        song_file.write_bytes(b"x")
        code, err, runner = self.main_with(song_file)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(runner.calls), 1)  # only that song, not the whole folder
        self.assertTrue((self.out / "htdemucs_6s" / song_file.stem / "guitar.wav").is_file())

    def test_single_song_unsupported_format(self):
        text = self.folder / "note.txt"
        text.write_bytes(b"x")
        code, err, runner = self.main_with(text)
        self.assertEqual((code, runner.calls), (ss.EXIT_USAGE, []))
        self.assertIn("formato non supportato: note.txt", err)

    def test_single_song_uppercase_extension(self):
        song_file = self.folder / "Tre.FLAC"
        song_file.write_bytes(b"x")
        code, _, runner = self.main_with(song_file)
        self.assertEqual((code, len(runner.calls)), (0, 1))

    def test_missing_ffmpeg(self):
        with mock.patch.object(ss.shutil, "which", return_value=None):
            code, _, err, runner = self.run_main()
        self.assertEqual((code, runner.calls), (ss.EXIT_NO_FFMPEG, []))
        self.assertIn("brew install ffmpeg", err)


if __name__ == "__main__":
    unittest.main()
