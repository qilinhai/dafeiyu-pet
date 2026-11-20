# -*- coding: utf-8 -*-
"""wb_menu.py —— 自绘右键菜单（贴合桌面端应用的质感）。

为什么不用 QMenu
----------------
1. 贴屏幕右缘时 QMenu 只按「主菜单自身宽度」避让，避完右缘刚好顶到边界，
   子菜单右侧一点空间都没有，只能往左折。
2. 往左折的位置是按「父菜单左缘 - 子菜单宽」算的，一旦子菜单比父菜单宽，
   折完就压在父菜单身上。鼠标从父行移到子菜单要先横穿父菜单，父级立刻判
   「离开」，子菜单当场关掉 —— 这就是「折叠后选不中」。
3. 老代码的绕法是「把整张菜单往左退两级的宽度」。退得动，但光标在右边、
   菜单蹦到屏幕中间，位置是错的（用户原话：弹窗位置不对）。
4. 想要圆角 + 柔和投影 + 紧凑行高，QMenu 的原生 popup 是不透明矩形，圆角
   一定带黑角。

这里的做法
----------
整棵菜单树画在**一个**窗口里（Popup 窗口，点外面自动关）。每一级子菜单
单独挑左/右：哪边放得下就走哪边，于是永远不会压住父级。位置全部自己算，
主菜单就是贴着光标出现的。

对外接口刻意跟 QMenu 对齐（addAction / addSeparator / addMenu / exec），
pet.py 那边只换了构造类，其余代码不用动。
"""
from __future__ import annotations

import inspect
import sys
import traceback
from typing import Callable, Optional

