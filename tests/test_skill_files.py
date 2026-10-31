"""[静的] Skillの構成・承認要件・秘密情報の混入がないことを確認する。"""
import os
import re
import unittest

from helpers import FAKE_TOKEN, ROOT
import _common as c

TEXT_EXT = (".md", ".py", ".json", ".txt", ".yml", ".yaml", ".template", ".ps1")


def all_text_files():
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in ("__pycache__", ".git")]
        for f in fn:
            if f.endswith(TEXT_EXT) or f in (".gitignore",):
                yield os.path.join(dp, f)


class SkillMdTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
            self.text = f.read()

    def test_frontmatter(self):
        self.assertTrue(self.text.startswith("---\n"))
        fm = self.text.split("---\n")[1]
        self.assertRegex(fm, r"(?m)^name: comfyui-discord-setup$")
        self.assertRegex(fm, r"(?m)^description: .+")
        desc = re.search(r"(?m)^description: (.+)$", fm).group(1)
        when = re.search(r"(?m)^when_to_use: (.+)$", fm).group(1)
        self.assertLessEqual(len(desc) + len(when), 1536)

    def test_length(self):
        self.assertLess(self.text.count("\n"), 500)

    def test_linked_files_exist(self):
        for rel in re.findall(r"\]\(((?:references|scripts|templates)/[^)]+)\)", self.text):
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), rel)
        for script in re.findall(r"scripts/(\w+\.py)", self.text):
            self.assertTrue(os.path.exists(os.path.join(ROOT, "scripts", script)), script)

    def test_required_rules_present(self):
        for phrase in ["1回の案内で1つの操作だけ", "トークンをチャットに貼らせない", "インターネットへ公開しない",
                       "承認", "推測で設定を変更しない", "勝手に次の Phase へ進まない", "Interaction Token",
                       "独自プラグイン"]:
            self.assertIn(phrase, self.text)

    def test_all_seven_phases(self):
        for n in range(1, 8):
            self.assertIn(f"## Phase {n}", self.text)


class ApprovalGateTest(unittest.TestCase):
    def test_mutating_commands_require_confirm(self):
        with open(os.path.join(ROOT, "scripts", "setup_imagesmith.py"), encoding="utf-8") as f:
            src = f.read()
        for sub in ("install", "make-config", "patch-security"):
            self.assertIn(f'"{sub}"', src)
        self.assertEqual(src.count("if not a.confirm:"), 3)

    def test_no_generation_or_free_requests_in_scripts(self):
        """スクリプトから ComfyUI へ生成(/prompt)・解放(/free)・POST を送らない。"""
        for name in os.listdir(os.path.join(ROOT, "scripts")):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(ROOT, "scripts", name), encoding="utf-8") as f:
                src = f.read()
            self.assertNotRegex(src, r'["\']/prompt["\']', name)
            self.assertNotRegex(src, r'["\']/free["\']', name)
            self.assertNotIn('method="POST"', src, name)


class NoSecretsTest(unittest.TestCase):
    def test_no_token_like_strings_outside_test_fixture(self):
        for path in all_text_files():
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read().replace(FAKE_TOKEN, "")
            hits = c._DISCORD_TOKEN.findall(text)
            self.assertEqual(hits, [], path)

    def test_gitignore_covers_secrets(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
            gi = f.read()
        for pat in (".env", "configuration.yml", "setup_progress.json", "*.log"):
            self.assertIn(pat, gi)

    def test_no_bundled_upstream_code(self):
        """ImageSmith のソースを同梱していない（ライセンス未確定のため再配布しない）。"""
        for path in all_text_files():
            self.assertNotIn(os.sep + "src" + os.sep + "bot", path)
            self.assertNotEqual(os.path.basename(path), "imagesmith.py")


if __name__ == "__main__":
    unittest.main()
