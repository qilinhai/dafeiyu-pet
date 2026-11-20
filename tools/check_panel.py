# -*- coding: utf-8 -*-
"""设置面板逐个控件审计。

做法：把面板里所有「写盘」函数换成内存记录器（绝不碰真文件），然后
逐个翻动控件 → 走该页签的 save() → 比对写出去的内容有没有变化。
翻完什么都没写出去的，就是「点了没用」的按钮。

用法：python tools/check_panel.py           只看结论
      python tools/check_panel.py -v        把每个控件的影响面都打出来
"""
import os
import sys
import json
import traceback

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = BASE   # 绿色版：脚本的上一层就是程序目录
sys.path.insert(0, BASE)
sys.path.insert(0, APP)
os.chdir(APP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox, QSlider,
    QLineEdit, QPlainTextEdit, QTextEdit, QPushButton, QRadioButton,
    QTableWidget, QListWidget, QGroupBox, QLabel, QWidget,
)

app = QApplication(sys.argv[:1])
import settings_panel as sp

VERBOSE = "-v" in sys.argv
FAIL = []
WROTE = []          # [(path, data)]  被拦下来的写盘请求


# ---------------------------------------------------------------- 拦住写盘
def fake_write(path, data, *a, **k):
    WROTE.append((str(path), data))


sp.write_json = fake_write
sp.read_json = sp.read_json          # 只读，不动

try:
    import anim_config
    anim_config.save = lambda *a, **k: None
except Exception:
    pass


class _FakeDing:
    """照着 settings_panel._SOUND_API 的接口补齐，别让它半路 AttributeError。"""
    TEMPLATES = {"ding": ("叮 · 玻璃双音",), "wood": ("木鱼",)}
    DEFAULT_CFG = {"enabled": True, "template": "ding", "volume": 0.7,
                   "custom_path": ""}

    def load_cfg(self, force=False):
        return dict(self.DEFAULT_CFG)

    def save_cfg(self, cfg):
        WROTE.append(("<ding-cfg>", cfg))

    def build(self, *a, **k):
        return None

    def play(self, *a, **k):
        return None


sp.sound_module = lambda: _FakeDing()
sp.RuntimeTab._style_mod = lambda self: None    # 声音风格表：读不到就跳过


