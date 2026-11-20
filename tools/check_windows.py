# -*- coding: utf-8 -*-
"""菜单里「打开某某窗口」的入口能不能真的开起来。

只到「窗口构造成功」这一步，不 exec（不等用户点关闭）。
依赖缺、接口改名，都会在这里冒出来。
"""
import os, sys, traceback
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); BASE = APP
sys.path.insert(0, BASE); sys.path.insert(0, APP); os.chdir(APP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])

FAIL = []
def ok(c, n, i=""):
    print(("  PASS  " if c else "  FAIL  ") + n + (f"   [{i}]" if i else ""))
    if not c: FAIL.append(n)

def try_open(name, fn):
    try:
        w = fn()
        ok(True, name, f"{type(w).__name__}")
        try: w.deleteLater()
        except Exception: pass
    except Exception as e:
        ok(False, name, f"{type(e).__name__}: {e}")
        traceback.print_exc()

def main():
    print("\n【窗口类入口】")
    # 这两个窗口要真的 pet 实例（要读 ambient / 记忆路径），别拿 None 糊
    import pet
    w = pet.PetWindow()

    def today():
        import zhengzhou_today as zt
        d = zt.TodayDialog(w)
        d.refresh()
        return d
    try_open("郑州今日面板", today)

    def dy():
        import douyin_panel as dp
        return dp.DouyinDialog(None, on_say=lambda t: None)
    try_open("抖音收藏面板", dy)

    def mem():
        import memory_panel as mp
        import pet_memory
        names = [n for n in dir(mp) if "Panel" in n or "Dialog" in n]
        if not names:
            raise RuntimeError("memory_panel 里没有面板类")
        cls = getattr(mp, names[0])
        return cls(w, w.ambient.path)
    try_open("记忆与成长面板", mem)

    w.close()
    print("\n" + "=" * 56)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for f in FAIL: print("  -", f)
        return 1
    print("窗口类入口都能打开")
    return 0

def run():
    code = 1
    try:
        code = main()
    except Exception:
        traceback.print_exc()
    QTimer.singleShot(80, lambda: app.exit(code))
QTimer.singleShot(150, run)
sys.exit(app.exec())
