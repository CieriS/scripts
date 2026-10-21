"""Tests of video/transcript.py: VTT cleanup, source choice and the main flow with a fake
yt-dlp (no network, no mlx-whisper)."""

import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).parent.parent
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "video"

spec = importlib.util.spec_from_file_location("transcript", ROOT / "video" / "transcript.py")
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)

URL = "https://www.youtube.com/watch?v=abcdefghijk"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def text_of(words):
    return " ".join(word for word, _ in words)


class NormalizeUrl(unittest.TestCase):
    def test_accepted_forms(self):
        for url in [
            URL,
            "https://youtu.be/abcdefghijk",
            "https://youtu.be/abcdefghijk?t=42",
            "https://m.youtube.com/watch?v=abcdefghijk&list=PL123",
            "https://www.youtube.com/shorts/abcdefghijk",
            "https://www.youtube.com/live/abcdefghijk?si=x",
            "http://youtube.com/embed/abcdefghijk",
            "  https://music.youtube.com/watch?v=abcdefghijk  ",
        ]:
            with self.subTest(url=url):
                self.assertEqual(tr.normalize_url(url), URL)

    def test_rejected_forms(self):
        for url in [
            "",
            "abcdefghijk",
            "https://www.youtube.com/playlist?list=PL123",
            "https://www.youtube.com/watch?v=short",
            "https://www.youtube.com/@canale",
            "ftp://youtu.be/abcdefghijk",
            "ftp://www.instagram.com/reel/abc/",
            "http://localhost/video",
            "instagram.com/reel/abc/",
        ]:
            with self.subTest(url=url):
                with self.assertRaises(tr.TranscriptError) as ctx:
                    tr.normalize_url(url)
                self.assertEqual(ctx.exception.code, tr.EXIT_USAGE)


class NormalizeOtherSites(unittest.TestCase):
    def test_other_sites_passed_unchanged(self):
        for url in [
            "https://www.instagram.com/reel/Abc123/",
            "https://www.tiktok.com/@utente/video/1234567890",
            "https://x.com/utente/status/1234567890",
            "https://vimeo.com/12345",
            "https://www.facebook.com/watch/?v=1234567890",
        ]:
            with self.subTest(url=url):
                self.assertEqual(tr.normalize_url(f" {url} "), url)


class CheckSingleVideo(unittest.TestCase):
    def test_single_video_accepted(self):
        tr.check_single_video({"_type": "video"})
        tr.check_single_video({})

    def test_carousel_rejected_with_count(self):
        with self.assertRaises(tr.TranscriptError) as ctx:
            tr.check_single_video({"_type": "playlist", "entries": [{}, {}, {}]})
        self.assertIn("contiene 3 video", str(ctx.exception))

    def test_multi_video_without_entries(self):
        with self.assertRaises(tr.TranscriptError) as ctx:
            tr.check_single_video({"_type": "multi_video"})
        self.assertIn("contiene più video", str(ctx.exception))


class HasAudio(unittest.TestCase):
    def test_video_only_formats(self):
        info = {"formats": [{"acodec": "none", "vcodec": "avc1"}, {"acodec": "none"}]}
        self.assertFalse(tr.has_audio(info))

    def test_one_format_with_audio(self):
        self.assertTrue(tr.has_audio({"formats": [{"acodec": "none"}, {"acodec": "mp4a.40.2"}]}))

    def test_unknown_codec_counts_as_audio(self):
        self.assertTrue(tr.has_audio({"formats": [{"vcodec": "unknown"}]}))

    def test_no_formats_listed(self):
        self.assertTrue(tr.has_audio({}))
        self.assertTrue(tr.has_audio({"formats": []}))


class LastLine(unittest.TestCase):
    def test_last_non_empty_line(self):
        self.assertEqual(tr.last_line("ffmpeg version 9\n  banner\nInvalid data found\n\n"), "Invalid data found")

    def test_empty(self):
        self.assertEqual(tr.last_line(""), "")
        self.assertEqual(tr.last_line(OSError()), "")


