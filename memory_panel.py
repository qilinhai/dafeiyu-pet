"""大肥鱼右键菜单的记忆与成长手册。"""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

import pet_growth as growth
from pet_memory import read_store, describe_card


class MemoryDialog(QDialog):
    def __init__(self, parent, path):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle("大肥鱼 · 记忆与成长")
        self.resize(510, 540)
        layout = QVBoxLayout(self)
        self.report = QLabel()
        self.report.setTextFormat(Qt.TextFormat.PlainText)
        self.report.setWordWrap(True)
        layout.addWidget(self.report)
        self.quiet = QCheckBox("WorkBuddy 收工时只冒泡")
        self.quiet.clicked.connect(self.set_quiet)
        layout.addWidget(self.quiet)
        self.listing = QListWidget()
        layout.addWidget(self.listing)
        actions = QHBoxLayout()
        layout.addLayout(actions)
        for label, callback in (("记住一件事", self.add), ("改一下", self.change), ("忘掉", self.forget)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        self.refresh()

    def refresh(self):
        d = read_store(self.path)
        self.report.setText(growth.growth_report(d))
        self.quiet.setChecked(any(p["text"] == growth.QUIET_STOP for p in d["preferences"]))
        self.listing.clear()
        for group, label in (("pins", "手记"), ("preferences", "偏好")):
            for row in d.get(group, []):
                text = row[growth.FIELDS[group]]
                item = QListWidgetItem(f"{label}　{text}", self.listing)
                item.setData(Qt.ItemDataRole.UserRole, (group, text))
        # 话题分两层：干活的和手机推来的。混着列看不清它到底记得什么。
        for row in d.get("items", []):
            tag = "消息" if row.get("layer") == "event" else "话题"
            text = str(row.get("topic") or "")
            if row.get("preview"):
                text += f"（{row['preview']}）"
            item = QListWidgetItem(f"{tag}　{text}", self.listing)
            item.setData(Qt.ItemDataRole.UserRole, ("items", row.get("topic")))
            # 每个话题下面挂上最近两次具体干了啥。只有话题名的话，用户只能
            # 看到「奇门命盘 23 次」，看不出这 23 次到底改了些什么东西。
            for card in (row.get("events") or [])[:2]:
                line = describe_card(card, brief=True)
                if not line:
                    continue
                stamp = time.strftime("%m-%d ", time.localtime(int(card.get("ts") or 0)))
                sub = QListWidgetItem(f"      ↳ {stamp}{line}", self.listing)
                sub.setData(Qt.ItemDataRole.UserRole, None)   # 子行不可操作
                sub.setFlags(Qt.ItemFlag.ItemIsEnabled)
        # 推测单独标出来：这些是「它觉得」，不是「它记得」，删了就不再复述。
        for row in d.get("impressions", []):
            kind = "对你" if row.get("kind") == "about_you" else "对事"
            text = str(row.get("text") or "")
            item = QListWidgetItem(f"推测·{kind}　{text}", self.listing)
            item.setData(Qt.ItemDataRole.UserRole, ("impressions", text))

    def apply(self, operation, *args):
        try:
            operation(self.path, *args)
            self.refresh()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "记忆未更新", str(exc))
            try:
                self.refresh()
            except (ValueError, OSError):
                self.close()

    def set_quiet(self, checked):
        self.apply(growth.set_quiet, checked)

    def add(self):
        text, ok = QInputDialog.getText(self, "记住一件事", "想让鱼下回记得什么？（最多 80 字）")
        if ok:
            self.apply(growth.edit_entry, "pins", None, text)

    def change(self):
        item = self.listing.currentItem()
        if item:
            data = item.data(Qt.ItemDataRole.UserRole)
            if not data:
                return           # 事件卡子行不能改，改它等于篡改事实
            group, old = data
            text, ok = QInputDialog.getText(self, "改一下", "新的内容", text=old)
            if ok:
                self.apply(growth.edit_entry, group, old, text)

    def forget(self):
        item = self.listing.currentItem()
        if item:
            data = item.data(Qt.ItemDataRole.UserRole)
            if not data:
                return           # 事件卡子行：不是记忆本身，没得删
            group, old = data
            if QMessageBox.question(self, "忘掉记忆", f"忘掉「{old}」？") == QMessageBox.StandardButton.Yes:
                if group == "impressions":
                    # 推测不能"删"，只能标错——标错之后它不再复述这一条。
                    try:
                        import pet_memory
                        pet_memory.mark_impression(old, wrong=True)
                        self.refresh()
                    except Exception as exc:      # noqa: BLE001
                        QMessageBox.warning(self, "记忆未更新", str(exc))
                    return
                self.apply(growth.edit_entry, group, old, None)
