# -*- coding: utf-8 -*-
"""tools/check_menu.py —— 右键菜单自检（不需要真弹窗）。

查的是三件用户报过的事：
  1. 贴屏幕右边/下边弹，菜单不许跑到屏幕外，也不许从光标处跳到远处；
  2. 二级、三级子菜单展开后不许互相压住（压住就会「选不中」）；
  3. 深层菜单项真的能被点到（命中测试 + 回调触发）。

只在内存里排布，不 show，所以不会干扰正在跑的桌宠。
"""
from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QApplication

import wb_menu as wb

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_menu_shot")
PASS, FAIL = [], []


def ok(cond, msg, extra=""):
    (PASS if cond else FAIL).append(msg + (f"  [{extra}]" if extra else ""))
    print(("  ok  " if cond else "  FAIL") + "  " + msg + (f"  [{extra}]" if extra else ""))


def build_menu():
    """跟 pet.py 的 _build_menu 同构，文案用真的（宽度才有意义）。"""
    m = wb.WMenu()
    m.addAction("郑州今日", lambda checked=False: None)
    m.addSeparator()

    act = m.addMenu("互动")
    act.addAction("说句话", lambda checked=False: None)
    feed = act.addMenu("喂食")
    feed.addAction("拖文件到鱼身上 → 回收站").setEnabled(False)
    for food in ("小虾干", "面包屑", "一块蛋糕", "珍珠奶茶"):
        feed.addAction(food, lambda checked=False, f=food: None)
    anim = act.addMenu("动作")
    anim.addAction("normal · 待机为主")
    den = anim.addMenu("节奏")
    for lb in ("安静 0.4", "正常 1.0", "热闹 1.8"):
        den.addAction(lb).setCheckable(True)
    cls = anim.addMenu("哪类动作")
    for lb in ("小动作（14）", "整段表演（22）", "大场面（6）", "节日动作（9）"):
        cls.addAction(lb).setCheckable(True)
    act.addAction("睡觉", lambda checked=False: None)
    a = act.addAction("碎碎念跟着对话走")
    a.setCheckable(True)
    a.setChecked(True)

    look = m.addMenu("外观")
    mode = look.addMenu("模式")
    for lb in ("自由散步", "跟随鼠标", "原地待着"):
        x = mode.addAction(lb)
        x.setCheckable(True)
    size = look.addMenu("大小")
    for lb in ("小 0.45", "中 0.55", "大 0.7", "特大 0.85"):
        size.addAction(lb).setCheckable(True)
    bright = look.addMenu("亮度")
    for lb in ("原样 0%", "微亮 15%", "亮 25%", "很亮 35%"):
        bright.addAction(lb).setCheckable(True)
    look.addSeparator()
    for lb in ("显示气泡", "屏幕边缘探头", "鼠标穿透（点不到它）", "窗口置顶", "开机自启"):
        look.addAction(lb).setCheckable(True)

    phone = m.addMenu("手机")
    phone.addAction("未连接（adb 就绪）").setEnabled(False)
    phone.addAction("连接手机（无线调试：IP / 端口 / 配对码）")
    phone.addAction("自动找一下（mDNS）")
    phone.addAction("启动通知监听")

    douyin = m.addMenu("抖音收藏")
    douyin.addAction("今天 6 条 · 累计 214 条").setEnabled(False)
    t = douyin.addMenu("今天的收藏（6）")
    for i in range(6):
        t.addAction(f"这条收藏的标题大概有这么长 {i}")
    douyin.addMenu("最近 5 条")

    voice = m.addMenu("语音")
    voice.addAction("念出台词").setCheckable(True)
    voice.addAction("自动碎嘴也念（费额度）").setCheckable(True)
    vsub = voice.addMenu("音色")
    for lb in ("青叔 · 低沉", "小鱼 · 女声", "讲书人 · 温和"):
        vsub.addAction(lb).setCheckable(True)
    voice.addAction("试听当前音色")
    sp = voice.addMenu("语速")
    for lb in ("慢 0.9", "正常 1.0", "偏快 1.15", "快 1.3"):
        sp.addAction(lb).setCheckable(True)
    voice.addAction("设置 Breeze Key")

    mem = m.addMenu("记忆")
    mem.addAction("它还记着啥")
    mem.addAction("记忆与成长")
    mem.addAction("现在推一句（试试推测）")

    think = m.addMenu("说话前想一下")
    think.addAction("总闸（关了就全用模板）").setCheckable(True)
    think.addAction("手机通知").setCheckable(True)
    think.addAction("今天说了 12 条，约 0.021 元").setEnabled(False)

    m.addSeparator()
    m.addAction("设置…（人设 / 语音 / 移机）")
    m.addAction("退出")
    return m


