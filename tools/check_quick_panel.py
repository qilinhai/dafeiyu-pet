# -*- coding: utf-8 -*-
"""双击面板自检：七个入口摆得对、喂食能翻页、日报拼得出来。

跑法（在 dafeiyu-pet-main 目录下）：
    ..\\venv\\Scripts\\python.exe tools\\check_quick_panel.py

只读：不碰 pet_memory / anim_config / config.json，不改任何真实存档。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from PySide6.QtWidgets import QApplication, QToolButton      # noqa: E402

import pet                                                    # noqa: E402

OK, FAIL = [], []


def check(name, cond, detail=""):
    (OK if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))


class FakePet:
    """只提供 _build_daily_report 用得到的字段，不构造整个桌宠。"""
    WEATHER_MAP = pet.PetWindow.WEATHER_MAP
    WMO_MAP = pet.PetWindow.WMO_MAP
    _build_daily_report = pet.PetWindow._build_daily_report
    _weather_line = pet.PetWindow._weather_line

    def __init__(self):
        self.said = []
        self._weather_cache = None

    def say(self, text, **kw):
        self.said.append(text)


def main():
    app = QApplication.instance() or QApplication([])
    hits = []

    panel = pet.QuickPanel({
        "report": lambda: hits.append("report"),
        "board": lambda: hits.append("board"),
        "act": lambda: hits.append("act"),
        "yoyo": lambda: hits.append("yoyo"),
        "clean": lambda: hits.append("clean"),
    }, on_pick=lambda f: hits.append("feed:" + f))

    main_btns = [b for b in panel._page_main.findChildren(QToolButton) if b.text() != "✕"]
    labels = [b.text() for b in main_btns]
    want = ["播放日报", "抖音看板", "喂食", "表演个节目", "打开悠悠", "释放资源", "静音"]
    check("主页面七个按钮，顺序和文案对得上", labels == want, " / ".join(labels))
    check("每个按钮都有提示", all(b.toolTip() for b in main_btns))
    check("初始停在主页面", panel._page_main.isVisible() or not panel._page_food.isVisible())

    # 尺寸：够点、又不至于挡住半屏
    panel.popup_at(400, 500)
    w, h = panel.width(), panel.height()
    check("主面板尺寸合理", 200 <= w <= 700 and 40 <= h <= 140, f"{w}x{h}")

    # 贴边不越界
    scr = QApplication.primaryScreen().availableGeometry()
    for x in (scr.left(), scr.right()):
        panel.popup_at(x, scr.top() + 5)
        g = panel.geometry()
        inside = (g.left() >= scr.left() - 1 and g.right() <= scr.right() + 1
                  and g.top() >= scr.top() - 1)
        check(f"贴边弹出不越界（x={x}）", inside, f"{g.left()}..{g.right()}")

    # 四个动作入口
    by_label = {b.text(): b for b in main_btns}
    for label, key in (("播放日报", "report"), ("抖音看板", "board"),
                       ("表演个节目", "act"), ("打开悠悠", "yoyo"),
                       ("释放资源", "clean")):
        hits.clear()
        by_label[label].click()
        check(f"「{label}」调到 {key}", hits == [key], str(hits))
        check(f"「{label}」点完收起面板", not panel.isVisible())

    # 喂食翻页
    panel.popup_at(400, 500)
    by_label["喂食"].click()
    food_btns = [b for b in panel._page_food.findChildren(QToolButton) if b.text() != "←"]
    check("「喂食」翻到食物页", panel._page_food.isVisible() and not panel._page_main.isVisible())
    check("食物页五种食物都在", [b.text() for b in food_btns] == pet.FOODS,
          " ".join(b.text() for b in food_btns))
    fw, fh = panel.width(), panel.height()
    check("食物页尺寸合理", 200 <= fw <= 700 and 40 <= fh <= 140, f"{fw}x{fh}")
    hits.clear()
    food_btns[0].click()
    check("点食物回调到 on_pick", hits == ["feed:" + pet.FOODS[0]], str(hits))
    check("喂完面板收起", not panel.isVisible())

    panel.popup_at(400, 500)
    by_label["喂食"].click()
    back = [b for b in panel._page_food.findChildren(QToolButton) if b.text() == "←"][0]
    back.click()
    check("← 回到主页面", panel._page_main.isVisible() and not panel._page_food.isVisible())

    # 日报内容
    fp = FakePet()

    # 天气超时：budget 内拿不到就返回空，绝不干等
    import time as _t
    fp._weather_cache = None
    t0 = _t.monotonic()
    wline = fp._weather_line(budget=0.05)
    cost = _t.monotonic() - t0
    check("天气超时立刻放行", cost < 1.0, f"{cost:.2f}s / 返回 {wline!r}")

    text = fp._build_daily_report()
    check("日报非空", bool(text and text.strip()), repr(text[:40]))
    check("日报带日期和星期", "月" in text and "星期" in text)
    check("日报没有 None/异常痕迹", "None" not in text and "Error" not in text)
    check("日报不再念待办", "待办" not in text and "4105337861850" not in text)
    print("  日报原文：" + text)

    # 播报链路（后台拼 → 信号 → 逐段念）在 check_daily_takes.py 里整条测，
    # 这里只确认面板的「播放日报」按钮确实接到了这个方法上。
    hit = []
    panel2 = pet.QuickPanel({
        "report": lambda: hit.append("report"),
        "board": lambda: None, "act": lambda: None, "yoyo": lambda: None,
    }, on_pick=None)
    [b for b in panel2._page_main.findChildren(QToolButton) if b.text() == "播放日报"][0].click()
    check("面板「播放日报」接到日报", hit == ["report"], str(hit))

    print()
    print(f"通过 {len(OK)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for name in FAIL:
            print("  未通过：" + name)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
