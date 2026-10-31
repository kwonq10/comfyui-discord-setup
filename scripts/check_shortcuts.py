"""デスクトップのショートカットが、どの ImageSmith 環境を使うかを読み取りで判定する。

- ショートカット（.lnk）は WScript.Shell の CreateShortcut で「読むだけ」。保存（Save）も実行もしない。
- 表示名では判断しない。リンク先・作業フォルダ・引数のスクリプト・スクリプト内のパスから判定する。
- リンク先が .ps1 / .bat / .cmd の場合は、テキストとして読み（最大256KB）、同じフォルダから
  読み込む補助スクリプト（例: . "$PSScriptRoot\\common.ps1"）も読む（最大5ファイル）。
- 根拠には強弱をつける。
    強: リンク先・作業フォルダ・実行されるスクリプト自身の置き場所
    弱: スクリプト本文に書かれたパス（コメント行は除く）。強い根拠で決まらないときだけ使う
- スクリプトの中身・引数は表示しない（トークン等が書かれている可能性があるため）。判定結果だけ出す。
- 判定できないときは「不明」。

使い方:
    python check_shortcuts.py [--details] [--json]
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # Skill フォルダに __pycache__ を作らない

import argparse  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import anonymize_path, emit, is_windows, setup_stdout  # noqa: E402

SCRIPT_EXTS = (".ps1", ".psm1", ".bat", ".cmd")
MAX_SCRIPT_BYTES = 256 * 1024
MAX_SCRIPTS = 5
NAME_HINT = re.compile(r"(?i)comfy|imagesmith|discord|bot|forge")
ABS_PATH = re.compile(r"[A-Za-z]:\\[^\"'\r\n|<>*?;]+")
USER_PATH = re.compile(r"(?i)(%USERPROFILE%|\$env:USERPROFILE)([\\/][^\"'\r\n|<>*?;]+)")
DOT_SOURCE = re.compile(r"""(?im)^\s*\.\s+["']?\$PSScriptRoot[\\/]([^"'\r\n]+?\.ps1)["']?\s*$""")
CALL_SAME_DIR = re.compile(r"""(?i)%~dp0([^"'\r\n%]+?\.(?:bat|cmd|ps1))""")
SAFE_ENV_VARS = ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "HOMEDRIVE")

MAX_LNK_BYTES = 1024 * 1024

# --- .lnk（Shell Link, MS-SHLLINK）をバイトとして読む ----------------------------------
# 外部プロセス（PowerShell/COM）を使わない：起動しない・キャッシュ等の副作用も出さないため。

_F_IDLIST, _F_LINKINFO, _F_NAME, _F_RELPATH, _F_WORKDIR, _F_ARGS, _F_ICON, _F_UNICODE = (
    0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x80)
_ENV_BLOCK_SIG = 0xA0000001


class LnkError(ValueError):
    pass


def _u32(b, o):
    if o + 4 > len(b):
        raise LnkError("short")
    return int.from_bytes(b[o:o + 4], "little")


def _u16(b, o):
    if o + 2 > len(b):
        raise LnkError("short")
    return int.from_bytes(b[o:o + 2], "little")


def _cstr(b, o, unicode=False):
    if o >= len(b):
        return ""
    if unicode:
        end = o
        while end + 1 < len(b) and b[end:end + 2] != b"\0\0":
            end += 2
        return b[o:end].decode("utf-16-le", "replace")
    end = b.find(b"\0", o)
    return b[o:end if end >= 0 else len(b)].decode("mbcs" if is_windows() else "latin-1", "replace")