def layout_at(m, pos):
    """按真实路径排布，但不 show（不打扰正在跑的桌宠）。"""
    scr = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
    m._anchor = QPoint(pos)
    m._avail = scr.availableGeometry()
    m._open_map = {}
    m._hover = (None, None)
    m._measure_tree(m)
    m._relayout()
    return m._avail


def open_path(m, *titles):
    """按标题一路展开子菜单（模拟鼠标停在那几行上）。"""
    cur = m
    for title in titles:
        for i, it in enumerate(cur._items):
            if it.sub is not None and it.text() == title:
                m._open_sub(cur, it.sub)
                cur = it.sub
                break
        else:
            raise AssertionError(f"菜单里没有 [{title}]")
    return cur


def press_area(a, b):
    inter = a.intersected(b)
    if inter.isNull():
        return 0, 0
    return inter.width(), inter.height()


def pressed(a, b):
    """真压住：两个方向都咬进 6px 以上。4px 的贴合边是有意留的。"""
    w, h = press_area(a, b)
    return w > 6 and h > 6


def panel_rects(m):
    return [p._grect for p in m._chain()]


def dump(m, name):
    """把菜单画到灰底上存 PNG：核对观感用。"""
    os.makedirs(OUT, exist_ok=True)
    grab = m.grab()
    img = QImage(grab.size(), QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(214, 216, 220))
    p = QPainter(img)
    p.drawPixmap(0, 0, grab)
    p.end()
    path = os.path.join(OUT, name)
    img.save(path)
    return path


