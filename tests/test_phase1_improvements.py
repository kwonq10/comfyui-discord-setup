"""[モック] Phase 1 実機検証で見つかった8項目の回帰テスト。

1 既存ImageSmith検出 / 2 再利用の判断 / 3 ComfyUI重複の解消 / 4 停止中の版表示 /
5 RAM警告 / 6 読み取り専用モード / 7 トークンに触れない / 8 初心者向け表示
"""
import builtins
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from helpers import FAKE_TOKEN, ROOT, SCRIPTS, FakeComfy
import check_environment as ce
import detect_comfyui as dc
import detect_imagesmith as di
import manage_progress as mp
import setup_imagesmith as si


def make_install(base, *, venv=True, config=True, plugin=None, commit=di.PINNED_COMMIT, packed=False,
                 security_fixed=True, launcher=False):
    """偽の ImageSmith 環境を作る（上流コードは含めない）。"""
    repo = os.path.join(base, "ImageSmith")
    os.makedirs(os.path.join(repo, "src", "bot"))
    os.makedirs(os.path.join(repo, "src", "core"))
    os.makedirs(os.path.join(repo, "plugins"))
    os.makedirs(os.path.join(repo, "workflows"))
    open(os.path.join(repo, "main.py"), "w").close()
    open(os.path.join(repo, "src", "bot", "imagesmith.py"), "w").close()
    open(os.path.join(repo, "plugins", "__init__.py"), "w").close()
    with open(os.path.join(repo, "src", "core", "security.py"), "w") as f:
        f.write("if not result.state:\n" if security_fixed else "if not result:\n")
    if plugin:
        open(os.path.join(repo, "plugins", plugin), "w").close()
    if config:
        with open(os.path.join(repo, "configuration.yml"), "w") as f:
            f.write(f"discord:\n  token: {FAKE_TOKEN}\n")
    g = os.path.join(repo, ".git")
    os.makedirs(os.path.join(g, "refs", "heads"))
    with open(os.path.join(g, "HEAD"), "w") as f:
        f.write("ref: refs/heads/master\n")
    if packed:
        with open(os.path.join(g, "packed-refs"), "w") as f:
            f.write(f"# pack-refs\n{commit} refs/heads/master\n")
    else:
        with open(os.path.join(g, "refs", "heads", "master"), "w") as f:
            f.write(commit + "\n")
    with open(os.path.join(g, "config"), "w") as f:
        f.write('[remote "origin"]\n\turl = https://github.com/jtyszkiew/ImageSmith.git\n')
    if venv:
        v = os.path.join(base, ".venv")
        os.makedirs(os.path.join(v, "Scripts"))
        open(os.path.join(v, "Scripts", "python.exe"), "w").close()
        with open(os.path.join(v, "pyvenv.cfg"), "w") as f:
            f.write("home = C:\\Python312\nversion = 3.12.10\n")
    if launcher:
        os.makedirs(os.path.join(base, "launcher"))
        open(os.path.join(base, "launcher", "start.ps1"), "w").close()
    return repo


GIT_CLEAN = lambda repo: ""  # noqa: E731
GIT_PATCHED = lambda repo: " M src/core/security.py\n?? workflows/a.json\n"  # noqa: E731


class OpenGuard:
    """configuration.yml を開こうとしたら失敗させる。"""

    def __init__(self):
        self.real = builtins.open

    def __call__(self, file, *a, **k):
        if str(file).replace("\\", "/").endswith("configuration.yml"):
            raise AssertionError("configuration.yml を開いた")
        return self.real(file, *a, **k)


def find(dirs, git=GIT_CLEAN, procs=None):
    with mock.patch.object(di, "candidate_dirs", return_value=dirs), mock.patch("builtins.open", OpenGuard()):
        return di.find_installs(git_runner=git, proc_runner=lambda: procs or [])


# --- 1. 既存 ImageSmith の検出 ----------------------------------------------------

class DetectImageSmithTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_detects_install_without_reading_config(self):
        base = os.path.join(self.t, "a")
        make_install(base, plugin="memory_tools.py", launcher=True)
        [r] = find([base], git=GIT_PATCHED)
        self.assertEqual(r["status"], "ready")
        self.assertEqual(r["git"]["commit"], di.PINNED_COMMIT)
        self.assertEqual(r["git"]["branch"], "master")
        self.assertEqual(r["git"]["modified"], ["src/core/security.py"])
        self.assertEqual(r["git"]["untracked"], 1)
        self.assertEqual(r["venv_info"]["python_version"], "3.12.10")
        self.assertTrue(r["has_config"])
        self.assertTrue(r["security_fix_applied"])
        self.assertEqual(r["custom_plugins"], ["memory_tools.py"])
        self.assertTrue(r["has_launcher"])
        self.assertFalse(r["running"])
        text = json.dumps(r) + "\n".join(di.human([r], di.recommend([r]), True))
        self.assertNotIn(FAKE_TOKEN, text)
        self.assertIn("独自プラグイン", text)
        self.assertIn("既知バグの1行修正", text)

    def test_packed_refs_and_repo_path_given_directly(self):
        base = os.path.join(self.t, "b")
        repo = make_install(base, packed=True)
        [r] = find([repo])
        self.assertEqual(r["git"]["commit"], di.PINNED_COMMIT)

    def test_running_detection_by_venv_python(self):
        base = os.path.join(self.t, "c")
        make_install(base)
        exe = os.path.join(base, ".venv", "Scripts", "python.exe")
        procs = [{"pid": 10, "ppid": 1, "exe": exe, "main": True},
                 {"pid": 11, "ppid": 10, "exe": r"C:\Python312\python.exe", "main": True},
                 {"pid": 20, "ppid": 1, "exe": r"D:\ComfyUI\python\python.exe", "main": True},  # ComfyUI
                 {"pid": 30, "ppid": 1, "exe": exe, "main": False}]
        [r] = find([base], procs=procs)
        self.assertEqual(sorted(r["running_pids"]), [10, 11])
        self.assertIn("起動中", "\n".join(di.human([r], di.recommend([r]), False)))

    def test_broken_venv_and_other_commit(self):
        base = os.path.join(self.t, "d")
        make_install(base, venv=False, config=False, commit="0" * 40)
        [r] = find([base])
        self.assertEqual(r["status"], "needs_attention")
        joined = " ".join(r["problems"] + r["notes"])
        self.assertIn("仮想環境", joined)
        self.assertIn("未検証", joined)
        self.assertIn("configuration.yml がまだありません", joined)

    def test_not_imagesmith_ignored(self):
        os.makedirs(os.path.join(self.t, "e", "ImageSmith"))
        self.assertEqual(find([os.path.join(self.t, "e")]), [])


# --- 2. 再利用の判断・複数環境 -------------------------------------------------------

class RecommendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a, self.b, self.c = (os.path.join(self.tmp.name, n) for n in "abc")
        make_install(self.a)
        make_install(self.b, plugin="memory_tools.py")
        make_install(self.c, venv=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_ready_reuse(self):
        rec = di.recommend(find([self.a, self.c]))
        self.assertEqual(rec["action"], "reuse")
        self.assertEqual(rec["target"], self.a)
        self.assertIn("再インストールは不要", rec["message"])

    def test_multiple_ready_never_auto_choose(self):
        installs = find([self.a, self.b, self.c])
        rec = di.recommend(installs)
        self.assertEqual(rec["action"], "choose")
        self.assertNotIn("target", rec)
        self.assertEqual(sorted(rec["candidates"]), sorted([self.a, self.b]))
        self.assertIn("勝手に選びません", rec["message"])
        # 環境ごとの情報が混ざらない
        by_base = {i["base"]: i for i in installs}
        self.assertEqual(by_base[self.a]["custom_plugins"], [])
        self.assertEqual(by_base[self.b]["custom_plugins"], ["memory_tools.py"])

    def test_same_install_listed_once(self):
        repo_a = os.path.join(self.a, "ImageSmith")
        installs = find([self.a, repo_a, self.a.upper() if os.name == "nt" else self.a, self.a + os.sep])
        self.assertEqual(len(installs), 1)

    def test_only_broken_needs_attention_and_none_new(self):
        self.assertEqual(di.recommend(find([self.c]))["action"], "needs_attention")
        self.assertEqual(di.recommend([])["action"], "new_install")

    def test_plan_does_not_recommend_new_install_when_existing(self):
        installs = find([self.a])
        fake = {"installs": installs, "recommendation": di.recommend(installs)}
        buf = io.StringIO()
        with mock.patch.object(si, "existing_installs_summary", return_value=fake), contextlib.redirect_stdout(buf):
            si.main(["plan", "--dir", os.path.join(self.tmp.name, "new")])
        out = buf.getvalue()
        self.assertIn("再インストールは不要", out)
        self.assertIn("新しい導入は勧めません", out)

    def test_skill_md_has_reuse_branch(self):
        with open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8") as f:
            text = f.read()
        for word in ("detect_imagesmith.py", "`reuse`", "`choose`", "Skill は選ばない", "新規導入を勧めない"):
            self.assertIn(word, text)


# --- 3・4. ComfyUI の重複解消と版表示 -------------------------------------------------

class ComfyDedupeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.appdata = os.path.join(self.tmp.name, "appdata")
        self.install = os.path.join(self.tmp.name, "D", "ComfyUI")
        core = os.path.join(self.install, "ComfyUI")
        os.makedirs(os.path.join(core, "comfy"))
        for fn in ("main.py", "nodes.py"):
            open(os.path.join(core, fn), "w").close()
        base = os.path.join(self.appdata, "Comfy Desktop")
        os.makedirs(os.path.join(base, "comfy-procs"))
        with open(os.path.join(base, "installations.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "i1", "name": "ComfyUI", "installPath": self.install, "version": "v0.34.0",
                        "comfyVersionTag": "v0.42.1", "sourceId": "standalone"},
                       {"id": "i2", "name": "Comfy Cloud", "sourceId": "cloud"}], f)
        with open(os.path.join(base, "comfy-procs", "i1.json"), "w", encoding="utf-8") as f:
            json.dump({"installationId": "i1", "port": 8188}, f)

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, fake):
        with mock.patch.dict(os.environ, {"APPDATA": self.appdata}):
            return dc.collect([8188], [self.tmp.name], True, opener=fake)

    def test_no_duplicate_with_deep_search(self):
        r = self.collect(FakeComfy(down=True))
        local = [i for i in r["installations"] if i["kind"] != "comfy_cloud"]
        self.assertEqual(len(local), 1)
        self.assertEqual(local[0]["kind"], "comfy_desktop")
        self.assertTrue(local[0]["comfyui_dir"].endswith("ComfyUI"))
        out = "\n".join(dc.human(r))
        self.assertEqual(out.count("場所:"), 1)

    def test_stopped_shows_recorded_version_note(self):
        out = "\n".join(dc.human(self.collect(FakeComfy(down=True))))
        self.assertIn("記録上の版: v0.42.1", out)
        self.assertIn("起動後に正確に確認できます", out)
        self.assertIn("停止中", out)

    def test_running_shows_actual_version(self):
        r = self.collect(FakeComfy())
        out = "\n".join(dc.human(r))
        self.assertIn("動いている版: 0.42.5", out)
        self.assertNotIn("記録上の版", out)


# --- 5. RAM 警告 -----------------------------------------------------------------

def env_result(**ram):
    return {"windows": {"os": "Windows", "is_windows_11": True}, "ram": ram,
            "gpu": {"nvidia": [{"vram_total_mib": 12288}]}, "python": {"has_3_12": True},
            "git": {"found": True}, "disks": [{"free_gb": 100.0}]}


class RamWarningTest(unittest.TestCase):
    def items(self, r, th=None):
        return {i["item"]: i["message"] for i in ce.evaluate(r, th)}

    def test_low_free_ram_warns(self):
        it = self.items(env_result(total_gb=15.9, available_gb=3.6, commit_limit_gb=31.9, commit_available_gb=12.0))
        self.assertEqual(set(it), {"ram_free"})
        self.assertIn("3.6GB", it["ram_free"])
        self.assertIn("RAM（実メモリ）", it["ram_free"])
        self.assertNotIn("ページファイル", it["ram_free"])

    def test_commit_warning_is_separate(self):
        it = self.items(env_result(total_gb=15.9, available_gb=8.0, commit_limit_gb=31.9, commit_available_gb=5.0))
        self.assertEqual(set(it), {"commit_free"})
        self.assertIn("ページファイル", it["commit_free"])

    def test_16gb_machine_not_flagged_and_enough_free_ok(self):
        self.assertEqual(self.items(env_result(total_gb=15.9, available_gb=8.0, commit_available_gb=20.0)), {})

    def test_thresholds_explicit_and_overridable(self):
        self.assertEqual(ce.THRESHOLDS["ram_free_warn_below_gb"], 6.0)
        r = env_result(total_gb=15.9, available_gb=3.6, commit_available_gb=12.0)
        self.assertEqual(self.items(r, {"ram_free_warn_below_gb": 2.0}), {})
        buf = io.StringIO()
        with mock.patch.object(ce, "run_cmd", lambda *a, **k: (-1, "")), \
                mock.patch.object(ce, "ram_info", return_value={"total_gb": 15.9, "available_gb": 3.6,
                                                                "commit_limit_gb": 31.9, "commit_available_gb": 12.0}), \
                contextlib.redirect_stdout(buf):
            ce.main(["--read-only", "--ram-free-warn-gb", "2"])
        self.assertNotIn("いま空いているRAM（実メモリ）が", buf.getvalue())


