"""[モック] Phase 2 実機検証で見つかった6項目の回帰テスト。

1 --target-dir / 2 読み取り専用での選択保持 / 3 ショートカットのリンク先判定 /
4 登録済みワークフロー名の安全な取得 / 5 生成前のRAM警告 / 6 不完全な環境の表示
実機を使うテストは tests/live/ にある（このファイルはすべて一時フォルダとモック）。
"""
import contextlib
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from helpers import FAKE_TOKEN, ROOT, SCRIPTS
from test_phase1_improvements import OpenGuard, TrackingEnviron, make_install, tree_snapshot
import check_connection as cc
import check_environment as ce
import check_shortcuts as cs
import detect_imagesmith as di
import manage_progress as mp


def no_search(*a, **k):
    raise AssertionError("指定先以外を検索した")


def run(fn, args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fn(args)
    return code, buf.getvalue()


# --- 1. --target-dir --------------------------------------------------------------

class TargetDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.good = os.path.join(self.tmp.name, "good")
        self.other = os.path.join(self.tmp.name, "comfy-discord-imagesmith")
        make_install(self.good)
        make_install(self.other)  # 既定名の別環境（指定時には絶対に出てこないこと）

    def tearDown(self):
        self.tmp.cleanup()

    def target(self, path):
        with mock.patch.object(di, "candidate_dirs", no_search), mock.patch.object(di, "find_installs", no_search):
            return di.inspect_target(path, git_runner=lambda r: "", check_processes=False)

    def test_valid_target_reuse_without_searching(self):
        res = self.target(self.good)
        self.assertEqual(res["recommendation"]["action"], "reuse")
        self.assertEqual(res["recommendation"]["target"], self.good)
        self.assertEqual([i["base"] for i in res["installations"]], [self.good])
        self.assertIn("既存環境を再利用できます", res["recommendation"]["message"])
        self.assertIn("新しいインストールは不要", res["recommendation"]["message"])

    def test_repo_subfolder_is_accepted(self):
        res = self.target(os.path.join(self.good, "ImageSmith"))
        self.assertEqual(res["recommendation"]["action"], "reuse")

    def test_invalid_targets_never_switch(self):
        broken = os.path.join(self.tmp.name, "broken")
        make_install(broken, venv=False)            # 設定はあるが venv なし
        source = os.path.join(self.tmp.name, "source")
        make_install(source, venv=False, config=False)
        plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(plain)
        cases = {os.path.join(self.tmp.name, "missing"): "target_invalid", plain: "target_invalid",
                 broken: "target_not_usable", source: "target_not_usable"}
        for path, action in cases.items():
            rec = self.target(path)["recommendation"]
            self.assertEqual(rec["action"], action, path)
            self.assertNotIn(self.other, json.dumps(rec))
            self.assertNotIn(self.good, json.dumps(rec))
            self.assertIn("切り替えません", rec["message"])

    def test_cli_target_only_and_conflicting_options(self):
        with mock.patch.object(di, "candidate_dirs", no_search), mock.patch.object(di, "list_processes", lambda r=None: []):
            code, out = run(di.main, ["--target-dir", self.good, "--no-shortcuts"])
        self.assertEqual(code, 0)
        self.assertIn("既存環境を再利用できます", out)
        self.assertIn("この会話で使う ImageSmith", out)
        self.assertNotIn("comfy-discord-imagesmith", out)
        code, out = run(di.main, ["--target-dir", self.good, "--dir", self.other])
        self.assertEqual(code, 2)

    def test_cli_invalid_target_exit_code(self):
        with mock.patch.object(di, "candidate_dirs", no_search):
            code, out = run(di.main, ["--target-dir", os.path.join(self.tmp.name, "missing"), "--no-shortcuts"])
        self.assertEqual(code, 1)
        self.assertNotIn("再利用できます", out)


# --- 2. 読み取り専用での選択保持 --------------------------------------------------------

class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = os.path.join(self.tmp.name, "env")
        make_install(self.env)  # configuration.yml に FAKE_TOKEN を含む
        self.prog = os.path.join(self.tmp.name, "state", "setup_progress.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_only_keeps_choice_in_conversation_only(self):
        with mock.patch("builtins.open", OpenGuard()):
            code, out = run(mp.main, ["--file", self.prog, "--read-only", "select-imagesmith", self.env])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.dirname(self.prog)))
        self.assertIn("次の会話には引き継がれません", out)
        self.assertIn(f'--target-dir "{self.env}"', out)

    def test_normal_mode_records_progress_without_secrets(self):
        code, out = run(mp.main, ["--file", self.prog, "select-imagesmith", self.env])
        self.assertEqual(code, 0, out)
        p = mp.load(self.prog)
        self.assertEqual(p["facts"]["imagesmith_source"], "reused")
        self.assertEqual(p["facts"]["imagesmith_commit"], di.PINNED_COMMIT)
        self.assertIn("imagesmith_installed", p["completed_steps"])
        self.assertIn("imagesmith_location_decided", p["completed_steps"])
        # Phase 1 が未記録の新しい進捗なので、現在地は最も手前の未完了工程（Phase 1）のまま
        self.assertEqual(p["pending_step"], "environment_check")
        with open(self.prog, encoding="utf-8") as f:
            self.assertNotIn(FAKE_TOKEN, f.read())

    def test_invalid_selection_rejected(self):
        code, out = run(mp.main, ["--file", self.prog, "select-imagesmith", os.path.join(self.tmp.name, "nope")])
        self.assertEqual(code, 1)
        self.assertIn("未選択", out)
        self.assertFalse(os.path.exists(self.prog))

    def test_skill_md_documents_session_scope(self):
        with open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("次の会話には引き継がれない", text)
        self.assertIn("--target-dir", text)


