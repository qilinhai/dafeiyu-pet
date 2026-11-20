# -*- coding: utf-8 -*-
"""诊断：dwm（桌面合成器）到底被谁拖着。

鼠标卡顿的典型链路是「有窗口以高帧率重绘 → dwm 合成队列积压 → 鼠标指针掉帧」。
这个脚本做 A/B：先量基线，再把指定进程的窗口临时隐藏，再量 —— 差值就是它
给 dwm 造成的负担。只调 ShowWindow，不动进程、不动配置。

用法：
    python tools/diag_dwm.py              # 默认量 pet.py，测 3+3+3 秒
    python tools/diag_dwm.py 12736 5      # 指定 pid 和每段秒数
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

import psutil

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]

SW_HIDE, SW_SHOWNOACTIVATE = 0, 4


def windows_of(pid):
    out = []

    def cb(hwnd, _):
        pid_owner = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_owner))
        if pid_owner.value == pid and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            out.append((hwnd, buf.value))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
    return out


def cpu_of(getter, sec):
    """在某进程集合上采 sec 秒的 CPU，换成「占全部核心的百分比」。"""
    cores = psutil.cpu_count() or 1
    t0 = [p.cpu_times() for p in getter()]
    time.sleep(sec)
    t1 = [p.cpu_times() for p in getter()]
    used = 0.0
    for a, b in zip(t0, t1):
        used += (b.user - a.user) + (b.system - a.system)
    return round(used / sec / cores * 100, 2)


def find_pet():
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cl = p.info["cmdline"] or []
            if any("pet.py" in x for x in cl):
                return p.info["pid"]
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    return None


def dwm_procs():
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if (p.info["name"] or "").lower() == "dwm.exe":
                out.append(psutil.Process(p.info["pid"]))
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    return out


def pet_procs(pid):
    try:
        return [psutil.Process(pid)]
    except psutil.NoSuchProcess:
        return []


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else find_pet()
    sec = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 3
    if not pid:
        print("没找到 pet.py 进程")
        return 1

    hs = windows_of(pid)
    print(f"目标 pid={pid}，可见窗口 {len(hs)} 个")
    for h, t in hs:
        print(f"   hwnd={h} title={t!r}")
    if not hs:
        print("没有可见窗口 —— 量不出效果，可能已经被隐藏或还在启动")
        return 1

    base = cpu_of(dwm_procs, sec)
    pet_base = cpu_of(lambda: pet_procs(pid), sec)
    print(f"\n基线     dwm={base}%   大肥鱼={pet_base}%")

    for h, _ in hs:
        user32.ShowWindow(h, SW_HIDE)
    time.sleep(0.5)
    hidden = cpu_of(dwm_procs, sec)
    print(f"隐藏后   dwm={hidden}%   （差 {base - hidden:+.2f} 个百分点）")

    for h, _ in hs:
        user32.ShowWindow(h, SW_SHOWNOACTIVATE)
    time.sleep(0.5)
    back = cpu_of(dwm_procs, sec)
    print(f"恢复后   dwm={back}%")

    # 结论只看「隐藏期间掉了多少」，恢复后回到基线说明因果关系成立
    if base > 0.3 and (base - hidden) / base > 0.3:
        print(f"\n=> 大肥鱼给 dwm 加了约 {base - hidden:.2f} 个百分点的持续负担"
              f"（占 dwm 本身 {round((base-hidden)/base*100)}%）")
    else:
        print(f"\n=> 大肥鱼不是 dwm 的主要负担（隐藏后只差 {base - hidden:.2f} 个点）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
