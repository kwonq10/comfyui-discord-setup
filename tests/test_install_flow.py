"""[モック] ImageSmith の新規導入（install）の流れ・途中失敗・再開。

実際の git / pip / python は一切呼ばない（subprocess.run を呼んだらテスト失敗にする）。
外部コマンドは FakeRunner が「それらしく」フォルダを作って代わりに応答する。すべて一時フォルダ内。
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from helpers import FAKE_TOKEN
import detect_imagesmith as di
import setup_imagesmith as si
from _common import INSTALL_STATE_FILE

COMMIT = si.PINNED_COMMIT
OTHER_COMMIT = "0" * 40


class FakeRunner:
    """外部コマンドの偽物。fail に工程名を入れると、その工程で失敗する。"""

    def __init__(self, fail=None, partial=False, head=COMMIT, remote=si.REPO_URL, python="Python 3.12.10",
                 secret_output=False, dirty=False):
        self.fail, self.partial, self.head, self.remote = set(fail or []), partial, head, remote
        self.python, self.secret_output, self.dirty = python, secret_output, dirty
        self.calls = []

    def _fail(self, step, msg="error"):
        if self.secret_output:
            msg += (f"\nAuthorization: Bot {FAKE_TOKEN}\nhttps://user:hunter2pass@pypi.example/simple"
                    f"\napi_key=sk-abcdef123456 at {os.path.expanduser('~')}\\secret")
        self.fail.discard(step)  # 2回目は成功（再開テスト用）
        raise si.SetupError(f"コマンドが失敗しました（終了コード 1）: {step}\n{msg}")

    def __call__(self, args, cwd=None, timeout=900):
        args = [str(a) for a in args]
        self.calls.append(args)
        if args[-1] == "--version":
            if args[0] in ("py", "python3.12", "python"):
                if self.python is None:
                    raise si.SetupError("not found")
                return self.python + "\n"
            return "Python 3.12.10\n"
        if args[:2] == ["git", "clone"]:
            assert args[2] == "--", args
            repo = args[4]
            if "clone" in self.fail:
                if self.partial:
                    os.makedirs(os.path.join(repo, ".git"))
                self._fail("clone", "fatal: unable to access 'https://github.com/'")
            os.makedirs(os.path.join(repo, ".git"))
            with open(os.path.join(repo, "requirements.txt"), "w") as f:
                f.write("discord.py>=2.3.0\n")
            return ""
        if args[0] == "git" and "checkout" in args:
            assert args[-2] == "--detach" and len(args[-1]) == 40, args
            if "checkout" in self.fail:
                self._fail("checkout", "fatal: reference is not a tree")
            return ""
        if args[0] == "git" and args[-2:] == ["rev-parse", "HEAD"]:
            return self.head + "\n"
        if args[0] == "git" and args[-3:] == ["remote", "get-url", "origin"]:
            return self.remote + "\n"
        if args[0] == "git" and args[-2:] == ["status", "--porcelain"]:
            return " M src/x.py\n" if self.dirty else ""
        if "-m" in args and args[args.index("-m") + 1] == "venv":
            venv = args[-1]
            if "venv" in self.fail:
                if self.partial:
                    os.makedirs(venv)
                self._fail("venv")
            exe = os.path.join(venv, "Scripts", "python.exe") if os.name == "nt" else os.path.join(venv, "bin", "python")
            os.makedirs(os.path.dirname(exe))
            open(exe, "w").close()
            return ""
        if args[1:4] == ["-m", "pip", "install"]:
            if "pip_install" in self.fail:
                self._fail("pip_install", "ERROR: Could not find a version that satisfies the requirement")
            return "Successfully installed\n"
        if args[1:4] == ["-m", "pip", "check"]:
            if "pip_check" in self.fail:
                self._fail("pip_check", "pkg 1.0 has requirement x>=2")
            return "No broken requirements found.\n"
        if args[1:4] == ["-m", "pip", "freeze"]:
            return "discord.py==2.7.1\nPyYAML==6.0.3\n"
        raise AssertionError(f"想定外のコマンド: {args}")

    def count(self, pred):
        return sum(1 for c in self.calls if pred(c))


def is_clone(c):
    return c[:2] == ["git", "clone"]


def is_pip_install(c):
    return c[1:4] == ["-m", "pip", "install"]


def run_main(args, runner):
    buf = io.StringIO()
    with mock.patch.object(si, "run", runner), contextlib.redirect_stdout(buf):
        code = si.main(args)
    return code, buf.getvalue()


def snapshot(path):
    """フォルダの中身（相対パスと内容）を記録する。変更が無いことの確認用。"""
    out = {}
    for dp, dn, fn in os.walk(path):
        for name in dn + fn:
            p = os.path.join(dp, name)
            rel = os.path.relpath(p, path)
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    out[rel] = f.read()
            else:
                out[rel] = None
    return out


class InstallTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = os.path.join(self.tmp.name, "imagesmith-new")
        # 実際の外部コマンドを呼んだら即失敗（GitHub・pip へ接続しないことの保証）
        guard = mock.patch.object(si.subprocess, "run", side_effect=AssertionError("実コマンドを呼んではいけない"))
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, runner, resume=False, target=None):
        return si.do_install(target or self.target, COMMIT, resume=resume, runner=runner)

    def state(self, target=None):
        with open(os.path.join(target or self.target, INSTALL_STATE_FILE), encoding="utf-8") as f:
            return json.load(f)

    def fail(self, runner, target=None, resume=False):
        with self.assertRaises(si.InstallFailed) as cm:
            self.install(runner, resume=resume, target=target)
        return cm.exception.report


class SuccessTest(InstallTestBase):
    def test_1_new_install_success(self):
        r = FakeRunner()
        res = self.install(r)
        self.assertEqual(res["commit"], COMMIT)
        self.assertEqual(res["packages"], 2)
        st = self.state()
        self.assertEqual(st["status"], "complete")
        self.assertTrue(all(st["steps"][s] == "done" for s in si.STEP_IDS))
        with open(os.path.join(self.target, "install_record.json"), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual(rec["commit"], COMMIT)
        self.assertEqual(rec["repository"], si.REPO_URL)
        # 取得元は固定URL、コミットは完全ハッシュで固定し、取得後に照合している
        self.assertIn(["git", "clone", "--", si.REPO_URL, os.path.join(self.target, "ImageSmith")], r.calls)
        self.assertTrue(any(c[-1] == COMMIT and "--detach" in c for c in r.calls))
        self.assertTrue(any(c[-2:] == ["rev-parse", "HEAD"] for c in r.calls))
        self.assertTrue(any(c[-3:] == ["remote", "get-url", "origin"] for c in r.calls))
        # pip は確認済みの版の制約付き
        pip = [c for c in r.calls if is_pip_install(c)][0]
        self.assertIn("-c", pip)

    def test_main_success_output(self):
        code, out = run_main(["install", "--dir", self.target, "--confirm"], FakeRunner())
        self.assertEqual(code, 0, out)
        self.assertIn("[完了]", out)

    def test_9_path_with_space_and_japanese(self):
        target = os.path.join(self.tmp.name, "画像 生成", "image smith")
        r = FakeRunner()
        self.install(r, target=target)
        self.assertEqual(self.state(target)["status"], "complete")
        # シェルを通さず、空白・日本語を含むパスが1つの引数のまま渡る
        clone = [c for c in r.calls if is_clone(c)][0]
        self.assertEqual(clone[4], os.path.join(target, "ImageSmith"))
        self.assertTrue(all(isinstance(a, str) for c in r.calls for a in c))

    def test_detect_treats_complete_install_as_ready(self):
        self.install(FakeRunner())
        self.assertEqual(di.install_state(self.target), "complete")


class FailureTest(InstallTestBase):
    def test_2_clone_failure_then_resume(self):
        r = FakeRunner(fail=["clone"])
        rep = self.fail(r)
        self.assertEqual(rep["failed_step"], "clone")
        self.assertEqual(rep["done_steps"], ["prepare_dir"])
        self.assertEqual(rep["leftovers"], [INSTALL_STATE_FILE])
        self.assertTrue(rep["resumable"])
        self.assertEqual(self.state()["status"], "failed")
        self.assertFalse(os.path.exists(os.path.join(self.target, "install_record.json")))
        self.install(r, resume=True)
        self.assertEqual(self.state()["status"], "complete")

    def test_2b_partial_clone_must_be_deleted_by_user(self):
        r = FakeRunner(fail=["clone"], partial=True)
        rep = self.fail(r)
        self.assertIn("ImageSmith", rep["leftovers"])
        self.assertIn("削除してから", rep["next_action"])
        before = snapshot(self.target)
        with self.assertRaises(si.SetupError) as cm:
            self.install(r, resume=True)
        self.assertNotIsInstance(cm.exception, si.InstallFailed)
        self.assertIn("自動では削除しません", str(cm.exception))
        self.assertEqual(snapshot(self.target), before)  # 何も変更していない
        shutil.rmtree(os.path.join(self.target, "ImageSmith"))  # ユーザーが削除した想定
        self.install(r, resume=True)
        self.assertEqual(self.state()["status"], "complete")

    def test_3_checkout_failure_then_resume_skips_clone(self):
        r = FakeRunner(fail=["checkout"])
        rep = self.fail(r)
        self.assertEqual(rep["failed_step"], "checkout")
        self.assertIn("clone", rep["done_steps"])
        self.assertTrue(rep["resumable"])
        self.install(r, resume=True)
        self.assertEqual(r.count(is_clone), 1)  # 取得はやり直さない
        self.assertEqual(self.state()["status"], "complete")

    def test_4_commit_mismatch_is_not_completed(self):
        r = FakeRunner(head=OTHER_COMMIT)
        rep = self.fail(r)
        self.assertEqual(rep["failed_step"], "verify_commit")
        self.assertFalse(rep["resumable"])
        self.assertEqual(r.count(is_pip_install), 0)  # 照合に失敗したら先へ進まない
        self.assertNotEqual(self.state()["status"], "complete")
        self.assertFalse(os.path.exists(os.path.join(self.target, "install_record.json")))
        with self.assertRaises(si.SetupError):
            self.install(r, resume=True)  # 記録と実際のコミットが違うので再開しない

    def test_4b_remote_mismatch_stops(self):
        r = FakeRunner(remote="https://github.com/someone/fork.git")
        rep = self.fail(r)
        self.assertEqual(rep["failed_step"], "verify_commit")
        self.assertEqual(r.count(is_pip_install), 0)

    def test_5_python312_missing_creates_nothing(self):
        r = FakeRunner(python=None)
        with self.assertRaises(si.SetupError) as cm:
            self.install(r)
        self.assertIn("Python 3.12", str(cm.exception))
        self.assertFalse(os.path.exists(self.target))
        self.assertEqual(r.count(is_clone), 0)

    def test_5b_other_python_version_is_not_accepted(self):
        r = FakeRunner(python="Python 3.13.1")
        with self.assertRaises(si.SetupError):
            self.install(r)
        self.assertFalse(os.path.exists(self.target))

    def test_6_venv_failure_with_partial_venv(self):
        r = FakeRunner(fail=["venv"], partial=True)
        rep = self.fail(r)
        self.assertEqual(rep["failed_step"], "venv")
        self.assertIn(".venv", rep["leftovers"])
        self.assertIn("削除してから", rep["next_action"])
        with self.assertRaises(si.SetupError):
            self.install(r, resume=True)
        shutil.rmtree(os.path.join(self.target, ".venv"))
        self.install(r, resume=True)
        self.assertEqual(r.count(is_clone), 1)
        self.assertEqual(self.state()["status"], "complete")

    def test_7_pip_failure_not_ready_then_resume(self):
        r = FakeRunner(fail=["pip_install"])
        rep = self.fail(r)
        self.assertEqual(rep["failed_step"], "pip_install")
        self.assertEqual(rep["message"], "必要なライブラリのインストールに失敗しました。")
        self.assertTrue(rep["resumable"])
        # 途中の環境を「使える状態」と判定しない
        self.assertEqual(di.install_state(self.target), "failed")
        found = di.resolve_install(self.target)
        if found:
            info = di.inspect(found, git_runner=lambda repo: "", procs=[])
            self.assertEqual(info["status"], "needs_attention")
        self.install(r, resume=True)
        self.assertEqual(r.count(is_clone), 1)
        self.assertEqual(r.count(is_pip_install), 2)
        self.assertEqual(di.install_state(self.target), "complete")

    def test_pip_check_failure(self):
        rep = self.fail(FakeRunner(fail=["pip_check"]))
        self.assertEqual(rep["failed_step"], "pip_check")
        self.assertNotEqual(self.state()["status"], "complete")

    def test_main_failure_output_is_beginner_friendly(self):
        code, out = run_main(["install", "--dir", self.target, "--confirm"], FakeRunner(fail=["pip_install"]))
        self.assertEqual(code, 1)
        for phrase in ("[失敗]", "必要なライブラリのインストールに失敗しました", "成功した工程", "失敗した工程",
                       "残っているもの", "自動削除はしていません", "再実行: できます", "次にすること", "--resume"):
            self.assertIn(phrase, out)
        self.assertNotIn("Traceback", out)

    def test_unexpected_exception_is_recorded_and_explained(self):
        class Boom(FakeRunner):
            def __call__(self, args, cwd=None, timeout=900):
                if args[1:4] == ["-m", "pip", "check"]:
                    raise KeyError("x")
                return super().__call__(args, cwd, timeout)
        code, out = run_main(["install", "--dir", self.target, "--confirm"], Boom())
        self.assertEqual(code, 1)
        self.assertIn("想定外のエラー", out)
        self.assertNotIn("Traceback", out)
        self.assertEqual(self.state()["failed_step"], "pip_check")


class ConflictAndResumeSafetyTest(InstallTestBase):
    def test_8_existing_folder_untouched(self):
        os.makedirs(self.target)
        with open(os.path.join(self.target, "mine.txt"), "w") as f:
            f.write("user data")
        before = snapshot(self.target)
        r = FakeRunner()
        with self.assertRaises(si.SetupError):
            self.install(r)
        with self.assertRaises(si.SetupError) as cm:
            self.install(r, resume=True)  # Skill の記録が無い＝所有元不明なので再開もしない
        self.assertIn("所有元を確認できない", str(cm.exception))
        self.assertEqual(snapshot(self.target), before)
        self.assertEqual(r.calls, [])

    def test_8b_foreign_state_file_is_refused(self):
        os.makedirs(self.target)
        with open(os.path.join(self.target, INSTALL_STATE_FILE), "w") as f:
            json.dump({"tool": "someone-else", "schema_version": 1, "status": "failed"}, f)
        before = snapshot(self.target)
        with self.assertRaises(si.SetupError) as cm:
            self.install(FakeRunner(), resume=True)
        self.assertIn("このSkillが作った処理記録ではありません", str(cm.exception))
        self.assertEqual(snapshot(self.target), before)

    def test_8c_target_inside_skill_dir_refused(self):
        target = os.path.join(si.SKILL_DIR, "imagesmith-test-should-not-exist")
        self.assertFalse(si.inspect_target(target)["ok"])
        with self.assertRaises(si.SetupError):
            self.install(FakeRunner(), target=target)
        self.assertFalse(os.path.exists(target))

    def test_10_rerun_without_resume_after_failure_points_to_resume(self):
        r = FakeRunner(fail=["pip_install"])
        self.fail(r)
        before = snapshot(self.target)
        with self.assertRaises(si.SetupError) as cm:
            self.install(r)
        self.assertIn("--resume", str(cm.exception))
        self.assertEqual(snapshot(self.target), before)

    def test_10b_resume_refuses_unknown_files(self):
        self.fail(FakeRunner(fail=["pip_install"]))
        with open(os.path.join(self.target, "notes.txt"), "w") as f:
            f.write("x")
        before = snapshot(self.target)
        with self.assertRaises(si.SetupError) as cm:
            self.install(FakeRunner(), resume=True)
        self.assertIn("notes.txt", str(cm.exception))
        self.assertEqual(snapshot(self.target), before)

    def test_10c_resume_refuses_modified_repo(self):
        self.fail(FakeRunner(fail=["pip_install"]))
        with self.assertRaises(si.SetupError) as cm:
            self.install(FakeRunner(dirty=True), resume=True)
        self.assertIn("変更されています", str(cm.exception))

    def test_10d_resume_refuses_moved_folder_and_other_commit(self):
        self.fail(FakeRunner(fail=["pip_install"]))
        moved = os.path.join(self.tmp.name, "moved")
        shutil.copytree(self.target, moved)
        with self.assertRaises(si.SetupError) as cm:
            self.install(FakeRunner(), resume=True, target=moved)
        self.assertIn("場所", str(cm.exception))
        with self.assertRaises(si.SetupError):
            si.do_install(self.target, OTHER_COMMIT, resume=True, runner=FakeRunner())

    def test_10e_resume_after_complete_is_refused(self):
        self.install(FakeRunner())
        with self.assertRaises(si.SetupError) as cm:
            self.install(FakeRunner(), resume=True)
        self.assertIn("既に完了", str(cm.exception))

    def test_10f_resume_refuses_broken_state_file(self):
        self.fail(FakeRunner(fail=["pip_install"]))
        with open(os.path.join(self.target, INSTALL_STATE_FILE), "w") as f:
            f.write("{not json")
        with self.assertRaises(si.SetupError) as cm:
            self.install(FakeRunner(), resume=True)
        self.assertIn("読めません", str(cm.exception))

    def test_invalid_commit_rejected_before_anything(self):
        for bad in ("--upload-pack=evil", "master", "dcc0807", "g" * 40, ""):
            r = FakeRunner()
            with self.assertRaises(si.SetupError):
                si.do_install(self.target, bad, runner=r)
            self.assertEqual(r.calls, [])
        self.assertFalse(os.path.exists(self.target))
        code, out = run_main(["install", "--dir", self.target, "--commit=master", "--confirm"], FakeRunner())
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(self.target))


class GateTest(InstallTestBase):
    def test_11_without_confirm_changes_nothing(self):
        for extra in ([], ["--resume"]):
            r = FakeRunner()
            code, out = run_main(["install", "--dir", self.target, *extra], r)
            self.assertEqual(code, 3)
            self.assertIn("未実行", out)
            self.assertEqual(r.calls, [])
            self.assertFalse(os.path.exists(self.target))

    def test_11b_without_confirm_shows_previous_failure(self):
        self.fail(FakeRunner(fail=["pip_install"]))
        before = snapshot(self.target)
        code, out = run_main(["install", "--dir", self.target, "--resume"], FakeRunner())
        self.assertEqual(code, 3)
        self.assertIn("導入は完了していません", out)
        self.assertIn("必要なライブラリのインストール", out)
        self.assertEqual(snapshot(self.target), before)

    def test_12_read_only_changes_nothing(self):
        for extra in (["--confirm"], ["--confirm", "--resume"]):
            r = FakeRunner()
            code, out = run_main(["install", "--dir", self.target, "--read-only", *extra], r)
            self.assertEqual(code, 4)
            self.assertEqual(r.calls, [])
            self.assertFalse(os.path.exists(self.target))

    def test_plan_reports_incomplete_install_without_changes(self):
        self.fail(FakeRunner(fail=["checkout"]))
        before = snapshot(self.target)
        with mock.patch.object(si, "existing_installs_summary",
                               return_value={"installs": [], "recommendation": {"action": "new_install", "message": "x"}}):
            code, out = run_main(["plan", "--dir", self.target], FakeRunner())
        self.assertIn("導入は完了していません", out)
        self.assertIn("確認済みコミットへの固定", out)
        self.assertEqual(snapshot(self.target), before)


class SecretTest(InstallTestBase):
    def test_13_secrets_not_in_output_or_state(self):
        code, out = run_main(["install", "--dir", self.target, "--confirm"],
                             FakeRunner(fail=["pip_install"], secret_output=True))
        self.assertEqual(code, 1)
        with open(os.path.join(self.target, INSTALL_STATE_FILE), encoding="utf-8") as f:
            state_text = f.read()
        for text in (out, state_text):
            self.assertNotIn(FAKE_TOKEN, text)
            self.assertNotIn("hunter2pass", text)
            self.assertNotIn("sk-abcdef123456", text)
        self.assertIn("<秘匿>", out)
        home = os.path.expanduser("~")
        self.assertNotIn(home + "\\secret", out)

    def test_13b_run_redacts_real_command_output(self):
        """run() 自体のマスク（subprocess の結果を偽物に差し替えて確認）。"""
        fake = mock.Mock(returncode=1, stdout="", stderr=f"token={FAKE_TOKEN}\nAuthorization: Bearer abcdefghijkl")
        with mock.patch.object(si.subprocess, "run", return_value=fake):
            with self.assertRaises(si.SetupError) as cm:
                si.run(["git", "clone", "--", si.REPO_URL, "x"])
        msg = str(cm.exception)
        self.assertNotIn(FAKE_TOKEN, msg)
        self.assertNotIn("abcdefghijkl", msg)

    def test_13c_run_missing_command_and_timeout_are_explained(self):
        with mock.patch.object(si.subprocess, "run", side_effect=FileNotFoundError()):
            with self.assertRaises(si.SetupError) as cm:
                si.run(["git", "--version"])
        self.assertIn("見つかりません", str(cm.exception))
        with mock.patch.object(si.subprocess, "run", side_effect=si.subprocess.TimeoutExpired("pip", 60)):
            with self.assertRaises(si.SetupError) as cm:
                si.run(["pip", "install"], timeout=60)
        self.assertIn("終わらなかった", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
