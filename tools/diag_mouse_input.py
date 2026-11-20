# -*- coding: utf-8 -*-
"""鼠标卡顿定位：直接量「鼠标事件到达的时间间隔」。

判断依据：
  · 无线鼠标正常每 8~16ms 报一次（125~250Hz）
  · 如果出现大量 >50ms 的空隙，说明**输入层**在丢包（信号干扰 / 电池 / USB 节能）
  · 如果间隔很稳但画面还是顿，那是**渲染层**（dwm/GPU）的问题，跟鼠标硬件无关

用法：跑起来之后**正常移动鼠标 15 秒**（画圈、拖窗口都行），别停下。
    python tools/diag_mouse_input.py            # 15 秒
    python tools/diag_mouse_input.py 20         # 20 秒
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MOUSEWHEEL = 0x020A
PM_REMOVE = 0x0001

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]

stamps = []          # 每个鼠标事件的到达时刻
moves = []           # 只记移动
click_ct = [0]


def _hook(ncode, wparam, lparam):
    if ncode == 0:
        t = time.perf_counter()
        stamps.append(t)
        if wparam == WM_MOUSEMOVE:
            moves.append(t)
        elif wparam in (WM_LBUTTONDOWN, WM_LBUTTONUP,
                        WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MOUSEWHEEL):
            click_ct[0] += 1
    return user32.CallNextHookEx(None, ncode, wparam, lparam)


def gaps(ts):
    return [round((b - a) * 1000, 1) for a, b in zip(ts, ts[1:])]


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, int(len(s) * p))
    return s[i]


def main():
    sec = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 15
    cb = HOOKPROC(_hook)
    h = user32.SetWindowsHookExW(WH_MOUSE_LL, cb, None, 0)
    if not h:
        print("装不上鼠标钩子，错误码", ctypes.get_last_error())
        return 1

    print(f"开始采集 {sec} 秒 —— 请现在**正常移动鼠标**（画圈或拖个窗口），别停着。")
    msg = wt.MSG()
    t0 = time.perf_counter()
    try:
        while time.perf_counter() - t0 < sec:
            # 低级钩子必须让线程抽消息，否则系统会把钩子踢掉
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.002)
    finally:
        user32.UnhookWindowsHookEx(h)

    if len(moves) < 20:
        print(f"只收到 {len(moves)} 个移动事件 —— 鼠标基本没动，测不出来。重跑一次并移动鼠标。")
        return 2

    g = gaps(moves)
    span = (moves[-1] - moves[0]) / sec
    stall = [x for x in g if x > 50]
    print()
    print(f"事件总数 {len(stamps)}（移动 {len(moves)}，按键/滚轮 {click_ct[0]}），"
          f"平均 {span:.0f} 次/秒")
    print(f"间隔 中位数={pct(g, 0.5)}ms  90%分位={pct(g, 0.9)}ms  "
          f"95%分位={pct(g, 0.95)}ms  最大={max(g)}ms")
    print(f"超过 50ms 的空隙：{len(stall)} 个"
          f"（占 {round(len(stall)/len(g)*100, 2)}%），"
          f"合计卡住 {round(sum(stall)/1000, 2)} 秒")
    if stall:
        print("最长的 6 次停顿：", sorted(stall, reverse=True)[:6], "ms")

    print()
    if len(stall) / len(g) > 0.02 or max(g) > 120:
        print("=> 输入层有明显丢包。问题在鼠标信号/驱动，不是画面渲染。")
        print("   先做三件事：接收器换到机箱前面或加 USB 延长线到桌面、换新电池、")
        print("   拔掉那个没在用的第二个接收器。")
    else:
        print("=> 输入事件很稳，鼠标信号没问题。如果眼睛还是觉得顿，")
        print("   那是 dwm/GPU 合成的问题，得从显卡驱动和刷新率查。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
