# -*- coding: utf-8 -*-
"""看得见的效果实测：亮度、大小、气泡开关。

不看配置值，直接 grab 窗口量像素——配置改了但画面没变，这里就露馅。
"""
import os, sys
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); BASE = APP
sys.path.insert(0, BASE); sys.path.insert(0, APP); os.chdir(APP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage

app = QApplication(sys.argv[:1])
import pet

# 同上：视觉测试会 resize / 挪窗口，绝不能落盘
pet.PetWindow.save_config = lambda self: None

FAIL = []
def ok(c, n, i=""):
    print(("  PASS  " if c else "  FAIL  ") + n + (f"   [{i}]" if i else ""))
    if not c: FAIL.append(n)

def mean_lum(w):
    """整窗平均亮度（只算不透明像素）。"""
    pm = w.grab()
    im = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    tot = n = 0
    for y in range(0, im.height(), 4):
        for x in range(0, im.width(), 4):
            c = im.pixelColor(x, y)
            if c.alpha() < 40:
                continue
            tot += c.red() + c.green() + c.blue()
            n += 1
    return (tot / max(1, n) / 3.0), n

def main():
    w = pet.PetWindow()
    w.show()
    w.timer.stop()           # 停掉主循环：呼吸/挤压会让每帧高矮不一，量出来没法比
    w.t = 0.0
    w.fx_sx, w.fx_sy, w.fx_dx, w.fx_dy, w.fx_rot = 1.0, 1.0, 0.0, 0.0, 0.0
    w.clip = None            # 用静态图，画面稳定好比
    w.cross_t = 0.0
    w.prev_key = None

    print("\n【亮度】")
    w.set_brightness(0.0)
    app.processEvents()
    d, n0 = mean_lum(w)
    w.set_brightness(0.35)
    app.processEvents()
    b, n1 = mean_lum(w)
    ok(b > d + 3, "从 0% 提到 35%，画面确实变亮",
       f"{d:.1f} → {b:.1f}（不透明像素 {n0}/{n1}）")
    w.set_brightness(0.0)
    app.processEvents()
    d2, _ = mean_lum(w)
    # 呼吸/眨眼会让每帧内容略有差别，只看「确实降回来了」，不追 1:1
    ok(d2 < b - 3.0, "改回 0% 亮度回落", f"35%={b:.1f} → 0%={d2:.1f}（原始 {d:.1f}）")

    print("\n【大小】")
    # 先把尺寸换到别的档再换回来，保证真有 resize（值没变时 Qt 不重排，
    # 量出来的还是上一档的画面，会得出「小 > 中」这种假结论）
    w.set_size(0.9)
    app.processEvents()
    w.set_size(0.55)
    app.processEvents()
    hs = []
    for mult in (0.55, 0.7, 0.9):
        w.set_size(mult)
        app.processEvents()
        _, n = mean_lum(w)          # 只用来触发一次绘制
        # 真正量：窗口里不透明像素的包围盒高度
        pm = w.grab()
        im = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        top = bot = None
        for y in range(im.height()):
            row = 0
            for x in range(0, im.width(), 3):
                if im.pixelColor(x, y).alpha() > 40:
                    row += 1
            if row > 1:
                if top is None:
                    top = y
                bot = y
        h = (bot - top + 1) if top is not None else 0
        hs.append((mult, h, w.cur_h))
        print(f"     {mult}: 画面内容高 {h}px / cur_h {w.cur_h}")
    ok(hs[0][1] < hs[1][1] < hs[2][1], "小 < 中 < 大，画面真的跟着变",
       " → ".join(str(h) for _, h, _ in hs))
    # 鲨鱼图上下有透明边，内容包围盒本来就矮一截；看比例是否跟 cur_h 同步
    ratio_img = hs[2][1] / max(1, hs[0][1])
    ratio_h = hs[2][2] / max(1, hs[0][2])
    ok(abs(ratio_img - ratio_h) < 0.08, "内容放大比例跟着 cur_h 走（不是空转窗口）",
       f"画面 {ratio_img:.2f}x vs 高度 {ratio_h:.2f}x")

    print("\n【气泡开关】")
    w.set_size(0.7)
    w.say("这是一句测试用的台词，用来验证气泡开关", force=True, speak=False)
    app.processEvents()
    on = bool(w.bubble_text)
    w.set_bubble_on(False)
    app.processEvents()
    ok(on, "开气泡时台词落在 bubble_text 上")
    ok(w.bubble_text == "", "关气泡后立刻清空，不留半句在屏幕上")
    w.set_bubble_on(True)

    w.close()
    print("\n" + "=" * 58)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for f in FAIL: print("  -", f)
        return 1
    print("视觉类设置都真的作用到画面上了")
    return 0

def run():
    code = 1
    try:
        code = main()
    except Exception:
        import traceback; traceback.print_exc()
    QTimer.singleShot(80, lambda: app.exit(code))
QTimer.singleShot(150, run)
sys.exit(app.exec())