# --- 6・7. 読み取り専用・トークンに触れない --------------------------------------------

class TrackingEnviron(dict):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.touched = []

    def __getitem__(self, k):
        self.touched.append(k)
        return super().__getitem__(k)

    def get(self, k, d=None):
        self.touched.append(k)
        return super().get(k, d)

    def __contains__(self, k):
        self.touched.append(k)
        return super().__contains__(k)


class ReadOnlyTokenTest(unittest.TestCase):
    def test_read_only_never_touches_token(self):
        env = TrackingEnviron(os.environ)
        env["DISCORD_TOKEN"] = FAKE_TOKEN

        def reg_guard(*a, **k):
            raise AssertionError("レジストリを読んだ")

        buf = io.StringIO()
        with mock.patch.object(os, "environ", env), mock.patch("winreg.OpenKey", reg_guard), \
                mock.patch.object(ce, "run_cmd", lambda *a, **k: (-1, "")), contextlib.redirect_stdout(buf):
            ce.main(["--read-only", "--json"])
        self.assertNotIn("DISCORD_TOKEN", env.touched)
        self.assertNotIn(FAKE_TOKEN, buf.getvalue())
        self.assertEqual(json.loads(buf.getvalue())["discord_token"], {"checked": False})

    def test_detect_imagesmith_never_touches_token(self):
        env = TrackingEnviron(os.environ)
        env["DISCORD_TOKEN"] = FAKE_TOKEN
        with tempfile.TemporaryDirectory() as t:
            make_install(os.path.join(t, "a"))
            with mock.patch.object(os, "environ", env):
                find([os.path.join(t, "a")])
        self.assertNotIn("DISCORD_TOKEN", env.touched)

    def test_progress_read_only_refuses_writes(self):
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "p", "setup_progress.json")
            for args in (["init"], ["complete", "environment_check"], ["fact", "comfyui_url", "http://127.0.0.1:8188"],
                         ["status", "blocked"], ["reset", "--confirm"]):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(mp.main(["--file", path, "--read-only", *args]), 4, args)
            self.assertFalse(os.path.exists(os.path.dirname(path)))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(mp.main(["--file", path, "--read-only", "show"]), 0)
            self.assertFalse(os.path.exists(os.path.dirname(path)))

    def test_setup_read_only_refuses_even_with_confirm(self):
        with tempfile.TemporaryDirectory() as t:
            target = os.path.join(t, "new")
            for args in (["install", "--dir", target, "--confirm"],
                         ["patch-security", "--dir", target, "--confirm"]):
                with mock.patch.object(si, "run") as run, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(si.main([*args, "--read-only"]), 4)
                run.assert_not_called()
            self.assertEqual(os.listdir(t), [])


def tree_snapshot(root):
    snap = {}
    for dp, dn, fn in os.walk(root):
        for n in dn:
            snap[os.path.relpath(os.path.join(dp, n), root)] = "dir"
        for n in fn:
            p = os.path.join(dp, n)
            with open(p, "rb") as f:
                snap[os.path.relpath(p, root)] = (hashlib.sha256(f.read()).hexdigest(), os.stat(p).st_mtime_ns)
    return snap


