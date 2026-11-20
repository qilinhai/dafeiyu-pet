# -*- coding: utf-8 -*-
"""tools/preview_menu_live.py —— 在真屏幕上弹一次自绘菜单并截图，1.8 秒后自己退出。

离屏 grab 是「把这棵树画成图片」，证不了 translucent 弹窗的真实合成效果
（真窗口才有真正的桌面阴影/透明）。这个脚本补上那一刀：真弹、真截、真退。
不会碰正在跑的桌宠，也不写任何配置。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

import wb_menu as wb
from check_menu import build_menu

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_menu_shot")


def main():
    app = QApplication(sys.argv)
    scr = QGuiApplication.primaryScreen().availableGeometry()
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    wb.set_theme(mode)

    m = build_menu()
    # 贴右下角弹：这正是用户报「位置不对」的场景
    anchor = QPoint(scr.right() - 30, scr.bottom() - 120)

    def show():
        m.popup(anchor)

    def hover():
        # 停在「语音」这一行，让二级自己展开
        for i, it in enumerate(m._items):
            if it.text() == "语音":
                m._set_hover(m, i)
                break

    def hover2():
        sub = m._open_map.get(m)
        if sub is None:
            return
        for i, it in enumerate(sub._items):
            if it.text() == "音色":
                m._set_hover(sub, i)
                break

    def shot():
        os.makedirs(OUT, exist_ok=True)
        path = os.path.join(OUT, f"live_{mode}.png")
        QGuiApplication.primaryScreen().grabWindow(0).save(path)
        print(path)

    QTimer.singleShot(250, show)
    QTimer.singleShot(650, hover)
    QTimer.singleShot(1050, hover2)
    QTimer.singleShot(1550, shot)
    QTimer.singleShot(1750, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
