"""[モック] 進捗の保存・再開と、秘密情報・個人情報を保存しないこと。"""
import contextlib
import io
import json
import os
import tempfile
import unittest

from helpers import FAKE_TOKEN, ROOT
import manage_progress as mp


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def run(path, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = mp.main(["--file", path, *args])
    return code, buf.getvalue()


class ProgressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "setup_progress.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_complete_resume(self):
        self.assertEqual(run(self.path, "init")[0], 0)
        for s in ["environment_check", "comfyui_detected", "prerequisites_ready",
                  "imagesmith_location_decided", "imagesmith_installed"]:
            self.assertEqual(run(self.path, "complete", s)[0], 0)
        p = mp.load(self.path)
        self.assertEqual(p["current_phase"], 3)
        self.assertEqual(p["pending_step"], "discord_app_created")
        # 「次回起動時」= ファイルから読み直して続きがわかる
        code, out = run(self.path, "show")
        self.assertIn("次にやること: Discordアプリの作成", out)
        self.assertIn("[済] Phase2 ImageSmithの導入", out)

    def test_init_does_not_overwrite(self):
        run(self.path, "init")
        run(self.path, "complete", "environment_check")
        self.assertEqual(run(self.path, "init")[0], 1)
        self.assertIn("environment_check", mp.load(self.path)["completed_steps"])

    def test_pending_waiting_for_user(self):
        run(self.path, "pending", "discord_bot_configuration", "--status", "waiting_for_user")
        p = mp.load(self.path)
        self.assertEqual((p["current_phase"], p["status"]), (3, "waiting_for_user"))

    def test_rejects_secrets_and_unknown(self):
        cases = [("fact", "comfyui_url", f"http://127.0.0.1:8188/?token={FAKE_TOKEN}"),
                 ("fact", "workflow_names", FAKE_TOKEN),
                 ("fact", "discord_token", "abc"),
                 ("fact", "discord_username", "yamada"),
                 ("fact", "comfyui_url", "https://comfy.example.com"),
                 ("fact", "imagesmith_dir", "https://discord.com/oauth2/authorize?client_id=1"),
                 ("complete", "not_a_step")]
        for c in cases:
            code, out = run(self.path, *c)
            self.assertEqual(code, 1, c)
        if os.path.exists(self.path):
            text = read(self.path)
            self.assertNotIn(FAKE_TOKEN, text)
            self.assertNotIn("yamada", text)

    def test_path_anonymized(self):
        home = os.path.expanduser("~")
        run(self.path, "fact", "imagesmith_dir", os.path.join(home, "comfy-discord-imagesmith"))
        p = mp.load(self.path)
        self.assertTrue(p["facts"]["imagesmith_dir"].startswith("%USERPROFILE%"))
        self.assertNotIn(os.path.basename(home), json.dumps(p))

    def test_tampered_file_with_secret_rejected(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"current_phase": 3, "completed_steps": [], "status": "in_progress",
                       "facts": {"workflow_names": FAKE_TOKEN}}, f)
        code, out = run(self.path, "show")
        self.assertEqual(code, 1)
        self.assertNotIn(FAKE_TOKEN, out)

    def test_extra_keys_dropped(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"current_phase": 2, "completed_steps": ["environment_check"], "status": "in_progress",
                       "token": "zzz", "user": "someone"}, f)
        run(self.path, "status", "blocked")
        text = read(self.path)
        self.assertNotIn("zzz", text)
        self.assertNotIn("someone", text)

    def test_reset_requires_confirm_and_backs_up(self):
        run(self.path, "init")
        self.assertEqual(run(self.path, "reset")[0], 3)
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(run(self.path, "reset", "--confirm")[0], 0)
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(any(n.endswith(".bak") for n in os.listdir(os.path.dirname(self.path))))

    def test_no_temp_files_left(self):
        run(self.path, "init")
        run(self.path, "complete", "environment_check")
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.path)) if n.endswith(".tmp")], [])

    def test_template_is_valid(self):
        with open(os.path.join(ROOT, "templates", "setup_progress.json"), encoding="utf-8") as f:
            p = mp.validate(json.load(f))
        self.assertEqual(p["pending_step"], "environment_check")

    def test_default_path_outside_skill(self):
        self.assertNotIn(os.path.normcase(ROOT), os.path.normcase(mp.default_path()))


if __name__ == "__main__":
    unittest.main()
