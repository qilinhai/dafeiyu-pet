# -*- coding: utf-8 -*-
"""量稳态 CPU / 内存：人在电脑前 / 人离开 / 窗口隐藏 三种情况。

判断「省了没有」要在这里量，别看配置文件：配置改了但画面没变是常事。
判断「有没有损失」要看 TICK —— 人在电脑前必须还是 20ms。
"""
import os, sys, time
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); BASE = APP
sys.path.insert(0,BASE); sys.path.insert(0,APP); os.chdir(APP)
os.environ["QT_QPA_PLATFORM"]="offscreen"
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
app=QApplication(sys.argv[:1])
import pet, psutil
pet.PetWindow.save_config=lambda self: None

def rss(): return psutil.Process(os.getpid()).memory_info().rss/1048576

w = pet.PetWindow()
w._douyin_timer.stop(); w._hunch_timer.stop()
w.cfg["phone_autoconnect"]=False; w.cfg["phone_remind"]=False
w._phone_auto_done=True; w._phone_remind_at=10**12; w._credits_next=10**12
w.tts=None; w._aware=None
w.show()

print(f"启动稳态 RSS {rss():.1f} MB  (旧版同口径 114.9 MB)")
print(f"亮度缓存上限 24MB，当前 {w._bright_used/1048576:.1f} MB\n")

def measure(tag, seconds=8.0, setup=None, undo=None):
    if setup: setup()
    app.processEvents()
    t0=time.process_time(); w0=time.perf_counter()
    while time.perf_counter() < w0+seconds:
        app.processEvents(); time.sleep(0.001)
    cpu=(time.process_time()-t0)/(time.perf_counter()-w0)*100
    print(f"  {tag:<16} TICK={w.tick_ms:>3}ms  CPU {cpu:5.2f}%  RSS {rss():5.1f} MB")
    if undo: undo()
    return cpu

print("【新版自适应】")
a = measure("待机呼吸")
b = measure("演动作", 6.0, lambda: setattr(w, "anim_name", "sway"),
            lambda: setattr(w, "anim_name", None))
c = measure("睡着", 6.0, lambda: setattr(w, "sleeping", True),
            lambda: setattr(w, "sleeping", False))

print("\n【人离开电脑（十分钟没动键鼠）】")
class _Away:
    def state(self): return "away"
w._aware = _Away(); app.processEvents()
t0=time.process_time(); w0=time.perf_counter()
while time.perf_counter() < w0+6:
    app.processEvents(); time.sleep(0.001)
away=(time.process_time()-t0)/(time.perf_counter()-w0)*100
print(f"  人在电脑前               TICK={pet.TICK:>3}ms  CPU {a:5.2f}%")
print(f"  人离开                   TICK={w.tick_ms:>3}ms  CPU {away:5.2f}%")
w._aware=None

print("\n【窗口隐藏（用户看不见时）】")
w.sleeping=False; w.hide(); app.processEvents()
t0=time.process_time(); w0=time.perf_counter()
while time.perf_counter() < w0+6:
    app.processEvents(); time.sleep(0.001)
hid=(time.process_time()-t0)/(time.perf_counter()-w0)*100
print(f"  隐藏                    TICK={w.tick_ms:>3}ms  CPU {hid:5.2f}%")
w.show()

print("\n" + "="*58)
print(f"待机 {a:.1f}%  |  隐藏 {hid:.1f}%（相对待机省 {(1-hid/a)*100:.0f}%）")
print("注：offscreen 软件光栅；真实屏幕还要加 DWM 合成。")
w.close()