class ParseVtt(unittest.TestCase):
    def test_tags_and_timing_removed(self):
        cues = tr.parse_vtt(fixture("auto-rolling.vtt"))
        self.assertEqual(cues[0], (0.32, "oggi prepariamo una torta di nuvole"))
        for _, text in cues:
            self.assertNotIn("<", text)
            self.assertNotIn("-->", text)

    def test_entities_voice_and_multiline(self):
        cues = tr.parse_vtt(fixture("manual.vtt"))
        self.assertEqual(cues[0], (1.0, 'Benvenuti nel laboratorio di "Cappelli & Comete".'))
        self.assertEqual(cues[-1], (65.5, "Serve un ago d'argento."))

    def test_empty_cues_annotations_and_blocks_skipped(self):
        self.assertEqual(tr.parse_vtt(fixture("edge.vtt")), [(3.5, "fine dello spettacolo")])

    def test_header_only(self):
        self.assertEqual(tr.parse_vtt(fixture("empty.vtt")), [])

    def test_windows_line_endings(self):
        text = fixture("manual.vtt").replace("\n", "\r\n")
        self.assertEqual(tr.parse_vtt(text), tr.parse_vtt(fixture("manual.vtt")))


class MergeCues(unittest.TestCase):
    def test_rolling_captions_lose_repetitions(self):
        words = tr.merge_cues(tr.parse_vtt(fixture("auto-rolling.vtt")), rolling=True)
        self.assertEqual(
            text_of(words),
            "oggi prepariamo una torta di nuvole con farina di luna e zucchero "
            "poi mescoliamo piano piano piano",
        )

    def test_rolling_keeps_start_time_of_new_words(self):
        words = tr.merge_cues(tr.parse_vtt(fixture("auto-rolling.vtt")), rolling=True)
        self.assertEqual(words[0], ("oggi", 0.32))
        self.assertEqual(words[6], ("con", 2.76))
        self.assertEqual(words[12], ("poi", 12.0))

    def test_rolling_cue_fully_repeated_adds_nothing(self):
        cues = [(0, "a b c"), (1, "a b c"), (2, "b c")]
        self.assertEqual(text_of(tr.merge_cues(cues, rolling=True)), "a b c")

    def test_rolling_partial_overlap(self):
        cues = [(0, "uno due tre"), (1, "due tre quattro cinque")]
        self.assertEqual(text_of(tr.merge_cues(cues, rolling=True)), "uno due tre quattro cinque")

    def test_manual_drops_only_identical_consecutive_cues(self):
        words = tr.merge_cues(tr.parse_vtt(fixture("manual.vtt")), rolling=False)
        self.assertEqual(text_of(words).count("cometa"), 1)
        self.assertTrue(text_of(words).endswith("Serve un ago d'argento."))

    def test_manual_keeps_overlapping_words(self):
        cues = [(0, "è il turno"), (1, "il turno di Ada")]
        self.assertEqual(text_of(tr.merge_cues(cues, rolling=False)), "è il turno il turno di Ada")

    def test_empty(self):
        self.assertEqual(tr.merge_cues([], rolling=True), [])


class ToParagraphs(unittest.TestCase):
    def test_sentence_end_after_minimum_closes_paragraph(self):
        words = [(f"w{i}", 0) for i in range(tr.PARAGRAPH_MIN - 1)] + [("fine.", 0), ("dopo", 1)]
        paragraphs = tr.to_paragraphs(words).split("\n\n")
        self.assertEqual(len(paragraphs), 2)
        self.assertEqual(paragraphs[1], "dopo")

    def test_short_sentence_does_not_close_paragraph(self):
        self.assertEqual(tr.to_paragraphs([("Ciao.", 0), ("Tutto", 1), ("bene?", 2)]), "Ciao. Tutto bene?")

    def test_forced_break_without_punctuation(self):
        words = [("parola", 0)] * (tr.PARAGRAPH_MAX * 2 + 5)
        lengths = [len(p.split()) for p in tr.to_paragraphs(words).split("\n\n")]
        self.assertEqual(lengths, [tr.PARAGRAPH_MAX, tr.PARAGRAPH_MAX, 5])

    def test_forced_break_in_punctuated_text_waits_twice_as_long(self):
        words = [("parola", 0)] * (tr.PARAGRAPH_MAX * 2 + 1) + [("fine.", 0)]
        lengths = [len(p.split()) for p in tr.to_paragraphs(words).split("\n\n")]
        self.assertEqual(lengths, [tr.PARAGRAPH_MAX * 2, 2])

    def test_pause_closes_long_enough_paragraph(self):
        words = [("a", 0)] * tr.PAUSE_MIN + [("b", tr.PAUSE_SECONDS + 1)]
        self.assertEqual(len(tr.to_paragraphs(words).split("\n\n")), 2)

    def test_pause_mid_sentence_ignored_in_punctuated_text(self):
        words = [("a", 0)] * tr.PAUSE_MIN + [("b.", tr.PAUSE_SECONDS + 1)]
        self.assertEqual(len(tr.to_paragraphs(words).split("\n\n")), 1)

    def test_pause_after_sentence_end_closes_paragraph(self):
        words = [("a", 0)] * (tr.PAUSE_MIN - 1) + [("a.", 0), ("b", tr.PAUSE_SECONDS + 1)]
        self.assertEqual(len(tr.to_paragraphs(words).split("\n\n")), 2)

    def test_timestamps(self):
        words = [("a", 0)] * tr.PARAGRAPH_MAX + [("b", 3725.9)]
        self.assertEqual(tr.to_paragraphs(words, timestamps=True).split("\n\n")[1], "[1:02:05] b")
        self.assertTrue(tr.to_paragraphs(words, timestamps=True).startswith("[00:00] a"))

    def test_empty(self):
        self.assertEqual(tr.to_paragraphs([]), "")