def snapshot(tab):
    """跑一次该页签的保存，把写出去的东西拼成一个可比对的字符串。"""
    WROTE.clear()
    try:
        r = tab.save()
        if isinstance(r, tuple) and len(r) >= 1 and r[0] is False:
            return "<save-failed:%s>" % (r[1] if len(r) > 1 else "")
    except Exception as e:
        return "<exception:%s>" % e
    items = []
    for p, d in WROTE:
        try:
            body = json.dumps(d, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            body = repr(d)
        items.append(os.path.basename(p) + "=" + body)
    return "\n".join(items)


def perturb(w):
    """把控件拨一下。返回 True 表示确实拨动了。"""
    if isinstance(w, QCheckBox):
        w.setChecked(not w.isChecked())
        return True
    if isinstance(w, QRadioButton):
        if not w.isChecked():
            w.setChecked(True)
            return True
        return False
    if isinstance(w, QComboBox):
        if w.count() > 1:
            w.setCurrentIndex((w.currentIndex() + 1) % w.count())
            return True
        return False
    if isinstance(w, QDoubleSpinBox):
        v = w.value()
        nv = v + max(w.singleStep(), 0.1)
        w.setValue(nv if nv <= w.maximum() else max(w.minimum(), v - w.singleStep()))
        return True
    if isinstance(w, QSpinBox):
        v = w.value()
        nv = v + max(1, w.singleStep())
        w.setValue(nv if nv <= w.maximum() else max(w.minimum(), v - 1))
        return True
    if isinstance(w, QSlider):
        v = w.value()
        w.setValue(min(w.maximum(), v + max(1, w.singleStep() * 10)))
        return True
    if isinstance(w, QPlainTextEdit):
        w.setPlainText(w.toPlainText() + "X")
        return True
    if isinstance(w, QTextEdit):
        w.setPlainText(w.toPlainText() + "X")
        return True
    if isinstance(w, QLineEdit):
        w.setText(w.text() + "X")
        return True
    return False


def describe(w):
    cls = type(w).__name__
    txt = ""
    for getter in ("text", "currentText", "title"):
        f = getattr(w, getter, None)
        if callable(f):
            try:
                txt = str(f() or "")
                if txt:
                    break
            except Exception:
                pass
    if not txt and isinstance(w, QWidget) and w.objectName():
        txt = w.objectName()
    return f"{cls}<{txt[:22]}>"


def audit(tab, title, skip=()):
    print("\n" + "=" * 64)
    print(f"【{title}】")
    base = snapshot(tab)
    if base.startswith("<"):
        print("  基础保存就失败了：", base)
        FAIL.append(title + " 基础保存")
        return

    widgets = []
    for cls in (QCheckBox, QRadioButton, QComboBox, QSpinBox, QDoubleSpinBox,
                QSlider, QLineEdit, QPlainTextEdit, QTextEdit):
        widgets += tab.findChildren(cls)
    # 去重（QComboBox 继承自 QWidget，不重复收集）
    seen = set()
    uniq = []
    for w in widgets:
        if id(w) in seen:
            continue
        seen.add(id(w))
        if not w.isEnabled():
            continue
        uniq.append(w)

    print(f"  可改值控件 {len(uniq)} 个")
    dead = []
    for w in uniq:
        old = None
        try:
            old = w.isChecked() if hasattr(w, "isChecked") else None
        except Exception:
            pass
        try:
            if not perturb(w):
                continue
            after = snapshot(tab)
        except Exception as e:
            print(f"    异常 {describe(w)}: {e}")
            traceback.print_exc()
            FAIL.append(f"{title}:{describe(w)}")
            continue
        changed = after != base
        if not changed:
            dead.append(describe(w))
        elif VERBOSE:
            # 打出影响的键名（粗略：比较 json 里的 key 集合）
            try:
                kb = set(json.loads(base.split("=", 1)[1]).keys())
                ka = set(json.loads(after.split("=", 1)[1]).keys())
                keys = sorted(k for k in (ka | kb)
                              if json.dumps(json.loads(base.split("=", 1)[1]).get(k),
                                            sort_keys=True, default=str)
                              != json.dumps(json.loads(after.split("=", 1)[1]).get(k),
                                            sort_keys=True, default=str))
                print(f"    ✓ {describe(w)} → {keys}")
            except Exception:
                print(f"    ✓ {describe(w)} → 有变化")

    if dead:
        print(f"  ⚠ 拨动后什么都没写出去的控件（{len(dead)} 个）：")
        for d in dead:
            print("      -", d)
        FAIL.extend(f"{title}:{d}" for d in dead)
    else:
        print("  ✓ 所有可改值控件都能写进配置")


def audit_buttons(root, title):
    """按钮单独看：有没有接 clicked。纯按钮不该出现在配置里。"""
    print("\n" + "=" * 64)
    print(f"【{title}·按钮】")
    btns = [b for b in root.findChildren(QPushButton) if b.isEnabled()]
    print(f"  按钮 {len(btns)} 个")
    for b in btns:
        n = 0
        try:
            # PySide6 只认旧式字符串签名；传 SignalInstance 会 TypeError
            n = b.receivers("2clicked()")
        except Exception:
            n = -1
        flag = "接了" if n > 0 else ("未知" if n < 0 else "没接!")
        if n == 0:
            FAIL.append(f"{title}:按钮未连接 {b.text()}")
        print(f"    {'✓' if n > 0 else '⚠'} {b.text()[:26]:28s} {flag}")


def main():
    print("设置面板控件审计（写盘全部拦截，不会动真配置）")

    p = sp.PersonaTab()
    audit(p, "人设")

    r = sp.RuntimeTab(None)
    audit(r, "运行")

    n = sp.NotifyTab(None)
    audit(n, "通知")

    po = sp.PortableTab(None)
    audit_buttons(po, "移机")
    audit_buttons(r, "运行")

    print("\n" + "=" * 64)
    if FAIL:
        print(f"共 {len(FAIL)} 处需要看：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("全部控件都有落点")
    return 0


if __name__ == "__main__":
    sys.exit(main())
