"""[モック・静的] Phase 3 検証で見つかった問題の回帰テスト。

既存Bot再利用ルート / Reset Token の安全対策 / 同意の要否 / 画面確認と記録の区別 /
4014・Disallowed intent の検出 / 設定ファイル無変更
"""
import contextlib
import hashlib
import io
import json
import os
import re
import tempfile
import unittest
from unittest import mock

from helpers import FAKE_TOKEN, ROOT
from test_phase1_improvements import OpenGuard, TrackingEnviron
import check_bot_reuse as cb
import check_environment as ce
import diagnose as dg
import manage_progress as mp

REUSE = os.path.join(ROOT, "references", "discord_reuse.md")
SETUP = os.path.join(ROOT, "references", "discord_setup.md")
TROUBLE = os.path.join(ROOT, "references", "troubleshooting.md")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def run(args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cb.main(args)
    return code, buf.getvalue()


def phase3_section(name):
    text = read(os.path.join(ROOT, "SKILL.md"))
    start = text.index("## Phase 3")
    return text[start:text.index("## Phase 4")]


# --- 既存Bot再利用ルート ----------------------------------------------------------------

class ReuseRouteTest(unittest.TestCase):
    def setUp(self):
        self.reuse = read(REUSE)
        self.skill3 = phase3_section("SKILL.md")

    def test_skill_asks_user_to_choose_route_first(self):
        self.assertIn("最初にユーザーに選んでもらう", self.skill3)
        self.assertIn("既存の Discord Bot を再利用する", self.skill3)
        self.assertIn("新しい Discord Bot を作成する", self.skill3)
        a = self.skill3.index("### 3-A")
        b = self.skill3.index("### 3-B")
        self.assertLess(a, b)
        self.assertIn("discord_reuse.md", self.skill3[a:b])
        self.assertIn("新規作成の手順へは進まない", self.skill3[a:b])

    def test_reuse_guide_never_instructs_creation(self):
        for forbidden in ("Create App", "URL Generator", "Generated URL", "「サーバーに追加」", "「Copy」"):
            self.assertNotIn(forbidden, self.reuse, forbidden)
        self.assertIn("新規作成の手順（discord_setup.md）へは進まない", self.reuse)

    def test_banner_exact_text(self):
        banner = "今回は既存Botの確認だけを行います。設定を変更する必要はありません。Reset TokenやSave Changesは押さないでください。"
        self.assertEqual(cb.BANNER, banner)
        self.assertIn(banner, self.reuse)
        self.assertIn(banner, self.skill3)
        code, out = run(["checklist"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("【注意】" + banner))
        self.assertNotIn("Create App", out)

    def test_each_r_step_has_five_parts_and_is_look_only(self):
        for n in range(1, 6):
            m = re.search(rf"## R{n}\..*?(?=\n## )", self.reuse, re.S)
            self.assertIsNotNone(m, n)
            sec = m.group(0)
            for part in ("1. **開", "2. **クリックする場所**", "3. **入力する内容**", "4. **", "5. **完了後**"):
                self.assertIn(part, sec, (n, part))
            self.assertRegex(sec, r"見るだけ|なし")
        for n in (6, 7):
            self.assertIn(f"## R{n}.", self.reuse)

    def test_new_bot_guide_still_present(self):
        setup = read(SETUP)
        for d in range(1, 10):
            self.assertIn(f"## D{d}.", setup)
        self.assertIn("discord_reuse.md", setup)   # 既存Botなら再利用ガイドへ誘導


class ResetTokenSafetyTest(unittest.TestCase):
    def test_reset_token_only_appears_as_prohibition_or_separate_step(self):
        text = read(REUSE)
        separate = text.index("## トークンの再発行について")
        for m in re.finditer("Reset Token", text[:separate]):
            line = text[:separate][text.rfind("\n", 0, m.start()) + 1: text.find("\n", m.end())]
            self.assertRegex(line, r"押さない|押させない|案内しない|触らない", line)
        tail = text[separate:]
        for must in ("別工程", "理由", "影響", "再登録", "承認"):
            self.assertIn(must, tail)

    def test_skill_reuse_section_requires_separate_approval_for_reset(self):
        sec = phase3_section("SKILL.md")
        a, b = sec.index("### 3-A"), sec.index("### 3-B")
        reuse = sec[a:b]
        self.assertIn("Reset Token は案内しない", reuse)
        self.assertIn("トークンの再発行は別工程", reuse)
        self.assertIn("自動で勧めない", reuse)

    def test_token_missing_message_does_not_suggest_reset(self):
        with mock.patch.object(cb, "token_presence", return_value={"in_user_environment": False, "in_this_process": False}):
            code, out = run(["token-presence", "--consent-token-presence"])
        self.assertEqual(code, 0)
        self.assertIn("Reset Token を案内しません", out)

    def test_diagnose_intents_action_requires_approval(self):
        p = [x for x in dg.PATTERNS if x["id"] == "privileged_intents"][0]
        self.assertIn("承認", p["action"])


# --- 同意の要否 ---------------------------------------------------------------------------

class ConsentTest(unittest.TestCase):
    def test_token_presence_requires_consent_and_normal_mode(self):
        env = TrackingEnviron(os.environ)
        env["DISCORD_TOKEN"] = FAKE_TOKEN

        def reg_guard(*a, **k):
            raise AssertionError("レジストリを読んだ")

        with mock.patch.object(os, "environ", env), mock.patch("winreg.OpenKey", reg_guard):
            self.assertEqual(run(["token-presence"])[0], 5)
            self.assertEqual(run(["token-presence", "--read-only", "--consent-token-presence"])[0], 4)
        self.assertNotIn("DISCORD_TOKEN", env.touched)

    def test_token_presence_with_consent_shows_presence_only(self):
        env = TrackingEnviron(os.environ)
        env["DISCORD_TOKEN"] = FAKE_TOKEN
        with mock.patch.object(os, "environ", env), mock.patch("winreg.OpenKey", side_effect=FileNotFoundError):
            code, out = run(["token-presence", "--consent-token-presence"])
        self.assertEqual(code, 0)
        self.assertNotIn(FAKE_TOKEN, out)
        self.assertNotIn(FAKE_TOKEN[:8], out)
        self.assertIn("未登録", out)   # ユーザー環境変数（レジストリ）には無い扱い

    def test_access_limits_requires_consent_without_opening(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = os.path.join(t, "configuration.yml")
            with open(cfg, "w") as f:
                f.write(f"discord:\n  token: {FAKE_TOKEN}\n")
            with mock.patch("builtins.open", OpenGuard()):
                code, out = run(["access-limits", "--file", cfg])
            self.assertEqual(code, 5)
            self.assertNotIn(FAKE_TOKEN, out)


# --- R7 の出力と設定ファイル無変更 ----------------------------------------------------------

CONFIG = f"""discord:
  token: "{FAKE_TOKEN}"
comfyui:
  instances:
    - url: 'http://127.0.0.1:8188'
      auth:
        api_key: sk-secretvalue-123456
workflows:
  flux3_klein:
    type: txt2img
    security:
      enabled: true
      allowed_users: [private_person_a]
      allowed_roles: []
      allowed_channels: [comfyui-generation]
  open_wf:
    type: txt2img
"""


class AccessLimitsTest(unittest.TestCase):
    def setUp(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("pyyaml なし（ImageSmith venv で実行すると検証される）")
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(self.tmp.name, "configuration.yml")
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write(CONFIG)

    def tearDown(self):
        self.tmp.cleanup()

    def state(self):
        with open(self.cfg, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest(), os.stat(self.cfg).st_mtime_ns

    def test_counts_and_channels_only_and_file_unchanged(self):
        before = self.state()
        code, out = run(["access-limits", "--file", self.cfg, "--consent-read-config"])
        self.assertEqual(code, 0, out)
        self.assertIn("flux3_klein: 制限あり / 許可ユーザー 1人 / 許可チャンネル comfyui-generation", out)
        self.assertIn("open_wf: 制限なし", out)
        for leak in (FAKE_TOKEN, "secretvalue", "private_person_a", "127.0.0.1"):
            self.assertNotIn(leak, out)
        code, out = run(["access-limits", "--file", self.cfg, "--consent-read-config", "--expect-user", "private_person_a"])
        self.assertIn("含まれています", out)
        self.assertNotIn("private_person_a", out)
        self.assertEqual(self.state(), before)

    def test_broken_config_generic_error(self):
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write(f"discord:\n  token: {FAKE_TOKEN}\n  bad: [unclosed\n")
        code, out = run(["access-limits", "--file", self.cfg, "--consent-read-config"])
        self.assertEqual(code, 1)
        self.assertNotIn(FAKE_TOKEN, out)
        self.assertIn("中身は表示していません", out)


# --- 画面確認と記録の区別 -------------------------------------------------------------------

class ClassificationTest(unittest.TestCase):
    def test_record_only_is_not_confirmed(self):
        s = cb.summarize({f"r{i}": "record_only" for i in range(1, 8)})
        self.assertFalse(s["ready"])
        self.assertIn("記録だけで「確認済み」にはしません", s["verdict"])
        self.assertNotIn("新規作成は不要です。必要な設定を確認した", s["verdict"])

    def test_all_screen_confirmed_gives_reuse_verdict(self):
        s = cb.summarize({f"r{i}": "screen_confirmed" for i in range(1, 6)})
        self.assertTrue(s["ready"])
        self.assertIn("既存のDiscord Botが利用できるため、新規作成は不要です", s["verdict"])
        self.assertEqual(s["results"]["r6"], "unconfirmed")   # 同意なしなら R6/R7 は未確認のまま

    def test_mismatch_blocks_and_is_not_fixed(self):
        s = cb.summarize({"r1": "screen_confirmed", "r2": "mismatch", "r3": "screen_confirmed",
                          "r4": "screen_confirmed", "r5": "screen_confirmed"})
        self.assertFalse(s["ready"])
        self.assertIn("ここでは直しません", s["verdict"])

    def test_labels_and_invalid_value(self):
        code, out = run(["summary", "--r1", "screen_confirmed", "--r2", "record_only", "--r3", "mismatch"])
        self.assertIn("画面で確認済み", out)
        self.assertIn("記録のみ", out)
        self.assertIn("不一致", out)
        self.assertIn("未確認", out)
        with self.assertRaises(ValueError):
            cb.summarize({"r1": "ok"})

    def test_progress_accepts_only_four_categories(self):
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "p.json")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mp.main(["--file", path, "fact", "bot_check_r2", "screen_confirmed"]), 0)
                self.assertEqual(mp.main(["--file", path, "fact", "discord_bot_source", "reused"]), 0)
                self.assertEqual(mp.main(["--file", path, "fact", "bot_check_r2", "ON"]), 1)
                self.assertEqual(mp.main(["--file", path, "fact", "bot_check_r8", "screen_confirmed"]), 1)
                self.assertEqual(mp.main(["--file", path, "fact", "discord_bot_name", "My ComfyUI Bot"]), 1)
            facts = mp.load(path)["facts"]
            self.assertEqual(facts, {"bot_check_r2": "screen_confirmed", "discord_bot_source": "reused"})

    def test_read_only_summary_writes_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "p.json")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mp.main(["--file", path, "--read-only", "fact", "bot_check_r1", "screen_confirmed"]), 4)
                run(["summary", "--read-only", "--r1", "screen_confirmed"])
            self.assertEqual(os.listdir(t), [])


# --- 4014 / Disallowed intent -------------------------------------------------------------

class IntentDiagnoseTest(unittest.TestCase):
    def ids(self, line):
        return [f["id"] for f in dg.scan_log(line)["findings"]]

    def test_detects_all_forms(self):
        for line in ("discord.errors.PrivilegedIntentsRequired: Shard ID None is requesting privileged intents",
                     "websockets.exceptions.ConnectionClosedError: received 4014 (private use) Disallowed intent(s)",
                     "Gateway closed with code 4014",
                     "ConnectionClosed: Shard ID None WebSocket closed with 4014",
                     "close code: Disallowed intents"):
            self.assertIn("privileged_intents", self.ids(line), line)

    def test_no_false_positive_on_other_numbers(self):
        self.assertNotIn("privileged_intents", self.ids("listening on port 40140 and 14014"))


# --- 公式仕様の記載 ------------------------------------------------------------------------

class OfficialSpecDocsTest(unittest.TestCase):
    def test_setup_and_troubleshooting_dated_and_accurate(self):
        setup, trouble = read(SETUP), read(TROUBLE)
        for text in (setup, trouble):
            self.assertIn("確認日 2026-10-29", text.replace("：", " ").replace("確認日：", "確認日 "))
            self.assertIn("4014", text)
        self.assertIn("`bot` スコープに既定で含まれる", setup)
        self.assertIn("Disallowed intent", setup)
        self.assertIn("15分", setup)
        self.assertIn("15分", trouble)
        self.assertIn("`bot` スコープに既定で含まれる", trouble)

    def test_unverified_permissions_not_called_required(self):
        setup = read(SETUP)
        for line in setup.splitlines():
            if any(p in line for p in ("Read Message History", "Embed Links", "Attach Files", "推奨する次の5つ")):
                self.assertNotIn("必須", line, line)
        self.assertIn("個々の必要性は実機で検証していない", setup)


if __name__ == "__main__":
    unittest.main()
