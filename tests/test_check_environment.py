"""[モック] Windows環境診断。外部コマンドはモック、トークンは偽物で漏洩防止を確認。"""
import contextlib
import io
import json
import os
import unittest
from unittest import mock

from helpers import FAKE_TOKEN
import check_environment as ce


def fake_runner(table):
    def run(args, timeout=15):
        key = args[0] if args[0] != "powershell" else "ps:" + args[-1][:30]
        for k, v in table.items():
            if key == k or (k.startswith("ps:") and key.startswith(k)):
                return v
        return (-1, "")
    return run


GOOD = {
    "nvidia-smi": (0, "NVIDIA GeForce RTX 3060, 12288, 1349, 580.10"),
    "py": (0, " -V:3.12 *        Python 3.12 (64-bit)\n -V:3.11          Python 3.11"),
    "python": (0, "Python 3.12.10"),
    "git": (0, "git version 2.53.0.windows.2"),
    "ps:(Get-CimInstance Win32_Processor": (0, "Intel(R) Core(TM) i7"),
    "ps:Get-CimInstance Win32_VideoCon": (0, "NVIDIA GeForce RTX 3060"),
}


class ParseTest(unittest.TestCase):
    def test_nvidia_smi(self):
        g = ce.parse_nvidia_smi("NVIDIA GeForce RTX 3060, 12288, 1349, 580.10\nbad line")
        self.assertEqual(g, [{"name": "NVIDIA GeForce RTX 3060", "vram_total_mib": 12288,
                              "vram_used_mib": 1349, "driver": "580.10"}])

    def test_py_launcher(self):
        self.assertEqual(ce.parse_py_launcher(" -V:3.12 *  Python 3.12\n -V:Astral/CPython3.11.15 x"),
                         ["3.12", "Astral/CPython3.11.15"])


class CollectTest(unittest.TestCase):
    def test_good_environment_has_no_needs(self):
        r = ce.collect(fake_runner(GOOD), secret_check=False)
        levels = {i["item"]: i["level"] for i in r["issues"]}
        self.assertNotIn("python", levels)
        self.assertNotIn("git", levels)
        self.assertNotIn("gpu", levels)
        self.assertEqual(r["gpu"]["nvidia"][0]["vram_total_mib"], 12288)

    def test_missing_tools_reported(self):
        r = ce.collect(fake_runner({}), secret_check=False)
        items = {i["item"] for i in r["issues"]}
        self.assertTrue({"python", "git", "gpu"} <= items)
        text = "\n".join(ce.human(r))
        self.assertIn("Python 3.12 が見つかりません", text)
        self.assertIn("Gitが見つかりません", text)

    def test_low_vram_and_ram_warn(self):
        r = {"windows": {"os": "Windows", "is_windows_11": True}, "ram": {"total_gb": 8.0},
             "gpu": {"nvidia": [{"vram_total_mib": 4096}]}, "python": {"has_3_12": True},
             "git": {"found": True}, "disks": [{"free_gb": 10.0}]}
        items = {i["item"] for i in ce.evaluate(r)}
        self.assertEqual(items, {"ram", "vram", "disk"})


class TokenLeakTest(unittest.TestCase):
    """DISCORD_TOKEN が設定されていても、値・長さ・先頭文字が出力に出ないこと。"""

    def _run_main(self, args):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"DISCORD_TOKEN": FAKE_TOKEN}), \
                mock.patch("winreg.OpenKey", side_effect=FileNotFoundError), \
                mock.patch.object(ce, "run_cmd", fake_runner(GOOD)), \
                contextlib.redirect_stdout(buf):
            ce.main(args)
        return buf.getvalue()

    def test_human_output_has_no_token(self):
        out = self._run_main([])
        self.assertNotIn(FAKE_TOKEN, out)
        self.assertNotIn(FAKE_TOKEN[:10], out)
        self.assertNotIn(FAKE_TOKEN[-10:], out)
        self.assertIn("値は表示しません", out)

    def test_json_output_has_no_token_only_booleans(self):
        out = self._run_main(["--json"])
        self.assertNotIn(FAKE_TOKEN, out)
        data = json.loads(out)
        self.assertEqual(data["discord_token"], {"in_this_process": True, "in_user_environment": False})

    def test_no_secret_check_skips(self):
        with mock.patch.object(ce, "discord_token_registered") as m, \
                mock.patch.object(ce, "run_cmd", fake_runner(GOOD)), contextlib.redirect_stdout(io.StringIO()):
            ce.main(["--no-secret-check"])
        m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
