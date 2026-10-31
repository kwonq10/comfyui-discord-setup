"""[モック] ImageSmith 導入支援: 既存環境の保護・承認フラグ・設定生成・バックアップ。
git clone / pip などネットワークや実環境を触る処理は呼ばない（--confirm なしの経路と、偽リポジトリで検証）。"""
import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from helpers import FAKE_TOKEN, SAMPLE_API_WORKFLOW
import setup_imagesmith as si

SPEC = {
    "comfy_url": "http://127.0.0.1:8188",
    "workflows": [{"name": "my_image", "type": "txt2img", "description": "Image", "file": "",
                   "prompt_node_id": "6", "seed_node_id": "3", "default": True,
                   "allowed_users": ["me"], "allowed_channels": ["comfyui-generation"]}],
}


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_main(args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = si.main(args)
    return code, buf.getvalue()


class TargetProtectionTest(unittest.TestCase):
    def test_nonempty_dir_is_conflict(self):
        with tempfile.TemporaryDirectory() as t:
            open(os.path.join(t, "keep.txt"), "w").close()
            info = si.inspect_target(t)
            self.assertFalse(info["ok"])
            with self.assertRaises(si.SetupError):
                si.do_install(t, si.PINNED_COMMIT)
            self.assertEqual(os.listdir(t), ["keep.txt"])

    def test_new_dir_ok(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertTrue(si.inspect_target(os.path.join(t, "new"))["ok"])

    def test_install_without_confirm_does_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            target = os.path.join(t, "new")
            with mock.patch.object(si, "run") as run, mock.patch.object(si, "do_install") as inst:
                code, out = run_main(["install", "--dir", target])
            self.assertEqual(code, 3)
            self.assertIn("未実行", out)
            run.assert_not_called()
            inst.assert_not_called()
            self.assertFalse(os.path.exists(target))


class RenderConfigTest(unittest.TestCase):
    def spec(self, **over):
        s = json.loads(json.dumps(SPEC))
        s["workflows"][0]["file"] = "C:/x/my_image_api.json"
        s["workflows"][0].update(over)
        return s

    def test_ascii_no_token_and_security(self):
        text = si.render_config(self.spec())
        text.encode("ascii")
        self.assertIn("SET_VIA_ENV_DISCORD_TOKEN", text)
        self.assertIn("enabled: true", text)
        self.assertIn('allowed_users: ["me"]', text)
        self.assertNotIn(FAKE_TOKEN, text)

    def test_parses_as_yaml_and_seed_code_runs(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("pyyaml なし（ImageSmith venv で実行すると検証される）")
        cfg = yaml.safe_load(si.render_config(self.spec()))
        w = cfg["workflows"]["my_image"]
        self.assertEqual(w["text_prompt_node_id"], "6")
        ns = {}
        exec(w["settings"][0]["code"], ns)
        wf = json.loads(json.dumps(SAMPLE_API_WORKFLOW))
        ns["__before"](wf)
        self.assertIsInstance(wf["3"]["inputs"]["seed"], int)

    def test_rejections(self):
        bad = [self.spec(allowed_users=[]), self.spec(allowed_channels=[]), self.spec(description="画像"),
               self.spec(allowed_users=["山田"]), self.spec(name="my image"), self.spec(type="video")]
        for s in bad:
            with self.assertRaises(si.SetupError):
                si.render_config(s)
        s = self.spec()
        s["comfy_url"] = "https://comfy.example.com"
        with self.assertRaises(si.UnsafeUrlError):
            si.render_config(s)


class MakeConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "ImageSmith")
        os.makedirs(self.repo)
        open(os.path.join(self.repo, "main.py"), "w").close()
        self.src = os.path.join(self.tmp.name, "my_image_api.json")
        with open(self.src, "w") as f:
            json.dump(SAMPLE_API_WORKFLOW, f)
        self.spec_path = os.path.join(self.tmp.name, "spec.json")
        s = json.loads(json.dumps(SPEC))
        s["workflows"][0]["file"] = self.src
        with open(self.spec_path, "w") as f:
            json.dump(s, f)
        self.cfg = os.path.join(self.repo, "configuration.yml")

    def tearDown(self):
        self.tmp.cleanup()

    def test_without_confirm_writes_nothing(self):
        code, out = run_main(["make-config", "--dir", self.tmp.name, "--spec", self.spec_path])
        self.assertEqual(code, 3)
        self.assertFalse(os.path.exists(self.cfg))
        self.assertIn("workflows:", out)

    def test_confirm_writes_and_copies_without_touching_source(self):
        before = sha(self.src)
        code, out = run_main(["make-config", "--dir", self.tmp.name, "--spec", self.spec_path, "--confirm"])
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(self.cfg))
        self.assertTrue(os.path.exists(os.path.join(self.repo, "workflows", "my_image_api.json")))
        self.assertEqual(sha(self.src), before)

    def test_existing_config_protected_then_backed_up(self):
        with open(self.cfg, "w") as f:
            f.write("original: true\n")
        code, out = run_main(["make-config", "--dir", self.tmp.name, "--spec", self.spec_path, "--confirm"])
        self.assertEqual(code, 1)
        self.assertIn("--overwrite", out)
        with open(self.cfg) as f:
            self.assertEqual(f.read(), "original: true\n")
        code, out = run_main(["make-config", "--dir", self.tmp.name, "--spec", self.spec_path, "--confirm", "--overwrite"])
        self.assertEqual(code, 0, out)
        baks = [n for n in os.listdir(self.repo) if n.startswith("configuration.yml.") and n.endswith(".bak")]
        self.assertEqual(len(baks), 1)
        with open(os.path.join(self.repo, baks[0])) as f:
            self.assertEqual(f.read(), "original: true\n")


SYNTH_SECURITY = (
    "class SecurityManager:\n    def check(self):\n        for x in y:\n            if z:\n"
    "                result = self.validate_settings_string(\n                    a\n                )\n\n"
    + si.SECURITY_BUG_OLD + "\n\n        return SecurityResult(True)\n"
)


class PatchSecurityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = os.path.join(self.tmp.name, "ImageSmith", "src", "core")
        os.makedirs(d)
        self.path = os.path.join(d, "security.py")
        with open(self.path, "w", newline="") as f:
            f.write(SYNTH_SECURITY)

    def tearDown(self):
        self.tmp.cleanup()

    def test_requires_confirm(self):
        code, out = run_main(["patch-security", "--dir", self.tmp.name])
        self.assertEqual(code, 3)
        with open(self.path, newline="") as f:
            self.assertEqual(f.read(), SYNTH_SECURITY)

    def test_patch_with_backup(self):
        code, out = run_main(["patch-security", "--dir", self.tmp.name, "--confirm"])
        self.assertEqual(code, 0, out)
        with open(self.path) as f:
            text = f.read()
        self.assertIn("if not result.state:", text)
        self.assertEqual(text.count("\n"), SYNTH_SECURITY.count("\n"))
        self.assertTrue(any(n.endswith(".bak") for n in os.listdir(os.path.dirname(self.path))))
        self.assertEqual(si.security_patch_status(self.tmp.name)["state"], "already_patched")

    def test_unknown_version_untouched(self):
        with open(self.path, "w") as f:
            f.write("something else\n")
        code, _ = run_main(["patch-security", "--dir", self.tmp.name, "--confirm"])
        self.assertEqual(code, 1)
        with open(self.path) as f:
            self.assertEqual(f.read(), "something else\n")


if __name__ == "__main__":
    unittest.main()