class ChooseSource(unittest.TestCase):
    def test_manual_beats_automatic(self):
        info = {"language": "it", "subtitles": {"it": []}, "automatic_captions": {"it": []}}
        self.assertEqual(tr.choose_source(info, ["it", "en"]), tr.Source("manual", "it"))

    def test_language_priority_and_regional_variants(self):
        info = {"subtitles": {"en-GB": [], "it-IT": []}}
        self.assertEqual(tr.choose_source(info, ["it", "en"]), tr.Source("manual", "it-IT"))
        self.assertEqual(tr.choose_source(info, ["en", "it"]), tr.Source("manual", "en-GB"))

    def test_manual_in_original_language_outside_preferences(self):
        info = {"language": "es", "subtitles": {"es": [], "fr": []}, "automatic_captions": {"es": []}}
        self.assertEqual(tr.choose_source(info, ["it", "en"]), tr.Source("manual", "es"))

    def test_automatic_only_in_original_language(self):
        info = {"language": "en", "automatic_captions": {"it": [], "en": [], "en-orig": []}}
        self.assertEqual(tr.choose_source(info, ["it", "en"]), tr.Source("auto", "en-orig"))

    def test_original_language_guessed_from_orig_key(self):
        info = {"automatic_captions": {"de": [], "fr-orig": [], "fr": []}}
        self.assertEqual(tr.choose_source(info, ["it"]), tr.Source("auto", "fr-orig"))

    def test_translated_automatic_captions_never_used(self):
        info = {"automatic_captions": {"it": [], "en": []}}
        self.assertIsNone(tr.choose_source(info, ["it", "en"]))

    def test_live_chat_ignored_and_nothing_available(self):
        self.assertIsNone(tr.choose_source({"subtitles": {"live_chat": []}}, ["it"]))
        self.assertIsNone(tr.choose_source({"subtitles": None, "automatic_captions": None}, ["it"]))


LOGIN = (
    "il sito chiede l'accesso per questo video (login, età o membri); se lo vedi nel "
    "browser, riprova con --cookies-from-browser e il nome del browser in cui hai "
    "fatto l'accesso (chrome, brave, firefox, safari...)"
)