# --- 3. ショートカット ----------------------------------------------------------------

def make_lnk(target="", workdir="", args="", env_target=None):
    """テスト用の .lnk（MS-SHLLINK、Unicode）を作る。"""
    flags = 0x80 | (0x10 if workdir else 0) | (0x20 if args else 0)
    body = b""
    if target:
        flags |= 0x2
        vol = struct.pack("<IIII", 0x11, 3, 0, 0x10) + b"\0"
        hsize = 0x24
        ansi = b"\0"
        uni_base = target.encode("utf-16-le") + b"\0\0"
        uni_suffix = b"\0\0"
        vol_off = hsize
        base_off = vol_off + len(vol)
        suffix_off = base_off + len(ansi)
        ubase_off = suffix_off + len(ansi)
        usuffix_off = ubase_off + len(uni_base)
        size = usuffix_off + len(uni_suffix)
        body += struct.pack("<IIIIIIIII", size, hsize, 1, vol_off, base_off, 0, suffix_off, ubase_off, usuffix_off)
        body += vol + ansi + ansi + uni_base + uni_suffix
    for s in (workdir, args):
        if s:
            body += struct.pack("<H", len(s)) + s.encode("utf-16-le")
    if env_target:
        blk = struct.pack("<II", 8 + 260 + 520, 0xA0000001) + b"\0" * 260
        u = env_target.encode("utf-16-le")
        blk += u + b"\0" * (520 - len(u))
        body += blk
    body += b"\0\0\0\0"
    header = struct.pack("<I", 0x4C) + b"\x01\x14\x02\0\0\0\0\0\xc0\0\0\0\0\0\0\x46" + struct.pack("<I", flags)
    header += b"\0" * (0x4C - len(header))
    return header + body


class LnkParseTest(unittest.TestCase):
    def test_roundtrip(self):
        info = cs.parse_lnk(make_lnk(r"C:\Windows\powershell.exe", r"D:\env\ImageSmith", '-File "D:\\env\\launcher\\start.ps1"'))
        self.assertEqual(info, {"target": r"C:\Windows\powershell.exe", "workdir": r"D:\env\ImageSmith",
                                "args": '-File "D:\\env\\launcher\\start.ps1"'})

    def test_env_block_target(self):
        info = cs.parse_lnk(make_lnk(env_target=r"%USERPROFILE%\x\run.bat"))
        self.assertEqual(info["target"], r"%USERPROFILE%\x\run.bat")

    def test_garbage_rejected(self):
        for data in (b"", b"hello", b"\x4c\0\0\0" + b"\0" * 10):
            with self.assertRaises(cs.LnkError):
                cs.parse_lnk(data)

    def test_read_shortcuts_from_folder(self):
        with tempfile.TemporaryDirectory() as t:
            with open(os.path.join(t, "a.lnk"), "wb") as f:
                f.write(make_lnk(r"C:\x.exe"))
            with open(os.path.join(t, "broken.lnk"), "wb") as f:
                f.write(b"nope")
            open(os.path.join(t, "note.txt"), "w").close()
            res = cs.read_shortcuts([t])
        self.assertEqual([r["name"] for r in res], ["a.lnk", "broken.lnk"])
        self.assertTrue(res[1]["unreadable"])


class ShortcutClassifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.a, self.b = os.path.join(t, "envA"), os.path.join(t, "envB")
        make_install(self.a, launcher=True)
        make_install(self.b)
        self.ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    def tearDown(self):
        self.tmp.cleanup()

    def script(self, name, text):
        p = os.path.join(self.tmp.name, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def check(self, shortcuts):
        def forbid(*a, **k):
            raise AssertionError("外部プロセスを起動した")
        with mock.patch.object(subprocess, "run", forbid), mock.patch.object(subprocess, "Popen", forbid), \
                mock.patch.object(os, "startfile", forbid, create=True):
            return cs.check(reader=lambda: shortcuts, resolve=di.resolve_install)

    def test_launcher_in_env_and_display_name_ignored(self):
        start = os.path.join(self.a, "launcher", "start.ps1")
        # 名前は「envB」を名乗るが、実際のリンク先は envA の launcher
        [r] = self.check([{"name": "ImageSmith envB 起動.lnk", "target": self.ps, "args": f'-File "{start}"', "workdir": ""}])
        self.assertEqual(r["env"], self.a)
        self.assertEqual(r["basis"], "リンク先・作業フォルダ")

    def test_python_venv_target(self):
        exe = os.path.join(self.b, ".venv", "Scripts", "python.exe")
        [r] = self.check([{"name": "x.lnk", "target": exe, "args": "main.py", "workdir": ""}])
        self.assertEqual(r["env"], self.b)

    def test_batch_with_path_inside(self):
        bat = self.script("run.bat", f'@echo off\r\ncd /d "{self.b}\\ImageSmith"\r\n"{self.b}\\.venv\\Scripts\\python.exe" main.py\r\n')
        [r] = self.check([{"name": "go.lnk", "target": bat, "args": "", "workdir": ""}])
        self.assertEqual(r["env"], self.b)
        self.assertEqual(r["basis"], "スクリプト内のパス")

    def test_comment_paths_ignored_and_dot_source_followed(self):
        os.makedirs(os.path.join(self.tmp.name, "tools"))
        helper = os.path.join(self.tmp.name, "tools", "common.ps1")
        with open(helper, "w", encoding="utf-8") as f:
            f.write(f'# old: {self.a}\n$Root = "{self.b}"\n')
        main_ps = os.path.join(self.tmp.name, "tools", "start.ps1")
        with open(main_ps, "w", encoding="utf-8") as f:
            f.write(f'# previously {self.a}\n. "$PSScriptRoot\\common.ps1"\n')
        [r] = self.check([{"name": "Bot.lnk", "target": self.ps, "args": f'-File "{main_ps}"', "workdir": ""}])
        self.assertEqual(r["env"], self.b)

    def test_ambiguous_and_unknown(self):
        both = self.script("both.ps1", f'& "{self.a}\\x.ps1"\n& "{self.b}\\y.ps1"\n')
        none = self.script("none.ps1", 'Write-Host hi\n')
        res = self.check([{"name": "comfy both.lnk", "target": self.ps, "args": f'-File "{both}"', "workdir": ""},
                          {"name": "comfy none.lnk", "target": self.ps, "args": f'-File "{none}"', "workdir": ""}])
        self.assertEqual([r["state"] for r in res], ["不明（複数の環境を参照）", "不明"])
        self.assertTrue(all(r["env"] is None for r in res))
        out = "\n".join(cs.human(res))
        self.assertIn("判定できませんでした", out)

    def test_unrelated_apps_excluded(self):
        res = self.check([{"name": "Discord.lnk", "target": r"C:\Users\x\Discord\Update.exe", "args": "--processStart Discord.exe", "workdir": ""},
                          {"name": "Notes.lnk", "target": self.ps, "args": "", "workdir": ""}])
        self.assertEqual(res, [])

    def test_script_secrets_never_output(self):
        s = self.script("secret.ps1", f'$env:DISCORD_TOKEN = "{FAKE_TOKEN}"\n& "{self.b}\\.venv\\Scripts\\python.exe" main.py\n')
        res = self.check([{"name": "bot.lnk", "target": self.ps, "args": f'-File "{s}" -Token {FAKE_TOKEN}', "workdir": ""}])
        text = json.dumps(res) + "\n".join(cs.human(res, True))
        self.assertNotIn(FAKE_TOKEN, text)
        self.assertNotIn("-Token", text)
        self.assertEqual(res[0]["env"], self.b)

    def test_attach_to_installs(self):
        with mock.patch.object(di, "candidate_dirs", return_value=[self.a, self.b]):
            installs = di.find_installs(git_runner=lambda r: "", proc_runner=lambda: [])
        start = os.path.join(self.a, "launcher", "start.ps1")
        sc = self.check([{"name": "起動.lnk", "target": self.ps, "args": f'-File "{start}"', "workdir": ""}])
        left = di.attach_shortcuts(installs, sc)
        by = {i["base"]: i for i in installs}
        self.assertEqual(by[self.a]["shortcuts"], ["起動"])
        self.assertEqual(by[self.b]["shortcuts"], [])
        self.assertEqual(left, [])
        out = "\n".join(di.human(installs, di.recommend(installs), False))
        self.assertIn("「起動」 がこの環境を使います（リンク先で確認）", out)
        self.assertIn("この環境を使うものはありません", out)
        self.assertNotIn("可能性", out)


# --- 4. 登録済みワークフロー名 -----------------------------------------------------------

GOOD_CONFIG = f"""language: "ja"
discord:
  token: "{FAKE_TOKEN}"
comfyui:
  instances:
    - url: 'http://127.0.0.1:8188'
      auth:
        api_key: sk-secretvalue-123456
workflows:
  # comment
  flux3_klein:
    type: txt2img
    settings:
      - name: __before
        code: |
          def __before(workflowjson):
              x = "secretvalue-in-code"
  minimax_h3:   # video
    type: txt2img
    security:
      allowed_users: [someone_private]
memory_tools:
  auto_free:
    enabled: true
"""


class WorkflowNamesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(self.tmp.name, "configuration.yml")
        self.write(GOOD_CONFIG)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text):
        with open(self.cfg, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def test_requires_explicit_consent(self):
        for sub in ("workflow-names", "config"):
            with mock.patch("builtins.open", OpenGuard()):
                code, out = run(cc.main, [sub, "--file", self.cfg, "--read-only"])
            self.assertEqual(code, 5)
            self.assertIn("--consent-read-config", out)
            self.assertNotIn(FAKE_TOKEN, out)

    def test_names_only(self):
        before = (hashlib.sha256(open(self.cfg, "rb").read()).hexdigest(), os.stat(self.cfg).st_mtime_ns)
        code, out = run(cc.main, ["workflow-names", "--file", self.cfg, "--consent-read-config"])
        self.assertEqual(code, 0, out)
        self.assertIn("- flux3_klein", out)
        self.assertIn("- minimax_h3", out)
        for leak in (FAKE_TOKEN, "secretvalue", "someone_private", "txt2img", "127.0.0.1", "memory_tools", "__before"):
            self.assertNotIn(leak, out)
        code, out = run(cc.main, ["workflow-names", "--file", self.cfg, "--consent-read-config", "--json"])
        self.assertEqual(json.loads(out), {"workflow_names": ["flux3_klein", "minimax_h3"]})
        after = (hashlib.sha256(open(self.cfg, "rb").read()).hexdigest(), os.stat(self.cfg).st_mtime_ns)
        self.assertEqual(before, after)

    def test_unsafe_layouts_abort_without_content(self):
        bad = {
            "tabs": "workflows:\n\tflux:\n",
            "flow": "workflows: {flux: {type: x}}\n",
            "quoted": 'workflows:\n  "flux": {}\n',
            "twice": "workflows:\n  a:\n    x: 1\nworkflows:\n  b:\n",
            "secret_name": f"workflows:\n  {FAKE_TOKEN.replace('.', '_')}:\n    type: x\n",
            "random_name": "workflows:\n  a1b2c3d4e5f6g7h8i9j0k1l2m3:\n    type: x\n",
            "merge_key": "workflows:\n  <<: *defaults\n",
            "value_inline": "workflows:\n  flux: secretvalue\n",
            "indent": "workflows:\n    flux:\n      type: x\n  odd:\n",
            "none": "discord:\n  token: secretvalue\n",
            "docs": "workflows:\n  a:\n---\nsecretvalue: 1\n",
        }
        for label, text in bad.items():
            self.write(text)
            code, out = run(cc.main, ["workflow-names", "--file", self.cfg, "--consent-read-config"])
            self.assertEqual(code, 1, label)
            self.assertIn("中止", out, label)
            for leak in ("secretvalue", FAKE_TOKEN, FAKE_TOKEN.replace(".", "_"), "a1b2c3d4e5f6", "defaults"):
                self.assertNotIn(leak, out, label)

    def test_binary_and_missing_file(self):
        with open(self.cfg, "wb") as f:
            f.write(b"\xff\xfe\x00\x81\x82secretvalue")
        code, out = run(cc.main, ["workflow-names", "--file", self.cfg, "--consent-read-config"])
        self.assertEqual(code, 1)
        self.assertNotIn("secretvalue", out)
        code, out = run(cc.main, ["workflow-names", "--file", os.path.join(self.tmp.name, "no.yml"), "--consent-read-config"])
        self.assertEqual(code, 1)
        self.assertIn("中身は表示していません", out)


# --- 5. 生成前の RAM 警告 ---------------------------------------------------------------

def fake_ram(avail, commit):
    return {"total_gb": 15.9, "available_gb": avail, "commit_limit_gb": 31.9, "commit_available_gb": commit}


class PreGenerationTest(unittest.TestCase):
    def pre(self, avail, commit, video=False, th=None):
        with mock.patch.object(ce, "ram_info", return_value=fake_ram(avail, commit)):
            return ce.pre_generation(video, th, runner=lambda *a, **k: (0, "NVIDIA GeForce RTX 3060, 12288, 1500, 1"))

    def test_both_warnings_before_generation(self):
        r = self.pre(1.9, 7.4)
        self.assertEqual({w["item"] for w in r["warnings"]}, {"ram_free", "commit_free"})
        self.assertFalse(r["ok_to_generate"])
        out = "\n".join(ce.human_pre_generation(r))
        self.assertIn("ご自身で", out)
        self.assertIn("アプリを閉じたり、メモリを解放したりしません", out)
        self.assertIn("1.9GB", out)
        self.assertIn("7.4GB", out)

    def test_enough_memory(self):
        r = self.pre(9.0, 20.0)
        self.assertEqual(r["warnings"], [])
        self.assertIn("目安を満たしています", "\n".join(ce.human_pre_generation(r)))

    def test_video_is_stricter_and_combines_with_custom(self):
        self.assertEqual(self.pre(7.0, 12.0)["warnings"], [])
        self.assertEqual({w["item"] for w in self.pre(7.0, 12.0, video=True)["warnings"]}, {"ram_free", "commit_free"})
        r = self.pre(7.0, 12.0, video=True, th={"ram_free_warn_below_gb": 2.0})
        self.assertEqual({w["item"] for w in r["warnings"]}, {"commit_free"})

    def test_cli_does_not_touch_token(self):
        env = TrackingEnviron(os.environ)
        env["DISCORD_TOKEN"] = FAKE_TOKEN
        with mock.patch.object(os, "environ", env), mock.patch.object(ce, "ram_info", return_value=fake_ram(1.0, 2.0)), \
                mock.patch.object(ce, "run_cmd", lambda *a, **k: (-1, "")):
            code, out = run(ce.main, ["--pre-generation", "--read-only"])
        self.assertEqual(code, 0)
        self.assertNotIn("DISCORD_TOKEN", env.touched)
        self.assertIn("[注意]", out)

    def test_no_kill_or_free_in_scripts_and_docs_reference(self):
        for name in os.listdir(SCRIPTS):
            if name.endswith(".py"):
                with open(os.path.join(SCRIPTS, name), encoding="utf-8") as f:
                    src = f.read()
                for bad in ("taskkill", "Stop-Process", "TerminateProcess", "os.kill("):
                    self.assertNotIn(bad, src, name)
        for doc in ("SKILL.md", os.path.join("references", "smartphone_test.md")):
            with open(os.path.join(ROOT, doc), encoding="utf-8") as f:
                self.assertIn("--pre-generation", f.read(), doc)


# --- 6. 不完全な環境の表示 -------------------------------------------------------------

class SourceOnlyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def find(self, dirs):
        with mock.patch.object(di, "candidate_dirs", return_value=dirs):
            return di.find_installs(git_runner=lambda r: "", proc_runner=lambda: [])

    def test_friendly_message_and_not_judged_by_name(self):
        research = os.path.join(self.tmp.name, "imagesmith-research")   # 名前は research だが venv あり
        make_install(research)
        plain = os.path.join(self.tmp.name, "work")                     # 名前は普通だがソースのみ
        make_install(plain, venv=False, config=False)
        installs = self.find([research, plain])
        by = {i["base"]: i for i in installs}
        self.assertEqual(by[research]["status"], "ready")
        self.assertFalse(by[research]["source_only"])
        self.assertTrue(by[plain]["source_only"])
        out = "\n".join(di.human(installs, di.recommend(installs), False))
        self.assertIn("ImageSmith のソースファイルが見つかりましたが、実行に必要な環境（仮想環境）は確認できませんでした。今回は使用しません。", out)
        self.assertNotIn("研究", out)
        self.assertEqual(out.count("[要確認]"), 0)
        self.assertEqual(di.recommend(installs)["action"], "reuse")

    def test_only_source_folders(self):
        p = os.path.join(self.tmp.name, "x")
        make_install(p, venv=False, config=False)
        rec = di.recommend(self.find([p]))
        self.assertEqual(rec["action"], "new_install")
        self.assertIn("ソースファイル", rec["message"])

    def test_details_still_show_reasons(self):
        p = os.path.join(self.tmp.name, "x")
        make_install(p, venv=False, config=False)
        installs = self.find([p])
        out = "\n".join(di.human(installs, di.recommend(installs), True))
        self.assertIn("仮想環境（.venv）が見つかりません", out)


# --- 読み取り専用の総合確認（別プロセスで実行） -------------------------------------------

class ReadOnlyEndToEndTest(unittest.TestCase):
    def test_new_commands_create_no_files(self):
        import shutil
        with tempfile.TemporaryDirectory() as t:
            skill = os.path.join(t, "skill")
            shutil.copytree(SCRIPTS, os.path.join(skill, "scripts"))
            shutil.copytree(os.path.join(ROOT, "templates"), os.path.join(skill, "templates"))
            inst = os.path.join(t, "envD")
            make_install(inst, launcher=True)
            cfg = os.path.join(inst, "ImageSmith", "configuration.yml")
            local = os.path.join(t, "localappdata")
            os.makedirs(local)
            snaps = (tree_snapshot(skill), tree_snapshot(inst))
            env = {k: v for k, v in os.environ.items() if k not in ("DISCORD_TOKEN", "PYTHONDONTWRITEBYTECODE")}
            env["LOCALAPPDATA"] = local
            s = os.path.join(skill, "scripts")
            cmds = [
                ["detect_imagesmith.py", "--read-only", "--target-dir", inst],
                ["detect_imagesmith.py", "--read-only", "--target-dir", os.path.join(t, "missing")],
                ["check_shortcuts.py", "--read-only"],
                ["manage_progress.py", "--read-only", "select-imagesmith", inst],
                ["check_environment.py", "--read-only", "--pre-generation"],
                ["check_environment.py", "--read-only", "--pre-generation", "--video"],
                ["check_connection.py", "workflow-names", "--read-only", "--file", cfg],
            ]
            outputs = ""
            for c in cmds:
                p = subprocess.run([sys.executable, os.path.join(s, c[0]), *c[1:]], capture_output=True,
                                   env=env, cwd=t, timeout=120)
                self.assertIn(p.returncode, (0, 1, 5), (c, p.stderr[-500:]))
                outputs += p.stdout.decode("utf-8", "replace")
            self.assertEqual((tree_snapshot(skill), tree_snapshot(inst)), snaps)
            self.assertEqual(os.listdir(local), [])
            self.assertNotIn(FAKE_TOKEN, outputs)


if __name__ == "__main__":
    unittest.main()