from PySide6.QtCore import (QEventLoop, QObject, QPoint, QPointF, QRect, QRectF,
                            QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication,
                           QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import QWidget

# ---------------------------------------------------------------- 尺寸
ROW_H = 26          # 单行高
SEP_H = 8           # 分隔线占位
PAD_V = 6           # 面板上下内边距
PAD_H = 7           # 面板左右内边距
CHK_W = 20          # 左侧勾选栏（不可勾的项也留白，文字才对齐）
ARROW_W = 14        # 子菜单箭头栏
RADIUS = 7
SHADOW = 16         # 画布四周给投影留的余量
MIN_W = 148
MAX_W = 400
FONT_PX = 13
HOVER_MS = 110      # 悬停多久展开子菜单


# ---------------------------------------------------------------- 主题
class _Theme:
    __slots__ = ("bg", "border", "text", "dim", "hover", "sep", "arrow", "accent")

    def __init__(self, bg, border, text, dim, hover, sep, arrow, accent):
        self.bg = bg
        self.border = border
        self.text = text
        self.dim = dim
        self.hover = hover
        self.sep = sep
        self.arrow = arrow
        self.accent = accent


_LIGHT = _Theme(
    bg=QColor(255, 255, 255, 250),
    border=QColor(0, 0, 0, 24),
    text=QColor(30, 31, 34),
    dim=QColor(136, 138, 145),
    hover=QColor(0, 0, 0, 15),
    sep=QColor(0, 0, 0, 18),
    arrow=QColor(134, 136, 142),
    accent=QColor(37, 99, 235),
)

_DARK = _Theme(
    bg=QColor(45, 46, 50, 251),
    border=QColor(255, 255, 255, 20),
    text=QColor(233, 234, 237),
    dim=QColor(146, 148, 156),
    hover=QColor(255, 255, 255, 22),
    sep=QColor(255, 255, 255, 18),
    arrow=QColor(150, 152, 158),
    accent=QColor(96, 165, 250),
)

_MODE = "auto"          # auto / light / dark


def set_theme(mode: str) -> None:
    """auto 跟着 Windows 的应用主题走，light / dark 强制。"""
    global _MODE
    _MODE = mode if mode in ("auto", "light", "dark") else "auto"


def _system_dark() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return int(val) == 0
    except Exception:      # noqa: BLE001  读不到就当浅色
        return False


def theme() -> _Theme:
    if _MODE == "dark":
        return _DARK
    if _MODE == "light":
        return _LIGHT
    return _DARK if _system_dark() else _LIGHT


_FAMILY = "Microsoft YaHei UI"
_font_cache: Optional[QFont] = None
_fm_cache: Optional[QFontMetrics] = None


def _font() -> QFont:
    global _font_cache, _fm_cache
    if _font_cache is None:
        f = QFont(_FAMILY)
        f.setPixelSize(FONT_PX)
        f.setHintingPreference(QFont.PreferDefaultHinting)
        _font_cache = f
        _fm_cache = QFontMetrics(f)
    return _font_cache


def _fm() -> QFontMetrics:
    _font()
    return _fm_cache


# ---------------------------------------------------------------- 菜单项
class WAction(QObject):
    """对齐 QAction 的常用面：setEnabled / setCheckable / setChecked / triggered。"""

    triggered = Signal(bool)

    def __init__(self, text: str = "", callback: Optional[Callable] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._text = str(text or "")
        self._cb = callback
        self.sub: Optional["WMenu"] = None
        self.sep = False
        self._checkable = False
        self._checked = False
        self._enabled = True

    # -- 读
    def text(self) -> str:
        return self._text

    def isEnabled(self) -> bool:
        return self._enabled

    def isCheckable(self) -> bool:
        return self._checkable

    def isChecked(self) -> bool:
        return self._checked

    def menu(self) -> Optional["WMenu"]:
        return self.sub

    # -- 写
    def setText(self, t: str):
        self._text = str(t or "")

    def setEnabled(self, on: bool):
        self._enabled = bool(on)
        return self

    def setDisabled(self, on: bool):
        self._enabled = not bool(on)
        return self

    def setCheckable(self, on: bool):
        self._checkable = bool(on)
        return self

    def setChecked(self, on: bool):
        self._checked = bool(on)
        return self

    def toggle(self):
        self._checked = not self._checked
        return self

    def trigger(self):
        """点了：可勾的先翻状态，再按 (checked) 调回调 / 发信号。"""
        if not self._enabled or self.sep:
            return
        if self._checkable:
            self._checked = not self._checked
        self.triggered.emit(self._checked)
        _safe_call(self._cb, self._checked)


def _safe_call(cb, checked: bool):
    """回调签名五花八门：有的收 checked，有的一个参数都不收。别为这个报错。"""
    if cb is None:
        return
    try:
        params = inspect.signature(cb).parameters.values()
        positional = [p for p in params
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        varargs = any(p.kind == p.VAR_POSITIONAL for p in params)
        argc = 1 if (positional or varargs) else 0
    except (TypeError, ValueError):
        argc = 1
    try:
        cb(checked) if argc else cb()
    except TypeError:
        # signature 骗人的情况（装饰器包过一层）：退一步再试
        try:
            cb()
        except Exception:      # noqa: BLE001
            traceback.print_exc()
    except Exception:          # noqa: BLE001  一个菜单项炸了不该带崩整只鱼
        traceback.print_exc()


# ---------------------------------------------------------------- 菜单
class WMenu(QWidget):
    """一棵菜单树。只有根节点会被 show，子节点只作为布局数据存在。"""

    aboutToShow = Signal()

    def __init__(self, title: str = "", parent=None):
        super().__init__(None)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint
                            | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setMouseTracking(True)
        self.setCursor(Qt.ArrowCursor)

        self._title = str(title or "")
        self._items: list[WAction] = []
        self._parent_menu: Optional[WMenu] = None

        # 布局结果
        self._rows: list[tuple[int, int]] = []     # [(相对面板 top 的 y, 高)]
        self._panel_w = MIN_W
        self._panel_h = PAD_V * 2
        self._wrect = QRect()      # 窗口内坐标（画图用）
        self._grect = QRect()      # 屏幕坐标（算位置用）
        self._anchor_row = 0       # 本菜单挂在父菜单的第几行

        # 以下只在根节点上有意义
        self._open_map: dict[WMenu, WMenu] = {}
        self._anchor: Optional[QPoint] = None
        self._avail = QRect()
        self._hover: tuple = (None, None)
        self._pending: Optional[WAction] = None
        self._loop: Optional[QEventLoop] = None
        self._pending_sub = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(HOVER_MS)
        self._timer.timeout.connect(self._fire_pending_sub)

    # ------------------------------------------------ 构建（对齐 QMenu）
    def addAction(self, text: str, callback=None, *args, **kw) -> WAction:
        it = WAction(text, callback, self)
        self._items.append(it)
        return it

    def addSeparator(self) -> WAction:
        it = WAction("", None, self)
        it.sep = True
        self._items.append(it)
        return it

    def addMenu(self, arg, *args, **kw) -> "WMenu":
        """支持 addMenu("语音") 和 addMenu(已建好的 WMenu) 两种。"""
        if isinstance(arg, WMenu):
            sub = arg
            title = sub._title
        else:
            title = str(arg)
            sub = WMenu(title)
        sub._parent_menu = self
        it = WAction(title, None, self)
        it.sub = sub
        self._items.append(it)
        return sub

    def actions(self) -> list[WAction]:
        return list(self._items)

    def title(self) -> str:
        return self._title

    def sizeHint(self) -> QSize:
        self._measure()
        return QSize(self._panel_w, self._panel_h)

    def ensurePolished(self):
        self._measure()

    # ------------------------------------------------ 树结构
    def root(self) -> "WMenu":
        m = self
        guard = 0
        while m._parent_menu is not None and guard < 16:
            m = m._parent_menu
            guard += 1
        return m

    @property
    def _opens(self) -> dict:
        return self.root()._open_map

    def _chain(self) -> list["WMenu"]:
        """当前可见的整摞面板：根 → 一层层展开下去。"""
        out = [self.root()]
        m = out[0]
        guard = 0
        while guard < 16:
            sub = self.root()._open_map.get(m)
            if sub is None:
                break
            out.append(sub)
            m = sub
            guard += 1
        return out

    # ------------------------------------------------ 度量
    def _measure(self):
        fm = _fm()
        rows = []
        y = PAD_V
        maxw = 0
        for it in self._items:
            if it.sep:
                h = SEP_H
            else:
                h = ROW_H
                w = CHK_W + fm.horizontalAdvance(it._text) + 12
                if it.sub is not None:
                    w += ARROW_W
                maxw = max(maxw, w)
            rows.append((y, h))
            y += h
        self._rows = rows
        self._panel_w = max(MIN_W, min(MAX_W, maxw + PAD_H * 2))
        self._panel_h = y + PAD_V

    def _measure_tree(self, m: "WMenu"):
        m._measure()
        sub = self.root()._open_map.get(m)
        if sub is not None:
            self._measure_tree(sub)

    # ------------------------------------------------ 弹出
    def exec(self, pos: Optional[QPoint] = None):
        """阻塞到菜单关掉，再执行被点中的那一项。

        回调放到关闭之后：菜单还开着就弹设置面板 / 消息框，菜单会滞留
        在最上层，看着像卡死。
        """
        self._pending = None
        self._pending_sub = None
        self._show_at(QPoint(pos) if pos is not None else QCursor.pos())
        self._loop = QEventLoop()
        self._loop.exec()
        self._loop = None
        it, self._pending = self._pending, None
        if it is not None:
            it.trigger()

    def popup(self, pos: Optional[QPoint] = None):
        """非阻塞版，留着给需要自己管生命周期的场景用。"""
        self._show_at(QPoint(pos) if pos is not None else QCursor.pos())

    def _show_at(self, pos: QPoint):
        root = self.root()
        root._anchor = pos
        scr = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        root._avail = scr.availableGeometry()
        root._open_map = {}
        root._hover = (None, None)
        self._measure_tree(root)
        self._relayout()
        root.show()
        root.raise_()
        root.activateWindow()

    # ------------------------------------------------ 布局
    def _relayout(self):
        root = self.root()
        if root._anchor is None:
            return
        avail = root._avail
        self._measure_tree(root)

        w, h = root._panel_w, root._panel_h
        x = root._anchor.x()
        y = root._anchor.y() - ROW_H // 2     # 第一行正对光标，像原生菜单

        # 只按主菜单自身宽度避让，剩下的空间留给子菜单自己挑方向。
        if x + w > avail.right() + 1:
            x = avail.right() + 1 - w
        if x < avail.left():
            x = avail.left()
        if y + h > avail.bottom() + 1:
            y = avail.bottom() + 1 - h
        if y < avail.top():
            y = avail.top()
        root._grect = QRect(int(x), int(y), w, h)

        self._place_children(root)

        union = QRect(root._grect)
        for m in self._chain():
            union = union.united(m._grect)
        win = union.adjusted(-SHADOW, -SHADOW, SHADOW, SHADOW)
        for m in self._chain():
            m._wrect = QRect(m._grect.translated(-win.topLeft()))
        if self.geometry() != win:
            self.setGeometry(win)
        self.update()

    def _place_children(self, parent: "WMenu"):
        sub = self.root()._open_map.get(parent)
        if sub is None:
            return
        avail = self.root()._avail
        idx = min(sub._anchor_row, len(parent._rows) - 1)
        row_y = parent._grect.top() + parent._rows[idx][0] - PAD_V
        sw, sh = sub._panel_w, sub._panel_h

        sy = row_y
        if sy + sh > avail.bottom() + 1:
            sy = avail.bottom() + 1 - sh
        if sy < avail.top():
            sy = avail.top()

        # 先定方向：贴着父面板那一侧（4px 压边，看着连成一体）
        right_x = parent._grect.right() + 1 - 4
        left_x = parent._grect.left() - sw + 4

        # 候选顺序 = 优先沿用上一级的方向，形成一条顺畅的阶梯。
        # 关键不是「屏幕放不放得下」，而是「会不会压在祖辈身上」：
        # 二级折到左边以后，三级要是又往右展，正好压住一级菜单——
        # 鼠标一移过去父级判「离开」，就成了「折叠后选不中」。
        side_prev = None
        grand = parent._parent_menu
        if grand is not None and not grand._grect.isNull():
            side_prev = ("left" if parent._grect.center().x()
                         < grand._grect.center().x() else "right")
        cands = ["left", "right"] if side_prev == "left" else ["right", "left"]

        sx = None
        for side in cands:
            x = right_x if side == "right" else left_x
            if x < avail.left() - 1 or x + sw - 1 > avail.right() + 1:
                continue
            if self._presses_ancestor(parent, QRect(int(x), sy, sw, sh)):
                continue
            sx = x
            break

        if sx is None:
            # 两条路都堵死（超长文字 + 窄屏才会遇到）：退回右侧并夹进屏幕，
            # 宁可贴边也不让菜单跑到屏幕外。
            sx = max(avail.left(), min(right_x, avail.right() + 1 - sw))

        sub._grect = QRect(int(sx), int(sy), sw, sh)
        self._place_children(sub)

    @staticmethod
    def _presses_ancestor(parent: "WMenu", rect: QRect) -> bool:
        """这块地方是不是盖到祖辈面板上了（4px 的贴合边不算）。"""
        m = parent
        guard = 0
        while m is not None and guard < 16:
            inter = rect.intersected(m._grect)
            if not inter.isNull() and inter.width() > 6 and inter.height() > 6:
                return True
            m = m._parent_menu
            guard += 1
        return False

    # ------------------------------------------------ 展开 / 收起
    def _open_sub(self, menu: "WMenu", sub: "WMenu"):
        opens = self._opens
        if opens.get(menu) is sub:
            return
        root = self.root()
        for i, it in enumerate(menu._items):
            if it.sub is sub:
                sub._anchor_row = i
                break
        self._close_below(menu)
        sub.aboutToShow.emit()
        root._open_map[menu] = sub
        self._relayout()

    def _close_below(self, menu: "WMenu", keep: Optional["WMenu"] = None):
        opens = self._opens
        cur = opens.get(menu)
        if cur is None:
            return
        if keep is not None and cur is keep:
            return
        guard = 0
        while cur is not None and guard < 16:
            cur = opens.pop(cur, None)
            guard += 1
        opens.pop(menu, None)

    def _fire_pending_sub(self):
        pend, self._pending_sub = self._pending_sub, None
        if not pend:
            return
        menu, sub = pend
        if self._hover[0] is not menu:
            return
        self._open_sub(menu, sub)

    # ------------------------------------------------ 命中
    def _panel_at(self, p: QPoint) -> Optional["WMenu"]:
        for m in reversed(self._chain()):
            if m._wrect.contains(p):
                return m
        return None

    @staticmethod
    def _row_at(menu: "WMenu", p: QPoint) -> Optional[int]:
        if menu is None:
            return None
        ly = p.y() - menu._wrect.top()
        for i, (oy, h) in enumerate(menu._rows):
            if oy <= ly < oy + h:
                return i
        return None

    def _set_hover(self, menu: Optional["WMenu"], idx: Optional[int]):
        root = self.root()
        if root._hover[0] is menu and root._hover[1] == idx:
            return
        root._hover = (menu, idx)
        item = None
        if menu is not None and idx is not None and 0 <= idx < len(menu._items):
            item = menu._items[idx]

        keep = None
        if item is not None and item.sub is not None and item._enabled:
            keep = item.sub
        if menu is not None:
            self._close_below(menu, keep)      # 移到别的行，下层的子菜单就该收

        root._timer.stop()
        root._pending_sub = None
        if keep is not None:
            root._pending_sub = (menu, keep)
            root._timer.start()

        self._relayout()

    # ------------------------------------------------ 事件
    def mouseMoveEvent(self, e):
        p = e.position().toPoint()
        menu = self._panel_at(p)
        if menu is None:
            self._set_hover(None, None)
            return
        self._set_hover(menu, self._row_at(menu, p))

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        p = e.position().toPoint()
        menu = self._panel_at(p)
        if menu is None:
            self._dismiss()
            return
        idx = self._row_at(menu, p)
        if idx is None:
            self._dismiss()
            return
        item = menu._items[idx]
        if item.sep:
            return
        if not item._enabled:
            return
        if item.sub is not None:
            self._open_sub(menu, item.sub)
            return
        self._pending = item
        self._dismiss()

    def keyPressEvent(self, e):
        key = e.key()
        if key == Qt.Key_Escape:
            self._dismiss()
            return
        if key in (Qt.Key_Down, Qt.Key_Up):
            self._move_hover(1 if key == Qt.Key_Down else -1)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            menu, idx = self._hover
            if menu is not None and idx is not None:
                item = menu._items[idx]
                if item.sub is not None:
                    self._open_sub(menu, item.sub)
                elif item._enabled and not item.sep:
                    self._pending = item
                    self._dismiss()
            return
        if key == Qt.Key_Right:
            menu, idx = self._hover
            if menu is not None and idx is not None:
                item = menu._items[idx]
                if item.sub is not None:
                    self._open_sub(menu, item.sub)
            return
        if key == Qt.Key_Left:
            menu, _ = self._hover
            if menu is not None:
                parent = menu._parent_menu
                if parent is not None:
                    self._close_below(parent)
                    self._relayout()
            return
        super().keyPressEvent(e)

    def _move_hover(self, step: int):
        chain = self._chain()
        # 键盘只在最深一层走，符合直觉
        menu = chain[-1]
        cur = self._hover[1] if self._hover[0] is menu else None
        n = len(menu._items)
        if n == 0:
            return
        idx = 0 if cur is None else cur
        for _ in range(n):
            idx = (idx + step) % n
            if not menu._items[idx].sep:
                break
        self._set_hover(menu, idx)

    def _dismiss(self):
        root = self.root()
        root._timer.stop()
        root._pending_sub = None
        root.hide()
        if root._loop is not None:
            root._loop.quit()

    def hideEvent(self, e):
        root = self.root()
        root._timer.stop()
        if root._loop is not None:
            root._loop.quit()
        super().hideEvent(e)

    def closeEvent(self, e):
        self._dismiss()
        super().closeEvent(e)

    # ------------------------------------------------ 绘制
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        th = theme()
        chain = self._chain()
        for m in chain:
            self._paint_shadow(p, m._wrect)
        for m in chain:
            self._paint_panel(p, m, th)
        p.end()

    @staticmethod
    def _paint_shadow(p: QPainter, r: QRect):
        if r.isNull():
            return
        p.setBrush(Qt.NoBrush)
        base = QRectF(r)
        for i in range(SHADOW, 0, -1):
            a = int(23 * (1 - i / SHADOW) ** 2.2)
            if a <= 0:
                continue
            p.setPen(QPen(QColor(0, 0, 0, a), 1))
            p.drawRoundedRect(base.adjusted(-i, -i, i, i),
                              RADIUS + i, RADIUS + i)

    def _paint_panel(self, p: QPainter, m: "WMenu", th: _Theme):
        r = QRectF(m._wrect)
        if r.isNull():
            return
        path = QPainterPath()
        path.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        p.fillPath(path, th.bg)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(th.border, 1))
        p.drawPath(path)

        fm = _fm()
        p.setFont(_font())
        root = self.root()

        for i, it in enumerate(m._items):
            oy, h = m._rows[i]
            top = r.top() + oy
            if it.sep:
                yy = top + h / 2.0
                p.setPen(QPen(th.sep, 1))
                p.drawLine(QPointF(r.left() + PAD_H + 4, yy),
                           QPointF(r.right() - PAD_H - 4, yy))
                continue

            hovered = (root._hover[0] is m and root._hover[1] == i
                       and it._enabled)
            if not hovered and it.sub is not None:
                # 子菜单开着的时候，父项保持高亮——不然鼠标一进子面板，
                # 父级那行就灭了，看不出自己是从哪儿进来的。
                hovered = root._open_map.get(m) is it.sub
            box = QRectF(r.left() + 4, top + 1, r.width() - 8, h - 2)
            if hovered:
                hp = QPainterPath()
                hp.addRoundedRect(box, 5, 5)
                p.fillPath(hp, th.hover)

            fg = th.text if it._enabled else th.dim
            if it._checkable and it._checked:
                self._paint_check(p, box, th if it._enabled else None, fg)

            tx = box.left() + 5 + CHK_W
            tail = (ARROW_W + 6) if it.sub is not None else 6
            tw = box.right() - tail - tx
            if tw > 8:
                txt = fm.elidedText(it._text, Qt.ElideRight, int(round(tw)))
                p.setPen(fg)
                p.drawText(QRectF(tx, box.top(), tw, box.height()),
                           int(Qt.AlignLeft | Qt.AlignVCenter), txt)

            if it.sub is not None:
                self._paint_arrow(p, box, th.text if hovered else th.arrow)

    @staticmethod
    def _paint_check(p: QPainter, box: QRectF, th: Optional[_Theme], fg: QColor):
        col = (th.accent if th is not None else fg)
        cx = box.left() + 5
        cy = box.center().y()
        path = QPainterPath()
        path.moveTo(cx + 1.5, cy + 0.5)
        path.lineTo(cx + 4.2, cy + 3.4)
        path.lineTo(cx + 9.4, cy - 3.6)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(col, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)

    @staticmethod
    def _paint_arrow(p: QPainter, box: QRectF, col: QColor):
        x = box.right() - 8
        cy = box.center().y()
        path = QPainterPath()
        path.moveTo(x - 2.6, cy - 4.0)
        path.lineTo(x + 1.2, cy)
        path.lineTo(x - 2.6, cy + 4.0)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(col, 1.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)


# ---------------------------------------------------------------- 便捷入口
def popup(menu: WMenu, pos: Optional[QPoint] = None):
    menu.exec(pos if pos is not None else QCursor.pos())
