# -*- coding: utf-8 -*-
"""动作类按钮冒烟：试听、导出配置包、重新体检。

这些按钮不写配置，所以上一轮的「翻控件看配置」抓不到它们。这里直接点，
把会弹窗的动作换成假对话框（返回临时路径），别真开文件选择器。
"""
import os
import sys
import json
import tempfile
import traceback

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = BASE   # 绿色版：脚本的上一层就是程序目录
sys.path.insert(0, BASE)
sys.path.insert(0, APP)
os.chdir(APP)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

app = QApplication(sys.argv[:1])
import settings_panel as sp

FAIL = []
def ok(cond, name, info=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{info}]" if info else ""))
    if not cond:
        FAIL.append(name)

TMP = tempfile.mkdtemp(prefix="dfy_btn_")

# 拦掉所有弹窗，别把测试挂住
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)
sp.QMessageBox.information = QMessageBox.information
sp.QMessageBox.warning = QMessageBox.warning
sp.QMessageBox.critical = QMessageBox.critical


def main():
    print("\n【运行页按钮】")
    r = sp.RuntimeTab(None)
    try:
        r.preview_sound()
        ok(True, "试听：不抛异常")
    except Exception as e:
        ok(False, "试听：不抛异常", f"{type(e).__name__}: {e}")
        traceback.print_exc()

    # 自定义文件那条路（文件不存在也要给提示而不是崩）
    r.snd_tpl.addItem("自定义", "custom")
    r.snd_tpl.setCurrentIndex(r.snd_tpl.count() - 1)
    r.snd_path.setText(r"C:\不存在的目录\没有这个.wav")
    try:
        r.preview_sound()
        ok(True, "试听（自定义文件不存在）：降级不崩")
    except Exception as e:
        ok(False, "试听（自定义文件不存在）：降级不崩", f"{type(e).__name__}: {e}")

    print("\n【移机页按钮】")
    p = sp.PortableTab(None)
    try:
        p.check()
        ok(True, "重新体检：不抛异常", p.lbl_status.text()[:40] if hasattr(p, "lbl_status") else "")
    except Exception as e:
        ok(False, "重新体检：不抛异常", f"{type(e).__name__}: {e}")
        traceback.print_exc()

    out = os.path.join(TMP, "bundle.json")
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, "JSON (*.json)"))
    sp.QFileDialog.getSaveFileName = QFileDialog.getSaveFileName
    try:
        p.export_bundle()
        made = os.path.exists(out)
        size = os.path.getsize(out) if made else 0
        ok(made, "导出配置包：文件生成了", f"{size} 字节")
        if made:
            with open(out, encoding="utf-8") as f:
                d = json.load(f)
            keys = list(d.keys())
            ok(len(keys) >= 3, "导出内容非空", str(keys))
            ok(d.get("notify") is not None or d.get("config") is not None,
               "导出包含主要配置段", str(keys))
    except Exception as e:
        ok(False, "导出配置包：文件生成了", f"{type(e).__name__}: {e}")
        traceback.print_exc()

    print("\n" + "=" * 60)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for f in FAIL:
            print("  -", f)
        return 1
    print("动作类按钮全部正常")
    return 0


def run():
    code = 1
    try:
        code = main()
    except Exception:
        traceback.print_exc()
    QTimer.singleShot(60, lambda: app.exit(code))


QTimer.singleShot(120, run)
code = app.exec()
sys.exit(code)
