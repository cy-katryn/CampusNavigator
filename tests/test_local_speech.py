"""Exercise the HTTP recording contract without a model download or microphone."""
import io
import json
import subprocess
import tempfile
import threading
import unittest
import wave
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from campus_nav.speech import MAX_SECONDS, SAMPLE_RATE, LocalTranscriber
from campus_nav.web import Handler


def recording(rate=SAMPLE_RATE, samples=1000, value=32):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, rate, samples, "NONE", "not compressed"))
        wav.writeframes(value.to_bytes(2, "little", signed=True) * samples)
    return buffer.getvalue()


class TestLocalSpeech(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.transcriber = LocalTranscriber()
        class TestHandler(Handler):
            app = SimpleNamespace(transcriber=cls.transcriber)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def request(self, method, path, data=None, content_type="audio/wav"):
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            conn.request(method, path, body=data, headers={"Content-Type": content_type})
            response = conn.getresponse()
            return response.status, json.load(response)
        finally:
            conn.close()

    def test_capture_requirements_before_decoding(self):
        valid = recording()
        for data, lang, mime, expected in [
            (valid, "en-US", "audio/webm", 415),
            (valid, "fr-FR", "audio/wav", 400),
            (valid[:-2], "en-US", "audio/wav", 400),
            (b"not audio", "en-US", "audio/wav", 400),
            (recording(rate=44100), "en-US", "audio/wav", 415),
            (recording(value=0), "en-US", "audio/wav", 422),
            (recording(samples=SAMPLE_RATE * MAX_SECONDS + 1), "en-US", "audio/wav", 413),
        ]:
            with self.subTest(lang=lang, mime=mime, expected=expected):
                status, result = self.request("POST", "/api/transcribe?lang=" + lang, data, mime)
                self.assertEqual(expected, status)
                self.assertTrue(result["error"])

    def test_missing_installation_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no-cli"
            with patch.object(self.transcriber, "cli", missing):
                status, info = self.request("GET", "/api/speech-status")
                self.assertEqual(200, status)
                self.assertFalse(info["available"])
                self.assertEqual(MAX_SECONDS, info["max_seconds"])
                status, info = self.request("POST", "/api/transcribe", recording())
                self.assertEqual(503, status)
                self.assertIn("executable", info["error"])

    def test_timeout_releases_decoder_and_retries_in_requested_language(self):
        result = subprocess.CompletedProcess([], 0, stdout="  由 Core D\n去 Block Z。\n".encode())
        with patch.object(self.transcriber, "model", Path("ggml-large-v3-turbo-q5_0.bin")), \
             patch.object(self.transcriber, "status", return_value={"available": True, "error": ""}), \
             patch("campus_nav.speech.subprocess.run",
                   side_effect=[subprocess.TimeoutExpired("cli", 90), result]) as run:
            wav = recording()
            status, payload = self.request("POST", "/api/transcribe?lang=zh-HK", wav)
            self.assertEqual(504, status)
            status, payload = self.request("POST", "/api/transcribe?lang=zh-HK", wav)
            self.assertEqual(200, status)
            self.assertEqual({"text": "由 Core D 去 Block Z。", "lang": "zh-HK"}, payload)
            args = run.call_args.args[0]
            self.assertEqual("yue", args[args.index("-l") + 1])
            self.assertEqual("-", args[args.index("-f") + 1])
            self.assertIn("-otxt", args)
            self.assertEqual("-", args[args.index("-of") + 1])
            self.assertEqual(wav, run.call_args.kwargs["input"])


    def test_small_model_override_uses_its_existing_chinese_token(self):
        output = subprocess.CompletedProcess([], 0, stdout=b"Core D")
        with patch.object(self.transcriber, "model", Path("ggml-small.bin")), \
             patch.object(self.transcriber, "status", return_value={"available": True, "error": ""}), \
             patch("campus_nav.speech.subprocess.run", return_value=output) as run:
            self.assertEqual("Core D", self.transcriber.transcribe(recording(), "zh-HK"))
            args = run.call_args.args[0]
            self.assertEqual("zh", args[args.index("-l") + 1])
