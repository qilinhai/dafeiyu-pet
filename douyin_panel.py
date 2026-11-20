"""大肥鱼右键菜单的「抖音收藏」面板：今天优先的卡片列表，双击打开视频。"""
import os
import sys
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout,
)

# 归档模块在 C:\DaFeiYu 根目录；正常由 pet.py 加进 sys.path，这里再兜一次
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import douyin_archive as da
    _DA_ERR = None
except Exception as exc:  # 模块坏了只灰这一块，不拖垮整个面板
    da = None
    _DA_ERR = exc


class DouyinDialog(QDialog):
    def __init__(self, parent=None, on_say=None):
        super().__init__(parent)
        self.on_say = on_say
        self.items = []
        self.setWindowTitle("大肥鱼 · 抖音收藏")
        self.resize(580, 600)

        layout = QVBoxLayout(self)

        self.head = QLabel()
        self.head.setWordWrap(True)
        layout.addWidget(self.head)

        self.listing = QListWidget()
        self.listing.itemDoubleClicked.connect(self.open_current)
        layout.addWidget(self.listing)

        actions = QHBoxLayout()
        layout.addLayout(actions)
        b_open = QPushButton("打开视频")
        b_open.clicked.connect(self.open_current)
        actions.addWidget(b_open)
        b_say = QPushButton("让鱼说说今天")
        b_say.clicked.connect(self.say_today)
        actions.addWidget(b_say)
        b_refresh = QPushButton("刷新")
        b_refresh.clicked.connect(self.refresh)
        actions.addWidget(b_refresh)

        self.refresh()

    def refresh(self):
        self.listing.clear()
        if da is None:
            self.head.setText(f"归档模块没加载：{type(_DA_ERR).__name__}: {_DA_ERR}")
            return
        try:
            self.items = da.load_records()
            rep = da.report("today", self.items)
        except Exception as exc:
            self.head.setText(f"读不到归档数据：{type(exc).__name__}: {exc}")
            return

        cats = "、".join(f"{c}{n}" for c, n in rep["categories"][:4]) or "无"
        self.head.setText(
            f"今天 {rep['count']} 条 · 累计 {rep['total']} 条\n分类：{cats}"
        )

        today_ids = {str(it.get("aweme_id")) for it in rep["items"]}
        ordered = list(rep["items"]) + [
            it for it in self.items if str(it.get("aweme_id")) not in today_ids
        ]
        for it in ordered[:300]:
            label = f"{str(it.get('at') or '')[:10]}  {da.item_line(it, 30)}"
            item = QListWidgetItem(label, self.listing)
            item.setData(Qt.ItemDataRole.UserRole, str(it.get("url") or ""))
            if str(it.get("aweme_id")) in today_ids:
                f = item.font()
                f.setBold(True)
                item.setFont(f)

    def open_current(self, *_):
        cur = self.listing.currentItem()
        if cur is None:
            return
        url = cur.data(Qt.ItemDataRole.UserRole) or ""
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception as exc:
            QMessageBox.warning(self, "打不开链接", str(exc))

    def say_today(self):
        if da is None or not self.on_say:
            return
        try:
            self.on_say(da.summary_text("today"))
        except Exception as exc:
            QMessageBox.warning(self, "汇报失败", str(exc))
