# -*- coding: utf-8 -*-
"""帧率与计时自检。

原则：可见时一帧不许少（50fps 是现有观感），只有「窗口看不见」才允许降。
另外守住三条：切档时时间不跳、动画按时间取帧、走路速度不随帧长变。
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = BASE   # 绿色版：脚本的上一层就是程序目录
sys.path.insert(0, BASE)
sys.path.insert(0, APP)
os.chdir(APP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv[:1])
import pet

pet.PetWindow.save_config = lambda self: None

FAIL = []


def ok(cond, name, info=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{info}]" if info else ""))
    if not cond:
        FAIL.append(name)


STAT = {"paint": 0}


def main():
    w = pet.PetWindow()
    w._douyin_timer.stop()
    w._hunch_timer.stop()
    w.cfg["phone_autoconnect"] = False
    w.cfg["phone_remind"] = False
    w._phone_auto_done = True
    w._phone_remind_at = 10 ** 12
    w._credits_next = 10 ** 12
    w.tts = None
    w.show()

    print("\n【档位】")
    ok(w._pick_tick() == pet.TICK, "可见时 → 20ms（50fps，跟旧版一致）",
       f"{w._pick_tick()}ms")
    w.hide()
    ok(w._pick_tick() == pet.TICK_HIDDEN, "隐藏 → 400ms（看不见就别画）",
       f"{w._pick_tick()}ms")
    w.show()

    class _Away:
        def state(self): return "away"
    real_aware = w._aware
    w._aware = _Away()
    ok(w._pick_tick() == pet.TICK_AWAY, "人离开（十分钟没动）→ 10fps",
       f"{w._pick_tick()}ms")
    w._aware = real_aware
    w.show()
    ok(w._pick_tick() == pet.TICK, "重新显示 → 立刻回到 20ms")

    print("\n【可见时不掉帧】")
    w.timer.stop()
    w.tick_ms = pet.TICK
    w.timer.start(pet.TICK)
    orig = w.paintEvent          # 已经绑定到 w，别再多传一个 self

    def count(self, ev):
        STAT["paint"] += 1
        return orig(ev)

    w.paintEvent = count.__get__(w, type(w))
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 3.0:
        app.processEvents()
        time.sleep(0.001)
    hz = STAT["paint"] / (time.perf_counter() - t0)
    ok(hz >= 45, "实际绘制 ≥45fps（没有偷偷降频）", f"{hz:.1f} fps")

    print("\n【切档不跳时间】")
    w.tick_ms = pet.TICK
    w.t = 10_000
    before = w.t
    w.hide()
    w.tick()
    ok(w.t - before == pet.TICK_HIDDEN, "隐藏后按 400ms 累加", f"{before} → {w.t}")
    w.show()
    w.tick_ms = pet.TICK
    before = w.t
    w.tick()
    ok(w.t - before == pet.TICK, "恢复后按 20ms 累加", f"{before} → {w.t}")

    print("\n【动作与走路】")
    if w.animlib:
        w.clip = "idle" if w.animlib.has("idle") else None
        if w.clip:
            w.clip_loop = True
            w.clip_t0 = 0.0
            w.tick_ms = pet.TICK
            w.t = 1000
            a = w.clip_frame()
            w.tick_ms = pet.TICK_HIDDEN
            w.t = 1000
            b = w.clip_frame()
            ok(a is b, "同一时刻取同一帧（按时间取，不按帧数）")
        w.tick_ms = pet.TICK
        s20 = w._start_move_anim()
        w.tick_ms = pet.TICK_HIDDEN
        s400 = w._start_move_anim()
        if s20 and s400:
            ok(abs(s400 / s20 - pet.TICK_HIDDEN / pet.TICK) < 0.3,
               "位移跟帧长成比例（降频也不会走慢）",
               f"{s20:.2f} → {s400:.2f}")

    w.close()
    print("\n" + "=" * 58)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：" + "、".join(FAIL))
        return 1
    print("帧率与计时全部通过")
    return 0


def run():
    code = 1
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
    QTimer.singleShot(60, lambda: app.exit(code))


QTimer.singleShot(120, run)
sys.exit(app.exec())
