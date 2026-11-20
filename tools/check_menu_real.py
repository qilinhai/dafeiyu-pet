# -*- coding: utf-8 -*-
"""冒烟：用真的 PetWindow 构建右键菜单，量一遍所有子菜单宽度 + 抓一张图。

只管「真实菜单树」这层：文案长度会不会被 MAX_W 截、展开会不会报错。

注意：会真的 new 一个 PetWindow —— 屏幕上会闪出第二只鱼，0.5 秒后自己退出，
不写配置、不占桥接端口（PetWindow 本身不起 bridge）。
"""
import os, sys
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = BASE   # 绿色版：脚本的上一层就是程序目录
sys.path.insert(0, BASE)
sys.path.insert(0, APP)
os.chdir(APP)

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
import pet
import wb_menu as wb

err = []


def walk(m, depth=0, out=None):
    out = out if out is not None else []
    m._measure()
    clipped = m._panel_w >= wb.MAX_W
    out.append((depth, m._title or "(root)", m._panel_w, m._panel_h, len(m._items), clipped))
    for it in m._items:
        if it.sub is not None:
            walk(it.sub, depth + 1, out)
    return out


def build():
    try:
        w = pet.PetWindow()
        m = w._build_menu()
        scr = QGuiApplication.primaryScreen().availableGeometry()
        m._anchor = QPoint(scr.right() - 40, 120)
        m._avail = scr
        m._open_map = {}
        m._hover = (None, None)
        m._measure_tree(m)

        rows = walk(m)
        bad = [r for r in rows if r[5]]
        print(f"菜单节点 {len(rows)} 个，宽度被截的 {len(bad)} 个")
        for d, t, pw, ph, n, clip in rows:
            flag = "  <-- 被 MAX_W 截断" if clip else ""
            print(f"  {'  ' * d}- {t}: {pw}x{ph} 项={n}{flag}")

        # 展开最长的一条链：手机 → 直接子项里没有三级，改展「互动 → 动作 → 节奏」
        def find(menu, title):
            for it in menu._items:
                if it.sub is not None and it.text() == title:
                    return it.sub
            return None

        chain = [m]
        for title in ("互动", "动作", "节奏"):
            nxt = find(chain[-1], title)
            if nxt is None:
                break
            m._open_sub(chain[-1], nxt)
            chain.append(nxt)
        m._relayout()
        p = os.path.join(APP, "tools", "_menu_shot", "smoke_real.png")
        m.grab().save(p)
        print("GRAB", p, m.geometry().getRect(), "levels=", len(m._chain()))
    except Exception:
        import traceback
        traceback.print_exc()
        err.append(1)
    QTimer.singleShot(200, app.quit)


QTimer.singleShot(300, build)
app.exec()
sys.exit(1 if err else 0)
