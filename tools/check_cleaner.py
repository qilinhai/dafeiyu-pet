# -*- coding: utf-8 -*-
"""冒烟：两个「释放资源」入口是否都通到 cleaner。

  1. 右键菜单「释放资源（清掉挂着的）」
  2. 双击面板「释放资源」

真 new 一个 PetWindow（离屏，不写配置），把 cleaner.run 换成假数据，
验证：入口存在 → 触发 → 后台线程 → 信号回主线程 → 播报文案。
全程不杀任何进程。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = BASE   # 绿色版：脚本的上一层就是程序目录
sys.path.insert(0, BASE)
sys.path.insert(0, APP)
os.chdir(APP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer                  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolButton       # noqa: E402

app = QApplication(sys.argv)
import pet                                                     # noqa: E402
import cleaner                                                 # noqa: E402

LIVE = "--live" in sys.argv
pet.PetWindow.save_config = lambda self: None

err = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not cond:
        err.append(name)


def find(menu, needle):
    """整棵菜单树里找文案包含 needle 的项。"""
    for it in menu._items:
        try:
            if needle in (it.text() or ""):
                return it
        except AttributeError:
            pass
        if getattr(it, "sub", None) is not None:
            r = find(it.sub, needle)
            if r is not None:
                return r
    return None


def fire(w, trigger, timeout=4000):
    """触发一次，等 cleanup_done 回来。"""
    loop = QEventLoop()
    box = []

    def _on(line):
        box.append(line)
        loop.quit()

    w.cleanup_done.connect(_on)
    QTimer.singleShot(timeout, loop.quit)
    trigger()
    loop.exec()
    try:
        w.cleanup_done.disconnect(_on)
    except (RuntimeError, TypeError):
        pass
    return box


def run():
    try:
        w = pet.PetWindow()

        # 默认用假结果，不碰真进程；加 --live 才真杀（只清白名单内且无窗口的）
        if not LIVE:
            cleaner.run = lambda dry=False: {"ok": True, "freed_mb": 1240.0,
                                             "count": 7, "killed": ["doubao.exe"],
                                             "secs": 0.1}

        # ---- 入口一：右键菜单
        it = find(w._build_menu(), "释放资源（")
        check("右键菜单有「释放资源」", it is not None, it.text() if it else "没找到")
        if it is not None:
            got = fire(w, it.trigger)
            check("右键「释放资源」跑完并播报", bool(got), got[0] if got else "无信号")
            if not LIVE:      # 真跑时可能只清出 100MB，不硬卡 GB 档
                check("播报是 GB 档", bool(got) and "GB" in got[0], got[0] if got else "")

        # ---- 入口二：双击面板
        btns = [b for b in w.quick_panel._page_main.findChildren(QToolButton)
                if b.text() == "释放资源"]
        check("双击面板有「释放资源」", len(btns) == 1)
        if btns:
            got2 = fire(w, btns[0].click)
            check("面板「释放资源」跑完并播报", bool(got2), got2[0] if got2 else "无信号")
            check("点完面板收起", not w.quick_panel.isVisible())

        # ---- 闸门要复位，否则下次点了没反应
        check("_cleaning 闸门已复位", w._cleaning is False)
    except Exception:
        import traceback
        traceback.print_exc()
        err.append("exception")
    app.quit()


QTimer.singleShot(200, run)
app.exec()

# ---- 文案分档
for res, want in (({"ok": True, "freed_mb": 1234.0, "count": 7}, "GB"),
                  ({"ok": True, "freed_mb": 0.0, "count": 0}, "干净"),
                  ({"ok": True, "freed_mb": 12.0, "count": 2}, "MB"),
                  ({"ok": True, "freed_mb": 0.0, "count": 3}, "结束 3 个")):
    line = cleaner.say_line(res)
    check(f"文案 freed={res['freed_mb']:>7} n={res['count']} → 含「{want}」", want in line, line)

print("RESULT", "PASS" if not err else "FAIL")
sys.exit(1 if err else 0)
