"""Phase 1: Windows環境の読み取り診断（何も変更しない）。

使い方:
    python check_environment.py            # 日本語の要約
    python check_environment.py --json     # 機械可読
    python check_environment.py --details    # 詳細（ビルド番号・ページファイル等）も表示
    python check_environment.py --read-only  # 読み取り専用。DISCORD_TOKEN に一切触れない
    python check_environment.py --no-secret-check   # DISCORD_TOKEN に一切触れない（--read-only に含まれる）

確認項目: Windowsバージョン / CPU / RAM / GPU・VRAM / Python / Git / ディスク空き /
          DISCORD_TOKEN が登録済みか（有無のみ。値・長さ・先頭文字は一切出さない）
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # Skill フォルダに __pycache__ を作らない

import argparse
import ctypes
import os
import platform
import shutil
import string
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import emit, is_windows, setup_stdout  # noqa: E402

GB = 1024 ** 3

# 目安（ImageSmith自体は軽量。重いのはComfyUI側の生成）
MIN_RAM_GB = 16
# 警告基準（ここを書き換えるか、コマンドの --ram-free-warn-gb 等で変更できる）
THRESHOLDS = {
    "ram_total_warn_below_gb": 15.0,    # 搭載RAM。16GB搭載でもWindows上は15.9GB等と表示されるため15.0
    "ram_free_warn_below_gb": 6.0,      # いま空いているRAM（実メモリ）。画像1枚の生成で数GB使う
    "commit_free_warn_below_gb": 8.0,   # RAM+ページファイルの空き。動画生成で不足しやすい
}
# 動画生成の前に使う、より厳しい目安（実機: RAM16GBで5秒動画の生成中に RAM+ページファイル残りが最小0.26GB）
VIDEO_THRESHOLDS = {
    "ram_free_warn_below_gb": 8.0,
    "commit_free_warn_below_gb": 16.0,
}
MIN_VRAM_GB = 8
MIN_FREE_DISK_GB = 20
RECOMMENDED_PYTHON = (3, 12)   # 実機検証済み: 3.12.10


def run_cmd(args: list[str], timeout: int = 15) -> tuple[int, str]:
    """外部コマンドを実行して (終了コード, 標準出力) を返す。見つからなければ (-1, '')。"""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def windows_info(runner=run_cmd) -> dict:
    info = {"os": platform.system(), "release": platform.release(), "version": platform.version()}
    build = None
    try:
        build = int(platform.version().split(".")[-1])
    except ValueError:
        pass
    info["build"] = build
    # Windows 11 もレジストリ上の ProductName は "Windows 10" のままなのでビルド番号で判定
    if info["os"] == "Windows" and build:
        info["edition_guess"] = "Windows 11" if build >= 22000 else "Windows 10"
    info["is_windows_11"] = bool(build and build >= 22000)
    return info


def cpu_info(runner=run_cmd) -> dict:
    name = platform.processor()
    if is_windows():
        code, out = runner(["powershell", "-NoProfile", "-Command",
                            "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"])
        if code == 0 and out:
            name = out.splitlines()[0].strip()
    return {"name": name, "logical_cores": os.cpu_count()}


def ram_info() -> dict:
    if not is_windows():
        return {"total_gb": None, "available_gb": None}

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return {"total_gb": None, "available_gb": None}
    return {
        "total_gb": round(st.ullTotalPhys / GB, 1),
        "available_gb": round(st.ullAvailPhys / GB, 1),
        # コミット上限 = RAM + ページファイル。動画生成ではここが不足しやすい
        "commit_limit_gb": round(st.ullTotalPageFile / GB, 1),
        "commit_available_gb": round(st.ullAvailPageFile / GB, 1),
    }


def parse_nvidia_smi(out: str) -> list[dict]:
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            total, used = int(float(parts[1])), int(float(parts[2]))
        except ValueError:
            continue
        gpus.append({"name": parts[0], "vram_total_mib": total, "vram_used_mib": used,
                     "driver": parts[3] if len(parts) > 3 else None})
    return gpus


def gpu_info(runner=run_cmd) -> dict:
    code, out = runner(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
                        "--format=csv,noheader,nounits"])
    nvidia = parse_nvidia_smi(out) if code == 0 else []
    others = []
    if is_windows():
        c2, o2 = runner(["powershell", "-NoProfile", "-Command",
                         "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"])
        if c2 == 0:
            others = [l.strip() for l in o2.splitlines() if l.strip()]
    return {"nvidia_smi_found": code != -1, "nvidia": nvidia, "all_adapters": others}


def parse_py_launcher(out: str) -> list[str]:
    versions = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("-V:") or line.startswith("-"):
            tag = line.split()[0].lstrip("-").replace("V:", "")
            versions.append(tag)
    return versions


def python_info(runner=run_cmd) -> dict:
    code, out = runner(["py", "-0"])
    launcher = parse_py_launcher(out) if code == 0 else []
    pcode, pout = runner(["python", "--version"])
    return {
        "running": platform.python_version(),
        "py_launcher_found": code != -1,
        "py_launcher_versions": launcher,
        "python_on_path": pout.replace("Python ", "") if pcode == 0 else None,
        "has_3_12": any(v.startswith("3.12") for v in launcher)
                     or (pcode == 0 and pout.replace("Python ", "").startswith("3.12")),
    }


def git_info(runner=run_cmd) -> dict:
    code, out = runner(["git", "--version"])
    return {"found": code == 0, "version": out.replace("git version ", "") if code == 0 else None}


def disk_info() -> list[dict]:
    drives = []
    letters = string.ascii_uppercase if is_windows() else []
    for letter in letters:
        root = f"{letter}:\\"
        if not os.path.exists(root):
            continue
        try:
            u = shutil.disk_usage(root)
        except OSError:
            continue
        drives.append({"drive": f"{letter}:", "free_gb": round(u.free / GB, 1), "total_gb": round(u.total / GB, 1)})
    if not is_windows():
        u = shutil.disk_usage(os.path.expanduser("~"))
        drives.append({"drive": "~", "free_gb": round(u.free / GB, 1), "total_gb": round(u.total / GB, 1)})
    return drives


def discord_token_registered() -> dict:
    """DISCORD_TOKEN が登録されているかだけを返す。値は呼び出し元に一切返さない。"""
    in_process = bool(os.environ.get("DISCORD_TOKEN"))
    in_user_scope = None
    if is_windows():
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, "DISCORD_TOKEN")
                in_user_scope = bool(value)
                del value
        except FileNotFoundError:
            in_user_scope = False
        except OSError:
            in_user_scope = None
    return {"in_this_process": in_process, "in_user_environment": in_user_scope}


def evaluate(r: dict, thresholds: dict | None = None) -> list[dict]:
    """不足・注意点を列挙する。thresholds で警告基準を上書きできる（既定は THRESHOLDS）。"""
    t = dict(THRESHOLDS, **(thresholds or {}))
    issues = []
    win = r["windows"]
    if win.get("os") != "Windows":
        issues.append({"level": "error", "item": "os", "message": "このSkillはWindows 11向けです。"})
    elif not win.get("is_windows_11"):
        issues.append({"level": "warn", "item": "os", "message": "Windows 11以外です。手順の画面が異なる場合があります（未検証）。"})

    ram = r.get("ram") or {}
    if ram.get("total_gb") is not None and ram["total_gb"] < t["ram_total_warn_below_gb"]:
        issues.append({"level": "warn", "item": "ram",
                       "message": f"RAM（実メモリ）が{ram['total_gb']}GBです。画像・動画生成では{MIN_RAM_GB}GB以上を推奨します。"})
    # RAM（実メモリ）の空きと、RAM+ページファイル（コミット）の空きは別の数値。混同しない
    if ram.get("available_gb") is not None and ram["available_gb"] < t["ram_free_warn_below_gb"]:
        issues.append({"level": "warn", "item": "ram_free",
                       "message": f"いま空いているRAM（実メモリ）が{ram['available_gb']}GBしかありません"
                                  f"（目安{t['ram_free_warn_below_gb']}GB以上）。生成の前に Chrome などのアプリを閉じてください。"})
    if ram.get("commit_available_gb") is not None and ram["commit_available_gb"] < t["commit_free_warn_below_gb"]:
        issues.append({"level": "warn", "item": "commit_free",
                       "message": f"RAMとページファイル（ディスク上の予備メモリ）を合わせた空きが{ram['commit_available_gb']}GBです"
                                  f"（目安{t['commit_free_warn_below_gb']}GB以上）。動画生成は失敗しやすい状態です。"})

    nv = (r.get("gpu") or {}).get("nvidia") or []
    if not nv:
        issues.append({"level": "warn", "item": "gpu",
                       "message": "NVIDIA GPUを検出できませんでした。ComfyUIの生成が非常に遅いか、動かない可能性があります。"})
    else:
        top = max(g["vram_total_mib"] for g in nv) / 1024
        if top < MIN_VRAM_GB:
            issues.append({"level": "warn", "item": "vram",
                           "message": f"VRAM（GPUのメモリ）が{top:.0f}GBです。使えるモデルが限られます（{MIN_VRAM_GB}GB以上推奨）。"})

    if not r["python"]["has_3_12"]:
        issues.append({"level": "need", "item": "python",
                       "message": "Python 3.12 が見つかりません。ImageSmith用に必要です（実機検証は3.12.10）。"})
    if not r["git"]["found"]:
        issues.append({"level": "need", "item": "git",
                       "message": "Gitが見つかりません。ImageSmithの取得に必要です。"})

    if r.get("disks") and max(d["free_gb"] for d in r["disks"]) < MIN_FREE_DISK_GB:
        issues.append({"level": "warn", "item": "disk",
                       "message": f"空き容量が{MIN_FREE_DISK_GB}GB未満のドライブしかありません。モデル追加には不足します。"})
    return issues


def collect(runner=None, secret_check: bool = True, thresholds: dict | None = None) -> dict:
    runner = runner or run_cmd
    r = {
        "windows": windows_info(runner),
        "cpu": cpu_info(runner),
        "ram": ram_info(),
        "gpu": gpu_info(runner),
        "python": python_info(runner),
        "git": git_info(runner),
        "disks": disk_info(),
    }
    # secret_check=False のときは os.environ / レジストリの DISCORD_TOKEN に一切触れない
    r["discord_token"] = discord_token_registered() if secret_check else {"checked": False}
    r["issues"] = evaluate(r, thresholds)
    r["thresholds"] = dict(THRESHOLDS, **(thresholds or {}))
    return r


def human(r: dict, details: bool = False) -> list[str]:
    w, ram, gpu, py = r["windows"], r["ram"], r["gpu"], r["python"]
    lines = ["=== PCの確認（読み取りのみ・何も変更していません） ==="]
    lines.append(f"Windows : {w.get('edition_guess', w['os'])}")
    lines.append(f"CPU     : {r['cpu']['name']}")
    if ram.get("total_gb") is not None:
        lines.append(f"RAM     : {ram['total_gb']}GB（いま空いているのは {ram['available_gb']}GB）"
                     "  ※RAM＝作業中のデータを置く実メモリ")
    if gpu["nvidia"]:
        for g in gpu["nvidia"]:
            lines.append(f"GPU     : {g['name']}（VRAM {g['vram_total_mib'] / 1024:.0f}GB）"
                         "  ※VRAM＝画像生成で使うGPU専用のメモリ")
    else:
        lines.append("GPU     : NVIDIA製のGPUは見つかりませんでした")
    ver = py["python_on_path"]
    lines.append("Python  : " + (ver if ver else ("あり" if py["py_launcher_versions"] else "見つかりません"))
                 + ("（ImageSmithに必要な 3.12 があります）" if py["has_3_12"] else "")
                 + "  ※Python＝ImageSmithを動かすためのソフト")
    lines.append(f"Git     : {'あり' if r['git']['found'] else '見つかりません'}"
                 "  ※Git＝プログラムを公式の場所から取り寄せる道具")
    lines.append("空き容量: " + "、".join(f"{d['drive']} {d['free_gb']:.0f}GB" for d in r["disks"]))
    tok = r["discord_token"]
    if tok.get("checked") is False:
        lines.append("DISCORD_TOKEN の登録: 今回は確認していません（トークンには一切触れていません）")
    else:
        reg = tok["in_user_environment"]
        lines.append("DISCORD_TOKEN の登録: " + ("登録あり" if reg else "未登録" if reg is False else "確認できず")
                     + "（値は表示しません）")
    if details:
        lines.append("--- 詳細 ---")
        lines.append(f"Windowsビルド: {w.get('build')} / 論理コア: {r['cpu']['logical_cores']}")
        if ram.get("commit_limit_gb") is not None:
            lines.append(f"RAM+ページファイル（コミット）: 上限 {ram['commit_limit_gb']}GB / 空き {ram['commit_available_gb']}GB"
                         "  ※ページファイル＝RAMが足りないときに使うディスク上の予備")
        for g in gpu["nvidia"]:
            lines.append(f"GPU使用中: {g['vram_used_mib']}MiB / ドライバー {g['driver']}")
        if gpu["all_adapters"]:
            lines.append(f"表示アダプター: {', '.join(gpu['all_adapters'])}")
        lines.append(f"py ランチャーに登録された Python: {', '.join(py['py_launcher_versions']) or 'なし'}")
        lines.append(f"Git バージョン: {r['git']['version']}")
        lines.append("警告の基準: " + ", ".join(f"{k}={v}" for k, v in r["thresholds"].items()))
    lines.append("")
    if r["issues"]:
        label = {"error": "[対応不可]", "need": "[要導入]", "warn": "[注意]"}
        lines.append("--- 確認が必要な点 ---")
        for i in r["issues"]:
            lines.append(f"{label[i['level']]} {i['message']}")
    else:
        lines.append("不足しているものは見つかりませんでした。")
    if not details:
        lines.append("（詳しい情報は --details で表示できます）")
    return lines


def pre_generation(video: bool = False, thresholds: dict | None = None, runner=None) -> dict:
    """Phase 5/7 の生成前チェック。メモリだけを読み取りで確認する（アプリ終了・メモリ解放はしない）。"""
    t = dict(THRESHOLDS)
    t.update(VIDEO_THRESHOLDS if video else {})
    t.update(thresholds or {})  # ユーザー指定が最優先
    ram = ram_info()
    gpu = gpu_info(runner or run_cmd)
    warnings = []
    if ram.get("available_gb") is not None and ram["available_gb"] < t["ram_free_warn_below_gb"]:
        warnings.append({"item": "ram_free", "message":
                         f"いま空いているRAM（実メモリ）が{ram['available_gb']}GBです（目安{t['ram_free_warn_below_gb']}GB以上）。"})
    if ram.get("commit_available_gb") is not None and ram["commit_available_gb"] < t["commit_free_warn_below_gb"]:
        warnings.append({"item": "commit_free", "message":
                         f"RAMとページファイル（ディスク上の予備メモリ）を合わせた空きが{ram['commit_available_gb']}GBです"
                         f"（目安{t['commit_free_warn_below_gb']}GB以上）。"})
    return {"video": video, "ram": ram, "gpu": gpu, "thresholds": t, "warnings": warnings,
            "ok_to_generate": not warnings}


def human_pre_generation(r: dict) -> list[str]:
    kind = "動画" if r["video"] else "画像"
    ram, t = r["ram"], r["thresholds"]
    lines = [f"=== {kind}生成の前のメモリ確認（読み取りのみ・アプリの終了やメモリ解放はしません） ==="]
    if ram.get("available_gb") is not None:
        lines.append(f"空いているRAM（実メモリ）: {ram['available_gb']}GB（目安 {t['ram_free_warn_below_gb']}GB以上）")
        lines.append(f"RAM＋ページファイルの空き: {ram['commit_available_gb']}GB（目安 {t['commit_free_warn_below_gb']}GB以上）"
                     "  ※ページファイル＝RAMが足りないときに使うディスク上の予備")
    for g in r["gpu"]["nvidia"]:
        lines.append(f"GPUのメモリ（VRAM）: {g['vram_total_mib'] / 1024:.0f}GB中 {g['vram_used_mib'] / 1024:.1f}GB使用中")
    lines.append("")
    if r["warnings"]:
        for w in r["warnings"]:
            lines.append(f"[注意] {w['message']}")
        lines.append(f"→ このまま{kind}を生成すると、遅くなったり失敗したりすることがあります。")
        lines.append("  生成の前に、ご自身で Chrome など使っていないアプリを閉じてください。")
        lines.append("  （このSkillはアプリを閉じたり、メモリを解放したりしません。閉じ終わったら、もう一度この確認を行います）")
    else:
        lines.append("メモリの空きは目安を満たしています。")
    return lines


def main(argv=None) -> int:
    setup_stdout()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--details", action="store_true", help="詳細（ビルド番号・ページファイル・ドライバー等）も表示")
    ap.add_argument("--no-secret-check", action="store_true", help="DISCORD_TOKEN に一切触れない")
    ap.add_argument("--read-only", action="store_true",
                    help="読み取り専用診断。DISCORD_TOKEN に一切触れず、ファイルも作らない")
    ap.add_argument("--ram-free-warn-gb", type=float,
                    help=f"RAM空きの警告基準GB（既定 {THRESHOLDS['ram_free_warn_below_gb']}）")
    ap.add_argument("--commit-free-warn-gb", type=float,
                    help=f"RAM+ページファイル空きの警告基準GB（既定 {THRESHOLDS['commit_free_warn_below_gb']}）")
    ap.add_argument("--pre-generation", action="store_true",
                    help="Phase 5/7 の生成前チェック（メモリだけ確認。トークンには触れない）")
    ap.add_argument("--video", action="store_true", help="--pre-generation で動画用の基準を使う")
    a = ap.parse_args(argv)
    th = {}
    if a.ram_free_warn_gb is not None:
        th["ram_free_warn_below_gb"] = a.ram_free_warn_gb
    if a.commit_free_warn_gb is not None:
        th["commit_free_warn_below_gb"] = a.commit_free_warn_gb
    if a.pre_generation:
        r = pre_generation(a.video, th)
        emit(r, a.json, human_pre_generation(r))
        return 0
    r = collect(secret_check=not (a.no_secret_check or a.read_only), thresholds=th)
    emit(r, a.json, human(r, a.details))
    return 0


if __name__ == "__main__":
    sys.exit(main())
