"""只读普查：按「应用」而不是「进程名」聚合，看一个应用底下到底挂了哪些进程。

一个不杀，一个不改配置。纯粹是把真实现状打印出来。

分组做法：
  1. 按父进程链做并查集 —— 同一个应用拉起来的进程天然连在一起。
  2. 再按可执行文件所在的「产品目录」合并 —— 有些应用（Chromium 系）
     子进程是父进程被系统创建的，ppid 会变成已退出的中间进程，光靠链会断。
  3. 产品目录 = 从 exe 所在目录往上走，跳过 bin/app/当前版本号这类通用层。

用法：
    ../venv/Scripts/python.exe tools/survey_apps.py
    ../venv/Scripts/python.exe tools/survey_apps.py --min 20   只列 20MB 以上的
"""

import ctypes
import os
import sys
from collections import defaultdict
from ctypes import wintypes as wt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psutil  # noqa: E402

# 往上走时要跳过的「非产品名」目录
GENERIC = {
    "bin", "bin64", "bin32", "app", "apps", "application", "current", "latest",
    "x64", "x86", "x86_64", "amd64", "64", "32", "release", "debug", "resources",
    "contents", "macos", "framework", "frameworks", "lib", "libs", "core",
    "versions", "version", "update", "updater", "plugins", "modules", "src",
    "dist", "build", "output", "runtime", "helpers", "helper", "subprocess",
}


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
_user32.EnumWindows.argtypes = [_WNDENUMPROC, wt.LPARAM]
_user32.IsWindowVisible.argtypes = [wt.HWND]
_user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]


def visible_pids():
    out = set()

    def _cb(hwnd, _):
        try:
            if _user32.IsWindowVisible(hwnd):
                pid = wt.DWORD(0)
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    out.add(pid.value)
        except Exception:                              # noqa: BLE001
            pass
        return True

    _user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return out


def product_dir(exe):
    """从 exe 路径推产品根目录，跳过 bin/app/1.2.3 这类通用层。"""
    if not exe:
        return ""
    d = os.path.dirname(exe)
    for _ in range(4):
        base = os.path.basename(d)
        if not base:
            break
        looks_ver = base[:1].isdigit() or base.replace(".", "").isdigit()
        if base.lower() in GENERIC or looks_ver or base.lower().startswith("v"):
            parent = os.path.dirname(d)
            if parent and parent != d:
                d = parent
                continue
        break
    return d.lower()


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def main():
    min_mb = 0.0
    if "--min" in sys.argv:
        try:
            min_mb = float(sys.argv[sys.argv.index("--min") + 1])
        except (IndexError, ValueError):
            pass

    wins = visible_pids()
    procs = {}
    for p in psutil.process_iter(["pid", "name", "ppid", "memory_info"]):
        try:
            info = p.info
            pid = info["pid"]
            try:
                exe = p.exe()
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                exe = ""
            procs[pid] = {
                "pid": pid,
                "name": info["name"] or "?",
                "ppid": info.get("ppid") or 0,
                "mb": (info["memory_info"].rss if info.get("memory_info") else 0) / 1048576.0,
                "exe": exe,
                "pdir": product_dir(exe),
                "win": pid in wins,
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError):
            continue

    # 只按「产品目录」分组。父进程链不能用：所有进程的祖先都通向 System，
    # 并查集会把整个系统连成一组（第一版就踩了这个坑，185 个进程分成 4 组）。
    # 系统目录里的进程不合并，各自按名字站一队 —— 它们本来就不该被碰。
    comps = defaultdict(list)
    for pid, d in procs.items():
        pdir = d["pdir"]
        if not d["exe"]:
            key = ("noexe", d["name"].lower())
        elif pdir.startswith("c:\\windows"):
            key = ("win", d["name"].lower())
        else:
            key = ("dir", pdir)
        d["key"] = key
        comps[key].append(d)

    rows = []
    for _root, members in comps.items():
        mb = sum(m["mb"] for m in members)
        if mb < min_mb:
            continue
        dirs = sorted({m["pdir"] for m in members if m["pdir"]})
        label = os.path.basename(dirs[0]) if dirs else ""
        if not label:
            big = max(members, key=lambda m: m["mb"])
            label = os.path.splitext(big["name"])[0]
        names = defaultdict(lambda: {"n": 0, "mb": 0.0, "win": False})
        for m in members:
            e = names[m["name"].lower()]
            e["n"] += 1
            e["mb"] += m["mb"]
            e["win"] = e["win"] or m["win"]
        rows.append({
            "label": label, "mb": mb, "n": len(members),
            "win": any(m["win"] for m in members),
            "dirs": dirs, "names": names,
        })

    rows.sort(key=lambda r: -r["mb"])
    print(f"总进程 {len(procs)} 个 / 分成 {len(rows)} 组（每组一个应用）\n")
    print(f"{'应用':<26}{'进程':>4}{'内存MB':>9}  窗口  底下的进程名")
    print("-" * 100)
    for r in rows[:26]:
        parts = []
        for nm, e in sorted(r["names"].items(), key=lambda kv: -kv[1]["mb"]):
            tag = "*" if e["win"] else ""
            parts.append(f"{nm}×{e['n']}{tag}")
        print(f"{r['label'][:25]:<26}{r['n']:>4}{r['mb']:>9.0f}  "
              f"{'有' if r['win'] else '—':<4}  {' '.join(parts)[:60]}")
    print("\n* = 该进程名下至少一个窗口可见\n")

    print("=== 多进程名应用明细（一个应用挂了几种名字）===")
    for r in rows:
        if len(r["names"]) < 2 and r["n"] < 3:
            continue
        print(f"\n[{r['label']}] {r['n']} 个进程 {r['mb']:.0f} MB "
              f"{'（有窗口）' if r['win'] else '（无窗口）'}")
        print(f"  目录: {r['dirs'][0] if r['dirs'] else '?'}")
        for nm, e in sorted(r["names"].items(), key=lambda kv: -kv[1]["mb"]):
            print(f"    {nm:<34} ×{e['n']:<3} {e['mb']:>8.0f} MB"
                  f"{'  有窗口' if e['win'] else ''}")


if __name__ == "__main__":
    main()