def parse_lnk(data: bytes) -> dict:
    """.lnk のバイト列から target / workdir / args を取り出す（ファイルには一切書かない）。"""
    if len(data) < 0x4C or _u32(data, 0) != 0x4C:
        raise LnkError("not a shell link")
    flags = _u32(data, 20)
    pos = 0x4C
    if flags & _F_IDLIST:
        pos += 2 + _u16(data, pos)
    target = ""
    if flags & _F_LINKINFO:
        li = pos
        size, hsize, liflags = _u32(data, li), _u32(data, li + 4), _u32(data, li + 8)
        if liflags & 0x1:  # VolumeIDAndLocalBasePath
            base_off, suffix_off = _u32(data, li + 16), _u32(data, li + 24)
            base, suffix = _cstr(data, li + base_off), _cstr(data, li + suffix_off)
            if hsize >= 0x24:
                ub, us = _u32(data, li + 28), _u32(data, li + 32)
                if ub:
                    base = _cstr(data, li + ub, True)
                if us:
                    suffix = _cstr(data, li + us, True)
            target = base + suffix
        pos += size
    uni = bool(flags & _F_UNICODE)
    strings = {}
    for flag, key in ((_F_NAME, "name"), (_F_RELPATH, "relpath"), (_F_WORKDIR, "workdir"),
                      (_F_ARGS, "args"), (_F_ICON, "icon")):
        if flags & flag:
            n = _u16(data, pos)
            pos += 2
            ln = n * 2 if uni else n
            if pos + ln > len(data):
                raise LnkError("short")
            strings[key] = data[pos:pos + ln].decode("utf-16-le" if uni else ("mbcs" if is_windows() else "latin-1"),
                                                     "replace")
            pos += ln
    # ExtraData: 環境変数入りのリンク先（%USERPROFILE%\... など）
    while not target and pos + 8 <= len(data):
        bsize = _u32(data, pos)
        if bsize < 8:
            break
        if _u32(data, pos + 4) == _ENV_BLOCK_SIG and bsize >= 8 + 260 + 520:
            target = _cstr(data, pos + 8 + 260, True) or _cstr(data, pos + 8)
        pos += bsize
    if not target and strings.get("relpath"):
        target = strings["relpath"]
    return {"target": target, "workdir": strings.get("workdir", ""), "args": strings.get("args", "")}


def desktop_dirs() -> list[str]:
    """ユーザーと全ユーザー共通のデスクトップ（OneDrive へ移動している場合も含む）。"""
    dirs = []
    if is_windows():
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

        def known(a, b, c, d):
            g = GUID(a, b, c, (ctypes.c_ubyte * 8)(*d))
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
                val = p.value
                ctypes.windll.ole32.CoTaskMemFree(p)
                return val
            return None

        for args in ((0xB4BFCC3A, 0xDB2C, 0x424C, (0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41)),   # Desktop
                     (0xC4AA340D, 0xF20F, 0x4863, (0xAF, 0xEF, 0xF8, 0x7E, 0xF2, 0xE6, 0xBA, 0x25))):  # PublicDesktop
            try:
                d = known(*args)
            except (OSError, AttributeError):
                d = None
            if d:
                dirs.append(d)
    else:
        dirs.append(os.path.join(os.path.expanduser("~"), "Desktop"))
    out, seen = [], set()
    for d in dirs:
        k = os.path.normcase(d)
        if k not in seen and os.path.isdir(d):
            seen.add(k)
            out.append(d)
    return out


def read_shortcuts(dirs: list[str] | None = None) -> list[dict]:
    results = []
    for d in dirs if dirs is not None else desktop_dirs():
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            if not e.name.lower().endswith(".lnk") or not e.is_file():
                continue
            try:
                if e.stat().st_size > MAX_LNK_BYTES:
                    continue
                with open(e.path, "rb") as f:   # 読み取りのみ
                    info = parse_lnk(f.read())
            except (OSError, LnkError):
                results.append({"name": e.name, "path": e.path, "target": "", "args": "", "workdir": "",
                                "unreadable": True})
                continue
            results.append(dict(info, name=e.name, path=e.path))
    return results


def _read_script(path: str) -> str | None:
    try:
        if not path.lower().endswith(SCRIPT_EXTS) or os.path.getsize(path) > MAX_SCRIPT_BYTES:
            return None
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _expand(path: str) -> str:
    """パス中の %USERPROFILE% 等だけを展開する（それ以外の環境変数は読まない）。"""
    path = path.strip().strip('"').strip("'")

    def env(name, original):
        return os.environ.get(name, original) if name.upper() in SAFE_ENV_VARS else original

    path = re.sub(r"(?i)\$env:(\w+)", lambda m: env(m.group(1).upper(), m.group(0)), path)
    path = re.sub(r"%(\w+)%", lambda m: env(m.group(1).upper(), m.group(0)), path)
    return path


def _script_args(args: str) -> list[str]:
    """引数から、実行されるスクリプトのパスだけを取り出す。"""
    found = []
    for m in re.finditer(r'"([^"]+)"|(\S+)', args or ""):
        tok = m.group(1) or m.group(2)
        if tok.lower().endswith(SCRIPT_EXTS + (".py",)):
            found.append(tok)
    return found


def _strip_comments(text: str, path: str) -> str:
    """コメント（PowerShell の #、バッチの REM / ::）を除く。コメント内の古いパスで誤判定しないため。"""
    bat = path.lower().endswith((".bat", ".cmd"))
    out = []
    for line in text.splitlines():
        t = line.strip()
        if bat:
            if t.lower().startswith("rem ") or t.startswith("::"):
                continue
        else:
            if t.startswith("#"):
                continue
            line = re.sub(r"\s#.*$", "", line)
        out.append(line)
    return "\n".join(out)


