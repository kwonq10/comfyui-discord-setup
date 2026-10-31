"""[モック] エラー診断: 既知パターンの判定、マスク、原因不明時の扱い、文字コード。"""
import contextlib
import io
import os
import tempfile
import unittest

from helpers import FAKE_TOKEN
import diagnose as dg

CASES = {
    "login_failure": "discord.errors.LoginFailure: Improper token has been passed.",
    "privileged_intents": "discord.errors.PrivilegedIntentsRequired: Shard ID None is requesting privileged intents",
    "comfy_connect": "aiohttp.client_exceptions.ClientConnectorError: Cannot connect to host 127.0.0.1:8188",
    "interaction_expired": "discord.errors.NotFound: 404 Not Found (error code: 10015): Unknown Webhook / 401 Unauthorized (error code: 50027): Invalid Webhook Token",
    "cuda_oom": "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB",
    "ram_oom": "OSError: [WinError 1455] The paging file is too small for this operation to complete",
    "model_missing": "Value not in list: ckpt_name: 'missing.safetensors' not in ['a.safetensors']",
    "missing_access": "discord.errors.Forbidden: 403 Forbidden (error code: 50013): Missing Permissions",
    "config_encoding": "UnicodeDecodeError: 'cp932' codec can't decode byte 0x86",
    "file_too_large": "discord.errors.HTTPException: 413 Payload Too Large (error code: 40005): Request entity too large",
    "unknown_interaction": "discord.errors.NotFound: 404 Not Found (error code: 10062): Unknown interaction",
}


class PatternTest(unittest.TestCase):
    def test_each_known_error(self):
        for pid, line in CASES.items():
            r = dg.scan_log("noise\n" + line + "\nnoise")
            ids = [f["id"] for f in r["findings"]]
            self.assertIn(pid, ids, f"{pid}: {ids}")
            self.assertEqual(r["conclusion"], "known")

    def test_bare_401_is_low_confidence_only(self):
        r = dg.scan_log("discord.errors.HTTPException: 401 Unauthorized")
        self.assertEqual([f["id"] for f in r["findings"]], ["unauthorized_after_long_job"])
        self.assertEqual(r["findings"][0]["confidence"], "low")

    def test_unknown_error_needs_investigation(self):
        r = dg.scan_log("Traceback...\nSomethingWeird: totally new error")
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["conclusion"], "needs_investigation")
        self.assertIn("追加調査が必要", "\n".join(dg.human(r, None)))

    def test_markers(self):
        log = "Connected to ComfyUI\nRegistered 4 Discord commands:\nLogged in as Bot#1\nBot is ready!"
        m = dg.scan_log(log)["markers"]
        self.assertEqual(m, {"registered": "4", "ready": True, "comfy_connected": True, "logged_in": True})

    def test_examples_are_redacted_and_limited(self):
        log = "\n".join([f"LoginFailure token={FAKE_TOKEN}"] * 10)
        r = dg.scan_log(log)
        f = [x for x in r["findings"] if x["id"] == "login_failure"][0]
        self.assertEqual(f["count"], 10)
        self.assertEqual(len(f["examples"]), 3)
        self.assertNotIn(FAKE_TOKEN, str(r))

    def test_symptom_filter(self):
        r = dg.scan_log(CASES["cuda_oom"] + "\n" + CASES["comfy_connect"], symptom="vram")
        self.assertEqual([f["id"] for f in r["findings"]], ["cuda_oom"])

    def test_every_symptom_has_guide(self):
        for s in ["comfyui_unreachable", "bot_offline", "commands_missing", "workflow_not_found", "model_missing",
                  "generation_not_starting", "vram", "ram", "video_reply_failed"]:
            self.assertIn(s, dg.SYMPTOMS)
        self.assertIn("15分", dg.SYMPTOMS["video_reply_failed"]["known_limitation"])


class CliEncodingTest(unittest.TestCase):
    def _run(self, data: bytes):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "log.txt")
            with open(p, "wb") as f:
                f.write(data)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                dg.main(["--log", p])
            return buf.getvalue()

    def test_utf16_powershell_log(self):
        out = self._run(("Bot is ready!\r\n" + CASES["privileged_intents"]).encode("utf-16"))
        self.assertIn("Server Members Intent", out)

    def test_cp932_log(self):
        out = self._run(("エラー: " + CASES["comfy_connect"]).encode("cp932"))
        self.assertIn("ComfyUI が起動していない", out)


if __name__ == "__main__":
    unittest.main()