class YtdlpErrors(unittest.TestCase):
    def test_messages(self):
        cases = {
            "ERROR: [youtube] x: Private video. Sign in if": "il video è privato",
            "ERROR: [youtube] x: Video unavailable": "il video non è disponibile",
            "ERROR: Sign in to confirm your age": LOGIN,
            "ERROR: [TikTok] 1: TikTok is requiring login for access to this content. Use "
            "--cookies-from-browser or --cookies for the authentication.": LOGIN,
            "ERROR: [Reddit] x: Account authentication is required.": LOGIN,
            "ERROR: [Instagram] x: Instagram sent an empty media response. If it is not, then "
            "use --cookies-from-browser or --cookies": LOGIN,
            "ERROR: [vimeo] 1: The web client only works when logged-in.": LOGIN,
            'ERROR: could not find brave cookies database in "/profilo/finto"':
                "non trovo i cookie di quel browser: controlla il nome in --cookies-from-browser e "
                "che sia il browser in cui hai fatto l'accesso al sito",
            "ERROR: [Errno 1] Operation not permitted: '/finto/Cookies.binarycookies'":
                "macOS non permette di leggere i cookie di Safari: dai l'accesso completo al disco "
                "all'app del terminale (Impostazioni di Sistema > Privacy e sicurezza) o usa un "
                "altro browser",
            "ERROR: Unsupported URL: https://example.com/": "sito o indirizzo non supportato da yt-dlp",
            "ERROR: [twitter] 1: No video could be found in this tweet": "nel link non c'è nessun video",
            "ERROR: HTTP Error 429: Too Many Requests": "il sito sta limitando le richieste: riprova più tardi",
            "WARNING: a\nERROR: boom\n": "yt-dlp non è riuscito a leggere il video (ERROR: boom); prova ad aggiornarlo con brew upgrade yt-dlp",
            "": "yt-dlp non è riuscito a leggere il video (nessun dettaglio); prova ad aggiornarlo con brew upgrade yt-dlp",
        }
        for stderr, message in cases.items():
            with self.subTest(stderr=stderr):
                self.assertEqual(tr.ytdlp_error_message(stderr), message)

    def test_run_ytdlp_raises_on_failure(self):
        failed = mock.Mock(returncode=1, stderr="ERROR: Video unavailable", stdout="")
        with mock.patch.object(tr.subprocess, "run", return_value=failed):
            with self.assertRaises(tr.TranscriptError) as ctx:
                tr.run_ytdlp(["x"])
        self.assertEqual(str(ctx.exception), "il video non è disponibile")


class FakeYtdlp:
    """Stands in for run_ytdlp: answers -J with `info`, writes fixture files for downloads."""

    def __init__(self, info, vtt="manual.vtt", fail_on=None):
        self.info = info
        self.vtt = vtt
        self.fail_on = fail_on
        self.calls = []
        self.tmp_dirs = []

    def __call__(self, args):
        self.calls.append(args)
        if self.fail_on and self.fail_on in args:
            raise tr.TranscriptError("il video non è disponibile")
        if "--dump-single-json" in args:
            return json.dumps(self.info)
        out = pathlib.Path(args[args.index("-o") + 1])
        self.tmp_dirs.append(out.parent)
        if "--write-subs" in args or "--write-auto-subs" in args:
            lang = args[args.index("--sub-langs") + 1].strip("^$").replace("\\", "")
            (out.parent / f"subs.{lang}.vtt").write_text(fixture(self.vtt), encoding="utf-8")
        else:
            (out.parent / "audio.webm").write_bytes(b"fake")
        return ""


INFO = {
    "title": "Cappelli per comete", "channel": "Canale Inventato", "language": "it",
    "extractor_key": "Youtube",
}