def candidate_paths(sc: dict) -> tuple[list[str], list[str]]:
    """(strong, weak) の2種類のパスを返す。スクリプトの中身そのものは返さない。"""
    strong, weak = [], []
    target, workdir = _expand(sc.get("target") or ""), _expand(sc.get("workdir") or "")
    strong += [p for p in (target, workdir) if p]
    scripts = [target] if target.lower().endswith(SCRIPT_EXTS) else []
    for a in _script_args(sc.get("args") or ""):
        a = _expand(a)
        if not os.path.isabs(a) and workdir:
            a = os.path.join(workdir, a)
        strong.append(a)
        if a.lower().endswith(SCRIPT_EXTS):
            scripts.append(a)
    seen = set()
    while scripts and len(seen) < MAX_SCRIPTS:
        s = scripts.pop(0)
        key = os.path.normcase(os.path.abspath(s))
        if key in seen:
            continue
        seen.add(key)
        text = _read_script(s)
        if text is None:
            continue
        code = _strip_comments(text, s)
        weak += [_expand(m.group(0)) for m in ABS_PATH.finditer(code)]
        weak += [_expand(m.group(1) + m.group(2)) for m in USER_PATH.finditer(code)]
        here = os.path.dirname(s)
        scripts += [os.path.join(here, rel) for rel in DOT_SOURCE.findall(code) + CALL_SAME_DIR.findall(code)]
    return strong, weak


def env_for_path(path: str, resolve, max_up: int = 4) -> str | None:
    """path 自身か、その上位フォルダ（最大 max_up 段）が ImageSmith 環境ならその base を返す。"""
    cur = os.path.abspath(path)
    if not os.path.isdir(cur):
        cur = os.path.dirname(cur)
    for _ in range(max_up + 1):
        inst = resolve(cur)
        if inst:
            return inst["base"]
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def _envs(paths, resolve) -> dict:
    found = {}
    for p in paths:
        b = env_for_path(p, resolve)
        if b:
            found[os.path.normcase(os.path.realpath(b))] = b
    return found


def classify(sc: dict, resolve) -> dict:
    target = (sc.get("target") or "").lower()
    if target.endswith(("powershell.exe", "pwsh.exe", ".ps1")):
        kind = "PowerShellスクリプト"
    elif target.endswith((".bat", ".cmd", "cmd.exe")):
        kind = "バッチファイル"
    elif target.endswith(("python.exe", "pythonw.exe")):
        kind = "Python"
    else:
        kind = "その他"
    strong, weak = candidate_paths(sc)
    bases = _envs(strong, resolve)
    basis = "リンク先・作業フォルダ"
    if not bases:
        bases, basis = _envs(weak, resolve), "スクリプト内のパス"
    if len(bases) == 1:
        env, state = next(iter(bases.values())), "判定済み"
    elif len(bases) > 1:
        env, state, basis = None, "不明（複数の環境を参照）", None
    else:
        env, state, basis = None, "不明", None
    # ImageSmith と無関係なアプリ（Discord.exe 等）は除く。
    # スクリプト・Python を起動し名前が関係しそうなものは「不明」として残す
    related = bool(bases) or (kind != "その他" and bool(NAME_HINT.search(sc.get("name") or "")))
    return {"name": sc.get("name"), "kind": kind, "env": env, "env_display": anonymize_path(env) if env else None,
            "state": state, "basis": basis, "related": related}


def check(reader=None, resolve=None) -> list[dict]:
    if resolve is None:
        import detect_imagesmith as di
        resolve = di.resolve_install
    shortcuts = (reader or read_shortcuts)()
    return [c for c in (classify(s, resolve) for s in shortcuts) if c["related"]]


def human(results: list[dict], details: bool = False) -> list[str]:
    lines = ["=== デスクトップのショートカットの確認（読み取りのみ・実行していません） ==="]
    if not results:
        lines.append("ImageSmith に関係するショートカットは見つかりませんでした。")
    for r in results:
        name = (r["name"] or "").removesuffix(".lnk")
        if r["env"]:
            lines.append(f"- 「{name}」→ {r['env_display']} の ImageSmith を使います")
        else:
            lines.append(f"- 「{name}」→ {r['state']}（どの ImageSmith を使うか判定できませんでした）")
        if details:
            lines.append(f"    起動するもの: {r['kind']}" + (f" / 判定の根拠: {r['basis']}" if r["basis"] else ""))
    return lines


def main(argv=None) -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--details", action="store_true")
    ap.add_argument("--read-only", action="store_true", help="読み取り専用（このスクリプトは常に読み取り専用）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = check()
    emit({"shortcuts": res}, a.json, human(res, a.details))
    return 0


if __name__ == "__main__":
    sys.exit(main())