def check_anchor(m, label, pos, ex_margin=1):
    avail = layout_at(m, pos)
    rects = panel_rects(m)
    inside = all(
        avail.left() - ex_margin <= r.left() and r.right() <= avail.right() + ex_margin
        and avail.top() - ex_margin <= r.top() and r.bottom() <= avail.bottom() + ex_margin
        for r in rects)
    ok(inside, f"{label}：一级菜单整块在屏幕内", f"root={rects[0].getRect()}")
    root = rects[0]
    # 「跳走」的判据：菜单左缘离光标超过一个菜单宽，就说明它被推到屏幕另一头去了
    # （老代码左退两级宽度，实测能推出去 500 多像素）。
    dx = abs(root.left() - pos.x())
    ok(dx <= root.width(), f"{label}：一级菜单没有从光标处跳走",
       f"偏移 {dx}px / 宽 {root.width()}")
    # 贴右边缘时，右缘要对齐屏幕右缘，而不是跑到中间
    if pos.x() > avail.right() - 200:
        ok(avail.right() - root.right() <= 2,
           f"{label}：贴右边时右缘贴住屏幕", f"右边缘差 {avail.right() - root.right()}")
    return root


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    scr = QGuiApplication.primaryScreen().availableGeometry()
    print(f"屏幕可用区: {scr.getRect()}\n")

    # ---------------- 1. 四角 + 右缘，一级菜单位置
    print("[1] 一级菜单位置")
    for label, pos in (("左上角", QPoint(scr.left() + 2, scr.top() + 2)),
                       ("右上角", QPoint(scr.right() - 2, scr.top() + 2)),
                       ("右下角", QPoint(scr.right() - 2, scr.bottom() - 2)),
                       ("右缘中部", QPoint(scr.right() - 2, scr.center().y())),
                       ("左下角", QPoint(scr.left() + 2, scr.bottom() - 2))):
        check_anchor(build_menu(), label, pos)

    # ---------------- 2. 贴右边，逐级展开不重叠
    print("\n[2] 贴右边 2/3/4 级展开")
    m = build_menu()
    avail = layout_at(m, QPoint(scr.right() - 2, scr.center().y()))
    open_path(m, "互动")
    m._relayout()
    r1 = panel_rects(m)
    w, _ = press_area(r1[0], r1[1])
    ok(len(r1) == 2 and w <= 6, "互动：二级面板没压住一级", f"水平咬合 {w}px")

    open_path(m, "互动", "动作")
    m._relayout()
    r2 = panel_rects(m)
    ok(len(r2) == 3, "互动→动作：三级已经展开", f"{len(r2)} 层")
    ok(not pressed(r2[0], r2[2]), "三级没压住一级",
       f"咬合 {press_area(r2[0], r2[2])}")
    w2, _ = press_area(r2[1], r2[2])
    ok(len(r2) == 3 and w2 <= 6, "三级与二级只有贴合边", f"水平咬合 {w2}px")

    open_path(m, "互动", "动作", "节奏")
    m._relayout()
    r3 = panel_rects(m)
    ok(len(r3) == 4, "四级已经展开", f"{len(r3)} 层")
    bad = [(i, j) for i in range(len(r3)) for j in range(i + 2, len(r3))
           if pressed(r3[i], r3[j])]
    ok(not bad, "四级链上互不相压（间隔两级以上）", f"相压组合 {bad}")
    ok(all(avail.left() <= r.left() and r.right() <= avail.right() for r in r3),
       "四级展开后全部在屏幕内", f"{[r.getRect() for r in r3]}")
    shot = dump(m, "right_edge_4levels.png")
    print(f"      截图: {shot}")

    # ---------------- 3. 深层项真的能点到
    print("\n[3] 深层命中 + 回调")
    hit_ok = []
    m2 = build_menu()
    fired = []
    layout_at(m2, QPoint(scr.right() - 2, scr.center().y()))
    deep = open_path(m2, "外观", "模式")
    deep._items[2]._cb = lambda checked=False: fired.append("原地待着")
    m2._relayout()
    row = deep._rows[2]
    # 用面板自己的窗口矩形 + 行偏移换算成窗口坐标去点
    pt = QPoint(deep._wrect.left() + 30,
                deep._wrect.top() + row[0] + row[1] // 2)
    panel_hit = m2._panel_at(pt)
    hit_ok.append(panel_hit is deep)
    ok(panel_hit is deep, "点最深一层拿到的就是它自己（没有被父级截走）",
       f"命中={getattr(panel_hit, '_title', None)!r}")
    idx = m2._row_at(panel_hit, pt)
    ok(idx == 2, "命中行号正确", f"idx={idx}")

    class _E:
        def position(self): return QPointF(pt)

        def button(self): return Qt.LeftButton

    m2.mouseReleaseEvent(_E())
    ok(m2._pending is deep._items[2], "点中的项被挂起，等菜单关掉再执行")
    m2._pending.trigger()
    m2._pending = None
    ok(fired == ["原地待着"], "触发后回调只走一次", f"{fired}")

    # ---------------- 4. 同一行反复 hover 不该把子菜单抖掉
    print("\n[4] hover 稳定性")
    m3 = build_menu()
    layout_at(m3, QPoint(scr.right() - 2, scr.center().y()))
    sub = open_path(m3, "语音")
    ok(m3._opens.get(m3) is sub, "语音子菜单已挂在根上")
    rowy = sub._anchor_row
    m3._set_hover(None, None)
    m3._set_hover(m3, rowy)
    ok(m3._opens.get(m3) is sub, "停在同一行，子菜单还在（没被自己关掉）")
    m3._set_hover(m3, 0)
    ok(m3._opens.get(m3) is None, "移到别的行，子菜单收起")

    # ---------------- 5. 截图存档：浅色 / 深色
    print("\n[5] 观感截图")
    for mode in ("light", "dark"):
        wb.set_theme(mode)
        m4 = build_menu()
        layout_at(m4, QPoint(scr.center().x(), scr.center().y()))
        open_path(m4, "语音", "音色")
        m4._relayout()
        chain = m4._chain()
        m4._set_hover(chain[-1], 1)   # 光标停最深一层，父项应保持高亮
        m4._relayout()
        print("      " + dump(m4, f"theme_{mode}_3levels.png"))
    wb.set_theme("auto")

    # ---------------- 6. exec 端到端（offscreen 子进程，不闪窗）
    print("\n[6] exec 端到端")
    import subprocess
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "--exec-test"],
                       env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for line in (r.stdout or "").splitlines():
        if line.strip():
            print("      " + line.strip())
    ok(r.returncode == 0, "弹出→点击→回调 全链路跑通",
       f"exit={r.returncode}" + (f" {r.stderr[-300:]}" if r.returncode else ""))

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print("  ✗ " + f)
    return 1 if FAIL else 0


def exec_e2e():
    """真跑一次 exec：弹出 → 点 → 关 → 回调。用 offscreen，不闪窗口。"""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    m = wb.WMenu()
    fired = []
    m.addAction("第一项", lambda checked=False: fired.append(1))
    m.addAction("第二项", lambda checked=False: fired.append(2))
    sub = m.addMenu("子菜单")
    sub.addAction("里面", lambda checked=False: fired.append(9))

    def click():
        oy, h = m._rows[1]
        pt = QPoint(m._wrect.left() + 40, m._wrect.top() + oy + h // 2)

        class _E:
            def position(self): return QPointF(pt)

            def button(self): return Qt.LeftButton

        m.mouseReleaseEvent(_E())

    QTimer.singleShot(120, click)
    m.exec(QPoint(200, 200))
    ok(fired == [2], "exec 端到端：点第二项，回调只触发一次", f"{fired}")
    ok(not m.isVisible(), "exec 返回前菜单已经隐藏")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    if "--exec-test" in sys.argv:
        sys.exit(exec_e2e())
    sys.exit(main())