class Main(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.TemporaryDirectory()
        self.addCleanup(self.base.cleanup)
        patches = [
            mock.patch.object(tempfile, "tempdir", self.base.name),
            mock.patch.object(tr.shutil, "which", return_value="/usr/bin/fake"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_main(self, fake, *args, transcribe=None, url=URL):
        out, err = io.StringIO(), io.StringIO()
        local = transcribe or mock.Mock(side_effect=AssertionError("fallback non atteso"))
        with mock.patch.object(tr, "run_ytdlp", fake), mock.patch.object(tr, "transcribe_local", local), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tr.main([url, *args])
        return code, out.getvalue(), err.getvalue()

    def assert_tmp_removed(self):
        self.assertEqual(list(pathlib.Path(self.base.name).iterdir()), [])

    def test_manual_subtitles(self):
        fake = FakeYtdlp({**INFO, "subtitles": {"it": []}, "automatic_captions": {"it": []}})
        code, out, _ = self.run_main(fake)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(
            "Titolo: Cappelli per comete\nAutore: Canale Inventato\nPiattaforma: Youtube\n"
            f"URL: {URL}\nFonte: sottotitoli manuali (it)\n\nBenvenuti"
        ))
        self.assertIn("--write-subs", fake.calls[1])
        self.assertEqual(len(fake.calls), 2)
        self.assert_tmp_removed()

    def test_automatic_subtitles_with_timestamps(self):
        fake = FakeYtdlp({**INFO, "automatic_captions": {"it": []}}, vtt="auto-rolling.vtt")
        code, out, _ = self.run_main(fake, "--timestamps")
        self.assertEqual(code, 0)
        self.assertIn("Fonte: sottotitoli automatici (it)", out)
        self.assertIn("\n\n[00:00] oggi prepariamo una torta di nuvole con farina", out)
        self.assertIn("--write-auto-subs", fake.calls[1])

    def test_fallback_to_local_transcription(self):
        fake = FakeYtdlp(INFO)
        local = mock.Mock(return_value=[(0.0, " Una storia inventata."), (2.0, "Fine.")])
        code, out, err = self.run_main(fake, "--model", "modello/finto", transcribe=local)
        self.assertEqual(code, 0)
        self.assertIn("Fonte: trascrizione locale (modello/finto)", out)
        self.assertTrue(out.endswith("Una storia inventata. Fine.\n"))
        audio, model, language = local.call_args.args
        self.assertEqual(audio.name, "audio.webm")
        self.assertEqual((model, language), ("modello/finto", "it"))
        self.assertIn("-f", fake.calls[1])
        self.assertIn("trascrivo", err)
        self.assert_tmp_removed()

    def test_no_fallback(self):
        code, out, err = self.run_main(FakeYtdlp(INFO), "--no-fallback")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("nessun sottotitolo", err)

    def test_fallback_failure_reported_and_tmp_removed(self):
        local = mock.Mock(side_effect=tr.TranscriptError("trascrizione locale fallita: boom"))
        code, out, err = self.run_main(FakeYtdlp(INFO), transcribe=local)
        self.assertEqual(code, 1)
        self.assertEqual(err, (
            "Nessun sottotitolo: scarico l'audio e lo trascrivo con "
            f"{tr.DEFAULT_MODEL}...\nErrore: trascrizione locale fallita: boom\n"
        ))
        self.assert_tmp_removed()

    def test_unexpected_exception_still_removes_tmp(self):
        fake = FakeYtdlp({**INFO, "subtitles": {"it": []}})
        with mock.patch.object(tr, "parse_vtt", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.run_main(fake)
        self.assertTrue(fake.tmp_dirs)
        self.assertFalse(fake.tmp_dirs[0].exists())
        self.assert_tmp_removed()

    def test_empty_subtitles(self):
        fake = FakeYtdlp({**INFO, "subtitles": {"it": []}}, vtt="empty.vtt")
        code, _, err = self.run_main(fake)
        self.assertEqual((code, err), (1, "Errore: la trascrizione è vuota\n"))
        self.assert_tmp_removed()

    def test_unavailable_video(self):
        code, out, err = self.run_main(FakeYtdlp(INFO, fail_on="--dump-single-json"))
        self.assertEqual((code, out, err), (1, "", "Errore: il video non è disponibile\n"))
        self.assert_tmp_removed()

    def test_invalid_url_does_not_call_ytdlp(self):
        fake = FakeYtdlp(INFO)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(tr, "run_ytdlp", fake), contextlib.redirect_stderr(err):
            code = tr.main(["ftp://vimeo.com/1"])
        self.assertEqual(code, tr.EXIT_USAGE)
        self.assertEqual(fake.calls, [])
        self.assertIn("URL non valido", err.getvalue())

    def test_missing_ytdlp(self):
        with mock.patch.object(tr.shutil, "which", return_value=None):
            code, _, err = self.run_main(FakeYtdlp(INFO))
        self.assertEqual(code, tr.EXIT_NO_YTDLP)
        self.assertIn("brew install yt-dlp", err)

    def test_output_file(self):
        target = pathlib.Path(self.base.name).parent / f"{pathlib.Path(self.base.name).name}-out.txt"
        self.addCleanup(target.unlink, missing_ok=True)
        fake = FakeYtdlp({**INFO, "subtitles": {"it": []}})
        code, out, err = self.run_main(fake, "-o", str(target))
        self.assertEqual((code, out), (0, ""))
        self.assertIn("Fonte: sottotitoli manuali (it)", target.read_text(encoding="utf-8"))
        self.assertIn("salvata", err)

    def test_instagram_reel_without_subtitles(self):
        reel = "https://www.instagram.com/reel/Abc123/"
        info = {
            "_type": "video", "title": "Video by utente_finto", "uploader": "Utente Finto",
            "channel": "utente_finto", "extractor_key": "Instagram", "webpage_url": reel,
        }
        fake = FakeYtdlp(info)
        local = mock.Mock(return_value=[(0.0, "Ecco la ricetta inventata.")])
        code, out, _ = self.run_main(fake, transcribe=local, url=reel)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(
            "Titolo: Video by utente_finto\nAutore: utente_finto\nPiattaforma: Instagram\n"
            f"URL: {reel}\nFonte: trascrizione locale"
        ))
        self.assertEqual([call[-1] for call in fake.calls], [reel, reel])
        self.assertIsNone(local.call_args.args[2])  # no language: Whisper detects it

    def test_carousel_rejected_before_any_download(self):
        fake = FakeYtdlp({**INFO, "_type": "playlist", "entries": [{}, {}]})
        code, out, err = self.run_main(fake)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("contiene 2 video", err)
        self.assertEqual(len(fake.calls), 1)
        self.assert_tmp_removed()

    def test_video_without_audio_stops_before_download(self):
        info = {**INFO, "formats": [{"acodec": "none"}, {"acodec": "none"}]}
        fake = FakeYtdlp(info)
        code, out, err = self.run_main(fake)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("solo il video senza audio", err)
        self.assertIn("--cookies-from-browser", err)
        self.assertEqual(len(fake.calls), 1)
        self.assert_tmp_removed()

    def test_video_without_audio_with_cookies_has_no_hint(self):
        fake = FakeYtdlp({**INFO, "formats": [{"acodec": "none"}]})
        _, _, err = self.run_main(fake, "--cookies-from-browser", "chrome")
        self.assertEqual(err, "Errore: nessun sottotitolo e il sito fornisce solo il video senza audio\n")

    def test_video_without_audio_but_subtitles_still_works(self):
        fake = FakeYtdlp({**INFO, "subtitles": {"it": []}, "formats": [{"acodec": "none"}]})
        code, _, _ = self.run_main(fake)
        self.assertEqual(code, 0)

    def test_no_login_by_default(self):
        fake = FakeYtdlp({**INFO, "subtitles": {"it": []}})
        self.run_main(fake)
        for call in fake.calls:
            self.assertFalse(any("cookies" in arg for arg in call))

    def test_cookies_passed_to_every_call(self):
        fake = FakeYtdlp(INFO)
        local = mock.Mock(return_value=[(0.0, "Testo.")])
        code, _, _ = self.run_main(fake, "--cookies-from-browser", "firefox", transcribe=local)
        self.assertEqual(code, 0)
        self.assertEqual(len(fake.calls), 2)
        for call in fake.calls:
            i = call.index("--cookies-from-browser")
            self.assertEqual(call[i + 1], "firefox")
            self.assertEqual(call[-1], URL)

    def test_lang_option(self):
        fake = FakeYtdlp({**INFO, "subtitles": {"it": [], "en": []}})
        code, out, _ = self.run_main(fake, "--lang", " en , it")
        self.assertEqual(code, 0)
        self.assertIn("(en)", out)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tr.parse_args([URL, "--lang", ","])


class TranscribeLocal(unittest.TestCase):
    def test_missing_ffmpeg(self):
        with mock.patch.object(tr.shutil, "which", return_value=None):
            with self.assertRaises(tr.TranscriptError) as ctx:
                tr.transcribe_local(pathlib.Path("a"), "m", None)
        self.assertIn("ffmpeg", str(ctx.exception))

    def test_segments_cleaned(self):
        fake = mock.Mock()
        fake.transcribe.return_value = {"segments": [{"start": 1.5, "text": " Ciao [Musica] a tutti "}]}
        with mock.patch.object(tr.shutil, "which", return_value="/x"), \
                mock.patch.dict("sys.modules", {"mlx_whisper": fake}):
            self.assertEqual(tr.transcribe_local(pathlib.Path("a"), "m", "it"), [(1.5, "Ciao a tutti")])
        fake.transcribe.assert_called_once_with("a", path_or_hf_repo="m", language="it")

    def test_engine_error_wrapped(self):
        fake = mock.Mock()
        fake.transcribe.side_effect = RuntimeError("Failed to load audio: ffmpeg version 9\n  banner\nrete assente\n")
        with mock.patch.object(tr.shutil, "which", return_value="/x"), \
                mock.patch.dict("sys.modules", {"mlx_whisper": fake}):
            with self.assertRaises(tr.TranscriptError) as ctx:
                tr.transcribe_local(pathlib.Path("a"), "m", None)
        self.assertEqual(str(ctx.exception), "trascrizione locale fallita: rete assente")


if __name__ == "__main__":
    unittest.main()
