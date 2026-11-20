# -*- coding: utf-8 -*-
"""感知模块：读懂「你此刻在干嘛」，只读系统信号，不碰任何现有动效链路。

三层里这是最底下的一层（信号采集 + 状态推断），输出只是一个「介入强度」，
具体怎么调节奏、怎么静音，由 pet.py 的调度层决定——本文件不直接选动画、
不碰 _react_anim_for / _queue_say / start_working 那几条反应链。

信号来源只有 Windows 系统本身：
  - GetLastInputInfo()：系统级「最后一次键盘/鼠标输入」距离现在多少秒。
    它统计的是这台电脑上的任何输入，不区分你在用哪个软件——这恰好是
    我们需要的：只要你在用电脑，就说明你在忙，它就该安静。

状态推断（规则透明、可解释）：
  空闲秒数 <  专注阈值  -> focus（你在忙，降频降干扰）
  空闲秒数 >= 离开阈值  -> away （你人走了，正常节奏，可触发重逢）
  其余                -> idle （维持现状，什么都不动）

优先级：away 判定放在 focus 之后，专注阈值内算 focus，超过离开阈值才算 away。
没有引入「开会」状态：检测麦克风占用需要 core audio 接口，侵入性高，先不做。
（这是方案 A 的取舍：只用「输入空闲」这一个信号，简单、零风险。）

本文件可以独立 import、独立测试，不依赖 Qt、不依赖 pet.py。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

# ---------------------------------------------------------------- Win32 封装
# LASTINPUTINFO 结构 + GetLastInputInfo。全部标准 Win32，无第三方依赖。
class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


def _system_idle_seconds():
    """系统级空闲秒数：距离最后一次键盘/鼠标输入过去了多少秒。

    失败（非 Windows / 调用出错）返回 None，让上层按「空闲」处理，绝不抛异常。
    """
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        tick_now = ctypes.windll.kernel32.GetTickCount()
        elapsed_ms = (tick_now & 0xFFFFFFFF) - (info.dwTime & 0xFFFFFFFF)
        # GetTickCount 会回绕（约 49.7 天），用 32 位无符号差值自动处理回绕
        return max(0.0, elapsed_ms / 1000.0)
    except Exception:
        return None


# ---------------------------------------------------------------- 状态推断
FOCUS = "focus"   # 你在忙，降频降干扰
AWAY = "away"     # 你人走了，正常节奏，可触发重逢
IDLE = "idle"     # 维持现状


def infer(idle_seconds, focus_threshold=180.0, away_threshold=600.0):
    """把空闲秒数映射成状态。

    focus_threshold：多少秒内有输入算「专注中」（默认 3 分钟）。
    away_threshold ：多少秒无输入算「离开」（默认 10 分钟）。

    空闲秒数是 None（拿不到信号）时保守按 idle 处理——既不打扰，也不乱触发。
    """
    if idle_seconds is None:
        return IDLE
    if idle_seconds < focus_threshold:
        return FOCUS
    if idle_seconds >= away_threshold:
        return AWAY
    return IDLE


class Awareness:
    """一个轻量的感知器实例，pet.py 每帧 tick 里调它的 status()。

    内部带节流：GetLastInputInfo 本身极便宜，但没必要求每个 tick 都调，
    默认 2 秒采一次样，够快也够省。
    """

    def __init__(self, focus_threshold=180.0, away_threshold=600.0,
                 sample_interval=2.0):
        self.focus_threshold = focus_threshold
        self.away_threshold = away_threshold
        self.sample_interval = sample_interval
        self._last_sample = 0.0          # time.monotonic 采样点
        self._idle = 0.0                 # 最近一次采到的空闲秒数
        self._state = IDLE

    def idle_seconds(self, now=None):
        """返回最近一次采样的空闲秒数（带节流）。"""
        import time
        now = time.monotonic() if now is None else now
        if now - self._last_sample >= self.sample_interval:
            self._last_sample = now
            v = _system_idle_seconds()
            self._idle = 0.0 if v is None else v
            self._state = infer(self._idle,
                                self.focus_threshold, self.away_threshold)
        return self._idle

    def state(self, now=None):
        """返回当前状态（focus/away/idle）。内部会先刷新采样。"""
        self.idle_seconds(now)
        return self._state


# ---------------------------------------------------------------- 独立测试
if __name__ == "__main__":
    import time
    a = Awareness()
    print("感知模块自检（每秒采样一次，观察状态变化）：")
    for _ in range(5):
        s = a.state()
        print("  空闲 %.1fs -> %s" % (a._idle, s))
        time.sleep(1.0)