class ReadOnlyFilesystemTest(unittest.TestCase):
    """実際にスクリプトを別プロセスで動かし、Skillフォルダ・ImageSmith環境・進捗の場所が変わらないこと。"""

    def test_scripts_create_no_files(self):
        with tempfile.TemporaryDirectory() as t:
            skill = os.path.join(t, "skill")
            import shutil
            shutil.copytree(SCRIPTS, os.path.join(skill, "scripts"))
            shutil.copytree(os.path.join(ROOT, "templates"), os.path.join(skill, "templates"))
            inst = os.path.join(t, "comfy-discord-imagesmith")
            make_install(inst)
            local = os.path.join(t, "localappdata")
            os.makedirs(local)
            before_skill, before_inst = tree_snapshot(skill), tree_snapshot(inst)
            env = {k: v for k, v in os.environ.items() if k not in ("DISCORD_TOKEN", "PYTHONDONTWRITEBYTECODE")}
            env["LOCALAPPDATA"] = local
            s = os.path.join(skill, "scripts")
            cmds = [
                ["check_environment.py", "--read-only"],
                ["detect_comfyui.py", "--read-only", "--port", "1"],
                ["detect_imagesmith.py", "--read-only", "--dir", inst, "--details"],
                ["manage_progress.py", "--read-only", "show"],
                ["manage_progress.py", "--read-only", "init"],
                ["setup_imagesmith.py", "plan", "--read-only", "--dir", os.path.join(t, "x")],
                ["diagnose.py", "--read-only", "--symptom", "ram"],
            ]
            for c in cmds:
                p = subprocess.run([sys.executable, os.path.join(s, c[0]), *c[1:]], capture_output=True,
                                   env=env, cwd=t, timeout=120)
                self.assertIn(p.returncode, (0, 1, 4), (c, p.stderr[-500:]))
            self.assertEqual(tree_snapshot(skill), before_skill)   # __pycache__ も作られない
            self.assertEqual(tree_snapshot(inst), before_inst)
            self.assertEqual(os.listdir(local), [])               # 進捗ファイルも作られない
            self.assertFalse(os.path.exists(os.path.join(t, "x")))


# --- 8. 初心者向け表示 -------------------------------------------------------------

GOOD = {
    "nvidia-smi": (0, "NVIDIA GeForce RTX 3060, 12288, 1349, 580.10"),
    "py": (0, " -V:3.12 *        Python 3.12 (64-bit)\n -V:Astral/CPython3.11.15  x"),
    "python": (0, "Python 3.12.10"),
    "git": (0, "git version 2.53.0.windows.2"),
}


def runner(args, timeout=15):
    return GOOD.get(args[0], (-1, ""))


class BeginnerDisplayTest(unittest.TestCase):
    def setUp(self):
        self.r = ce.collect(runner, secret_check=False)

    def test_terms_explained_and_internals_hidden(self):
        out = "\n".join(ce.human(self.r))
        for term in ("※RAM＝", "※VRAM＝", "※Python＝", "※Git＝"):
            self.assertIn(term, out)
        for internal in ("Astral", "-V:", "ビルド", "580.10", "ページファイル（コミット）"):
            self.assertNotIn(internal, out)
        self.assertIn("--details", out)

    def test_details_show_internals(self):
        out = "\n".join(ce.human(self.r, details=True))
        for internal in ("Astral/CPython3.11.15", "Windowsビルド", "580.10", "警告の基準"):
            self.assertIn(internal, out)
        self.assertIn("※ページファイル＝", out)

    def test_comfyui_labels_are_plain(self):
        r = {"installations": [{"kind": "comfy_desktop", "installPath_display": r"D:\ComfyUI",
                                "recorded_version": "v0.42.1", "running_version": None}],
             "probes": [{"port": 8188, "running": False}], "running": [], "warnings": []}
        out = "\n".join(dc.human(r))
        self.assertIn("ComfyUI Desktop版", out)
        self.assertNotIn("comfy_desktop", out)
        self.assertIn("画像・動画を作るアプリ", out)

    def test_imagesmith_summary_plain(self):
        with tempfile.TemporaryDirectory() as t:
            make_install(os.path.join(t, "a"))
            installs = find([os.path.join(t, "a")])
        out = "\n".join(di.human(installs, di.recommend(installs), False))
        self.assertIn("Discord Bot のプログラム", out)
        self.assertIn("ImageSmith専用のPython置き場", out)
        self.assertNotIn(di.PINNED_COMMIT, out)   # コミット番号は --details のみ
        self.assertIn("判断:", out)


if __name__ == "__main__":
    unittest.main()
