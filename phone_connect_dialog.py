# -*- coding: utf-8 -*-
"""无线调试连接窗口：手输 IP / 端口 / 配对码。

为什么要有这个窗口：Android 11+ 的无线调试，端口每次重开都会变，配对码
只显示一次。自动那套（mDNS 发现 + 缓存地址）在「手机换了 WiFi / 重启过 /
撤销过授权」时全断，这时唯一的路就是让人把手机屏幕上那三样抄进来。

两段式，和手机上的操作一一对应：

    配对   adb pair    IP:配对端口  6位码   ←「使用配对码配对设备」弹窗
    连接   adb connect IP:调试端口           ← 无线调试主界面那行 IP:端口

配对只要一次；之后每次「只连接」就够。

设计约束（踩过的坑）：
- 所有 adb 调用都在 QThread 里，主线程一丝都不许碰（碰了鱼会冻住）。
- 失败原因原样显示 adb 的返回，不翻译成「连接失败」——码错、端口过期、
  不在同网，三种原因的 adb 原文完全不同，翻没了就没法排查。
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(os.path.dirname(HERE), "phone_pair.json")


# ---------------------------------------------------------------- 纯逻辑
def load_hist():
    try:
        with open(HIST, encoding="utf-8") as f:
            d = json.load(f) or {}
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_hist(d):
    try:
        with open(HIST, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


def do_connect(link, ip, conn_port, pair_port="", code="", log=None):
    """按用户填的东西走一遍。返回 (ok, 一句话结论)。

    link 传 phone_link 模块（自检塞假模块）。log 是 callable(str)，
    把每一步的 adb 原文实时吐到界面上。
    """
    say = log or (lambda s: None)
    ip = link.clean_host(ip)
    if not link.valid_host(ip):
        return False, "手机 IP 没填对"
    conn_port = str(conn_port or "").strip()

    paired = False
    if str(pair_port or "").strip() and str(code or "").strip():
        say("① 配对 %s:%s ……" % (ip, str(pair_port).strip()))
        ok, msg = link.pair(ip, pair_port, code)
        say("   " + msg)
        if not ok:
            return False, ("配对没通过。手机上的「使用配对码配对设备」弹窗还开着吗？"
                           "端口和码要现抄，关掉就失效")
        paired = True

    # 配对成功后 adb 通常已经通过 mDNS 广播了连接端口，能发现就不用手抄
    addr = ""
    if paired:
        addrs = link.discovered(budget=4) or []
        if addrs:
            addr = addrs[0]
            say("② mDNS 发现 " + addr)

    if not addr:
        if not link.valid_port(conn_port):
            if paired:
                return True, ("配对成功。连接端口没填——回到无线调试主界面，"
                              "抄那行「IP 地址和端口」的端口，再点「只连接」")
            return False, "连接端口没填（无线调试主界面那行 IP:端口 的端口）"
        addr = "%s:%s" % (ip, conn_port)

    say("③ 连接 %s ……" % addr)
    ok, msg = link.connect_to(addr)
    say("   " + msg)
    if ok:
        return True, "连上了：%s" % addr
    return False, "连接没成功。手机锁屏了？不在同一个 WiFi？解锁再点一次"


def do_disconnect(link, log=None):
    say = log or (lambda s: None)
    out, err, rc = link._run2(["disconnect"], timeout=6)
    say((out + " " + err).strip() or "adb 没有返回内容")
    if link.online():
        return False, "没断干净，还有设备在线"
    return True, "已断开"


# ---------------------------------------------------------------- 界面
class _Job(QThread):
    """后台跑一次 adb 流程。QThread.finished 是自带的，这里另起一个带结果。"""

    result = Signal(bool, str)
    line = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            ok, msg = self._fn(self.line.emit)
        except Exception as e:                      # 兜底：线程里崩了也要回话
            ok, msg = False, "出错了：%s" % e
        self.result.emit(ok, msg)


def _link():
    try:
        import phone_link
        return phone_link
    except Exception:
        return None


class PhoneConnectDialog(QDialog):
    """输入 IP / 端口 / 配对码 → 配对 → 连接。自带本机 IP 对照。"""

    def __init__(self, parent=None, link=None, on_success=None):
        super().__init__(parent)
        self.link = link or _link()
        self.on_success = on_success
        self.job = None
        self.setWindowTitle("连接手机（无线调试）")
        self.setMinimumWidth(560)
        self.setModal(False)

        root = QVBoxLayout(self)

        hint = QLabel(
            "手机：设置 → 开发者选项 → 无线调试\n"
            "· 「使用配对码配对设备」弹窗里：IP、配对端口、6 位配对码\n"
            "· 无线调试主界面上那行「IP 地址和端口」：用来连接（和配对端口不是同一个）"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#555;")
        root.addWidget(hint)

        hist = load_hist()
        self.ip_edit = QLineEdit(hist.get("ip", ""))
        self.ip_edit.setPlaceholderText("192.168.1.7")
        self.conn_edit = QLineEdit(hist.get("conn_port", ""))
        self.conn_edit.setPlaceholderText("无线调试主界面那个，比如 46775")
        self.pair_edit = QLineEdit(hist.get("pair_port", ""))
        self.pair_edit.setPlaceholderText("配对弹窗里冒号后面那个，比如 37123")
        self.code_edit = QLineEdit("")
        self.code_edit.setPlaceholderText("配对弹窗里的 6 位数字")
        self.code_edit.setMaxLength(12)

        form = QFormLayout()
        form.addRow("手机 IP", self.ip_edit)
        form.addRow("连接端口", self.conn_edit)
        form.addRow("配对端口", self.pair_edit)
        form.addRow("配 对 码", self.code_edit)
        root.addLayout(form)

        ips = []
        if self.link is not None:
            try:
                ips = self.link.local_ips()
            except Exception:
                ips = []
        self.local_lbl = QLabel("本机 IP：" + ("、".join(ips) if ips else "读不到") +
                                "　（手机要和它在同一个网段）")
        self.local_lbl.setStyleSheet("color:#666;")
        self.local_lbl.setWordWrap(True)
        root.addWidget(self.local_lbl)

        row = QHBoxLayout()
        self.b_pair = QPushButton("配对并连接")
        self.b_conn = QPushButton("只连接")
        self.b_disc = QPushButton("断开")
        self.b_close = QPushButton("关闭")
        row.addWidget(self.b_pair)
        row.addWidget(self.b_conn)
        row.addWidget(self.b_disc)
        row.addStretch(1)
        row.addWidget(self.b_close)
        root.addLayout(row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(120)
        root.addWidget(self.log, 1)

        self.b_pair.clicked.connect(lambda: self._run("pair"))
        self.b_conn.clicked.connect(lambda: self._run("connect"))
        self.b_disc.clicked.connect(lambda: self._run("disconnect"))
        self.b_close.clicked.connect(self.close)

        # 配对码只显示一次，别在界面上留副本；关窗就清掉
        self.finished.connect(lambda _=0: self.code_edit.clear())

    # ---------------------------------------------------------- 内部
    def _append(self, s):
        self.log.appendPlainText(s)

    def _busy(self, on):
        for b in (self.b_pair, self.b_conn, self.b_disc):
            b.setEnabled(not on)
        if on:
            self.log.clear()

    def _read(self):
        return (self.ip_edit.text(), self.conn_edit.text(),
                self.pair_edit.text(), self.code_edit.text())

    def _run(self, mode):
        if self.job is not None and self.job.isRunning():
            return
        if self.link is None:
            self.log.setPlainText("连接模块没找到（phone_link.py）")
            return
        ip, conn, pairp, code = self._read()
        save_hist({"ip": ip, "conn_port": conn, "pair_port": pairp})
        self._busy(True)
        link = self.link
        if mode == "pair":
            fn = lambda say: do_connect(link, ip, conn, pairp, code, say)
        elif mode == "connect":
            fn = lambda say: do_connect(link, ip, conn, "", "", say)
        else:
            fn = lambda say: do_disconnect(link, say)
        self.job = _Job(fn, self)
        self.job.line.connect(self._append)
        self.job.result.connect(self._done)
        self.job.start()

    def _done(self, ok, msg):
        self._busy(False)
        self._append(("PASS  " if ok else "FAIL  ") + msg)
        self.code_edit.clear()          # 用过一次就失效了，别留着
        # 连上了要立刻通知外面刷状态：桌宠的快照有 90 秒 TTL，
        # 不刷的话右键菜单还会显示「没发现手机」，用户以为没连上。
        if ok and callable(self.on_success):
            try:
                self.on_success()
            except Exception:
                pass

    def closeEvent(self, e):
        if self.job is not None and self.job.isRunning():
            self.job.wait(3000)
        super().closeEvent(e)
