# -*- coding: utf-8 -*-
"""大肥鱼的设置面板：四页签，改完即时生效，不用改源码。

人设页  persona.json        性格与越界词、默认值禁用词、表达分层、字数上限
运行页  config.json         语音双 key、音色语速、动作节奏、防重置开关、城市
通知页  notify_config.json  手机通知念什么、挡什么、多久抓一次（监听进程热加载）
移机页  ——                  换电脑时的依赖体检、端口检查、配置导出导入

两条设计底线：

1. **保存后必须热加载**。面板点了保存却没反应，比没有面板更糟——
   所以写完文件要重算 persona 的守卫表、清 pet_acts 的过滤缓存。
2. **运行页不能直接写盘**。桌宠内存里有一份 cfg，它一 save_config()
   就会把磁盘盖回去（双 key 那次就是这么丢的）。所以有 pet 引用时
   走 pet.apply_settings()，让内存和磁盘一起改。
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSlider,
    QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

HERE = os.path.dirname(os.path.abspath(__file__))
# 安装根目录：绿色版（代码和配置同一层）以本目录为根；旧双层布局退回上一层
ROOT = HERE if os.path.exists(os.path.join(HERE, "persona.json")) else os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PERSONA_PATH = os.path.join(ROOT, "persona.json")
CONFIG_PATH = os.path.join(HERE, "config.json")
ANIM_PATH = os.path.join(HERE, "anim_config.json")
# WorkBuddy hooks 的配置。定时任务「只叮不念」的开关在这里，不在 config.json
HOOK_CFG_PATH = os.path.join(ROOT, "hooks", "hook_config.json")
NOTIFY_CFG_PATH = os.path.join(ROOT, "notify_config.json")

# 定时任务中途卡住时念的那句默认台词。面板里三处要用（占位提示、读不到时的
# 兜底、写空时的回填），只留这一份，改一句不用改三个地方。
# hooks/wb_hook.py 的 DEFAULT_CFG 里还有一份同名默认——那是「配置文件整个丢了」
# 时 hook 自己用的兜底，跨进程没法共享常量，必须留着。
AUTO_STUCK_LINE = "定时任务卡住了，等你点一下"
AUTO_STUCK_GAP = 90          # 秒，两次提醒的最小间隔默认值

UI_FONT = "Microsoft YaHei UI"

QSS = """
QDialog { background: #f6f6f8; }
QLabel { color: #333; }
QGroupBox {
    font-weight: 600; border: 1px solid #dcdce0; border-radius: 6px;
    margin-top: 12px; padding: 10px 8px 8px 8px; background: #fff;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #444; }
QTableWidget { background: #fff; border: 1px solid #e0e0e4; gridline-color: #eee; }
QHeaderView::section {
    background: #f0f0f3; color: #555; border: none;
    border-bottom: 1px solid #e0e0e4; padding: 5px;
}
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
    background: #fff; border: 1px solid #d0d0d5; border-radius: 4px; padding: 3px 5px;
}
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #7a9bd6; }
QPushButton {
    background: #fff; border: 1px solid #cfcfd6; border-radius: 4px; padding: 5px 14px;
}
QPushButton:hover { background: #eef2fb; border-color: #a9bfe6; }
QPushButton:pressed { background: #e2e9f7; }
QPushButton#primary {
    background: #4c7dd8; color: #fff; border: 1px solid #4070c8; font-weight: 600;
}
QPushButton#primary:hover { background: #5a8ae2; }
QTabWidget::pane { border: 1px solid #dcdce0; background: #fff; border-radius: 6px; }
QTabBar::tab {
    background: #eaeaee; border: 1px solid #dcdce0; border-bottom: none;
    padding: 7px 18px; margin-right: 2px; border-top-left-radius: 5px;
    border-top-right-radius: 5px; color: #555;
}
QTabBar::tab:selected { background: #fff; color: #2b2b2b; font-weight: 600; }
"""


# ---------------------------------------------------------------- 小工具
def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else (default if default is not None else {})
    except Exception:
        return default if default is not None else {}


def write_json(path, data):
    """原子写：先写临时文件再替换，写一半断电也不会留下半个 json。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def split_words(text):
    """逗号、顿号、换行、空格都能分——用户不会记得你规定的是哪个。"""
    out, seen = [], set()
    for part in str(text or "").replace("\n", ",").replace("，", ",") \
                              .replace("、", ",").replace(" ", ",").split(","):
        w = part.strip()
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def hint(text):
    """灰色小字说明。"""
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setStyleSheet("color:#888; font-size:11px;")
    return lb


# ================================================================ 人设页
class PersonaTab(QWidget):
    BAN_KEYS = [
        ("address", "称呼", "默认用「你」，这些话不许说："),
        ("relation", "关系", "是同伴不是恋人，这些话不许说："),
        ("quirks", "口癖", "不许卖萌成习惯，这些词禁掉："),
        ("honesty", "诚实", "不许假装查过/算过，这些话禁掉："),
    ]

    def __init__(self):
        super().__init__()
        self.raw = read_json(PERSONA_PATH, {})
        self.ban_edits = {}
        self.layer_widgets = {}
        self._build()
        self._fill()

    # -------------------------------------------------- 界面
    def _build(self):
        v = QVBoxLayout(self)
        v.setSpacing(10)

        # --- 性格表
        g = QGroupBox("性格与越界约束")
        gv = QVBoxLayout(g)
        gv.addWidget(hint(
            "性格 = 它是什么样；越界 = 不许演成什么样；拦截词 = 说出来这句就作废。"
            "三条缺一条，性格就会演过头——只写「贪吃」它会句句吃饭。"))
        self.tbl = QTableWidget(0, 3)
        self.tbl.setHorizontalHeaderLabels(["性格", "越界约束（不许…）", "拦截词（逗号分隔）"])
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tbl.verticalHeader().setDefaultSectionSize(30)
        gv.addWidget(self.tbl, 1)

        hb = QHBoxLayout()
        b_add = QPushButton("加一条")
        b_add.clicked.connect(lambda: self._add_row())
        b_del = QPushButton("删掉选中")
        b_del.clicked.connect(self._del_row)
        hb.addWidget(b_add)
        hb.addWidget(b_del)
        hb.addStretch(1)
        gv.addLayout(hb)
        v.addWidget(g, 3)

        # --- 默认值
        g2 = QGroupBox("默认值（否定式钉死）")
        f2 = QFormLayout(g2)
        f2.addRow(hint("人设可以夸张，但不能改写现实。这些词一律进拦截表。"))
        for key, label, tip in self.BAN_KEYS:
            ed = QLineEdit()
            ed.setPlaceholderText("逗号分隔，留空表示不禁")
            self.ban_edits[key] = ed
            f2.addRow(f"{label}（{tip}）", ed)
        v.addWidget(g2, 2)

        # --- 分层
        g3 = QGroupBox("表达分层")
        grid = QGridLayout(g3)
        grid.addWidget(hint(
            "角色腔只管招呼、状态、情境台词。系统播报（积分、网络、报错）必须说人话，"
            "因为里面有要看清的下一步。"), 0, 0, 1, 4)

        self.max_chars = QSpinBox()
        self.max_chars.setRange(10, 120)
        self.max_chars.setSuffix(" 字")
        grid.addWidget(QLabel("气泡台词字数上限"), 1, 0)
        grid.addWidget(self.max_chars, 1, 1)

        for row, (layer, label) in enumerate(
                (("system", "系统播报"), ("character", "角色台词")), start=2):
            spin = QDoubleSpinBox()
            spin.setRange(0.5, 30.0)
            spin.setSingleStep(0.5)
            spin.setSuffix(" 秒")
            cb = QCheckBox("配戏剧反应")
            self.layer_widgets[layer] = (spin, cb)
            grid.addWidget(QLabel(f"{label}最短停留"), row, 0)
            grid.addWidget(spin, row, 1)
            grid.addWidget(cb, row, 2)
        grid.setColumnStretch(3, 1)
        v.addWidget(g3, 0)

    def _fill(self):
        att = self.raw.get("attitude") or []
        self.tbl.setRowCount(len(att))
        for i, a in enumerate(att):
            if isinstance(a, str):
                a = {"trait": a, "guard": "", "bound": []}
            self.tbl.setItem(i, 0, QTableWidgetItem(a.get("trait", "")))
            self.tbl.setItem(i, 1, QTableWidgetItem(a.get("guard", "")))
            self.tbl.setItem(i, 2, QTableWidgetItem("，".join(a.get("bound") or [])))

        d = self.raw.get("defaults") or {}
        for key, ed in self.ban_edits.items():
            seg = d.get(key) or {}
            ed.setText("，".join(seg.get("ban") or []))

        sp = self.raw.get("speech") or {}
        self.max_chars.setValue(int(sp.get("max_chars") or 34))
        layers = self.raw.get("layers") or {}
        for layer, (spin, cb) in self.layer_widgets.items():
            seg = layers.get(layer) or {}
            spin.setValue(float(seg.get("min_seconds") or
                                (5.0 if layer == "system" else 2.8)))
            cb.setChecked(bool(seg.get("react", layer != "system")))

    def _add_row(self):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        for c in range(3):
            self.tbl.setItem(r, c, QTableWidgetItem(""))
        self.tbl.editItem(self.tbl.item(r, 0))

    def _del_row(self):
        r = self.tbl.currentRow()
        if r >= 0:
            self.tbl.removeRow(r)

    # -------------------------------------------------- 存取
    def collect(self):
        """收回成 persona 字典。原有其它字段和 _note_* 注释原样保留。"""
        d = dict(self.raw)
        att = []
        for i in range(self.tbl.rowCount()):
            def cell(c):
                it = self.tbl.item(i, c)
                return (it.text() if it else "").strip()
            trait = cell(0)
            if not trait:
                continue
            att.append({"trait": trait, "guard": cell(1),
                        "bound": split_words(cell(2))})
        d["attitude"] = att

        defaults = dict(d.get("defaults") or {})
        for key, ed in self.ban_edits.items():
            seg = dict(defaults.get(key) or {})
            seg["ban"] = split_words(ed.text())
            defaults[key] = seg
        d["defaults"] = defaults

        layers = dict(d.get("layers") or {})
        for layer, (spin, cb) in self.layer_widgets.items():
            seg = dict(layers.get(layer) or {})
            seg["min_seconds"] = round(spin.value(), 2)
            seg["react"] = cb.isChecked()
            layers[layer] = seg
        d["layers"] = layers

        sp = dict(d.get("speech") or {})
        sp["max_chars"] = self.max_chars.value()
        d["speech"] = sp
        return d

    def save(self):
        """写文件 + 热加载。返回 (成功, 说明)。"""
        d = self.collect()
        if not d.get("attitude"):
            return False, "至少要留一条性格，全删了它就没有人设了"
        try:
            write_json(PERSONA_PATH, d)
        except Exception as e:
            return False, f"人设没写进去：{e}"

        try:
            import pet_memory
            ok, msg = pet_memory.reload_persona()
        except Exception as e:
            return True, f"已保存，但热加载失败（重启后生效）：{e}"

        # 台词池的过滤缓存也得清，不清新词不生效
        try:
            import pet_acts
            pet_acts.clear_guard_cache()
        except Exception as e:
            print("[panel] 清台词缓存失败:", e)
        return ok, msg


# ================================================================ 运行页
_SOUND_API = ("TEMPLATES", "DEFAULT_CFG", "build", "play", "load_cfg", "save_cfg")


def fresh_module(name, need=(), fallback_on_reload=None):
    """拿模块，顺手治「内存里是旧版」。

    真实的坑（2026-11-06 事故）：桌宠开着几个小时，`ding` / `phone_notify`
    这类模块早就进了 sys.modules。之后我改了它们的接口（加 TEMPLATES、
    加 DEFAULT_RULES），新面板 import 回来还是内存那份旧的，一取新属性就
    AttributeError，**整个面板打不开**。

    所以：缺接口就地 reload 一次（磁盘上是新版就好了）；磁盘上也是旧文件才会
    仍然缺，那就返回 None —— 面板的某一组降级，总比整页打不开强。
    """
    import importlib
    try:
        mod = importlib.import_module(name)
    except Exception as e:
        print(f"[panel] {name} 没加载: {e}")
        return None
    miss = [n for n in need if not hasattr(mod, n)]
    if not miss:
        return mod
    try:
        mod = importlib.reload(mod)
        print(f"[panel] {name} 是旧版（缺 {miss}），已重载")
    except Exception as e:
        print(f"[panel] 重载 {name} 失败: {e}")
        return None
    miss = [n for n in need if not hasattr(mod, n)]
    if miss:
        print(f"[panel] 重载后 {name} 仍缺 {miss}，这组先禁用")
        return None
    return mod


def sound_module():
    """提示音模块。拿不到返回 None——不能因为少个文件让整页打不开。"""
    return fresh_module("ding", _SOUND_API)


def sound_defaults():
    m = sound_module()
    if m is not None:
        return dict(m.DEFAULT_CFG)
    return {"enabled": True, "template": "ding", "volume": 0.7, "custom_path": ""}


def sound_templates():
    """[(key, 显示名)]。模块缺了就只给一个默认项。"""
    m = sound_module()
    if m is None:
        return [("ding", "叮 · 玻璃双音")]
    return [(k, v[0]) for k, v in m.TEMPLATES.items()]


class RuntimeTab(QWidget):
    def __init__(self, pet=None):
        super().__init__()
        self.pet = pet
        self.cfg = read_json(CONFIG_PATH, {})
        self.anim = read_json(ANIM_PATH, {})
        self._build()
        self._fill()

    def _build(self):
        v = QVBoxLayout(self)
        v.setSpacing(10)

        # --- 语音
        g = QGroupBox("语音（BreezeBlue）")
        f = QFormLayout(g)
        self.key = QPlainTextEdit()
        self.key.setPlaceholderText("brz_xxx\nbrz_yyy")
        self.key.setFixedHeight(64)
        f.addRow("API Key", self.key)
        f.addRow("", hint(
            "多个 Key 用逗号或换行分开，会轮流用。哪个返回额度不足就自动跳下一个。"))

        self.voice = QLineEdit()
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.5, 2.0)
        self.speed.setSingleStep(0.05)
        self.volume = QDoubleSpinBox()
        self.volume.setRange(0.0, 1.0)
        self.volume.setSingleStep(0.05)
        f.addRow("音色 ID", self.voice)
        f.addRow("语速", self.speed)
        f.addRow("音量", self.volume)

        self.tts_on = QCheckBox("启用语音")
        self.tts_ambient = QCheckBox("自动碎嘴也念出来（费额度）")
        f.addRow(self.tts_on)
        f.addRow(self.tts_ambient)
        v.addWidget(g)

        # --- 动作
        g2 = QGroupBox("动作")
        f2 = QFormLayout(g2)
        self.fg0, self.fg1 = QSpinBox(), QSpinBox()
        self.ag0, self.ag1 = QSpinBox(), QSpinBox()
        for s in (self.fg0, self.fg1, self.ag0, self.ag1):
            s.setRange(2, 3600)
            s.setSuffix(" 秒")
        row_f = QHBoxLayout()
        row_f.addWidget(self.fg0)
        row_f.addWidget(QLabel("到"))
        row_f.addWidget(self.fg1)
        row_f.addStretch(1)
        f2.addRow("小动作间隔", self._wrap(row_f))

        row_a = QHBoxLayout()
        row_a.addWidget(self.ag0)
        row_a.addWidget(QLabel("到"))
        row_a.addWidget(self.ag1)
        row_a.addStretch(1)
        f2.addRow("整段表演间隔", self._wrap(row_a))

        self.no_restart = QCheckBox("同一动作正在播时，不从头重播（防连点抽动）")
        f2.addRow(self.no_restart)
        v.addWidget(g2)

        # --- 提示音（不出人声时用，比如叫悠悠）
        gs = QGroupBox("提示音（该提醒但不出人声时，比如叫悠悠）")
        fs = QFormLayout(gs)
        self.snd_on = QCheckBox("启用提示音")
        fs.addRow(self.snd_on)

        row_t = QHBoxLayout()
        self.snd_tpl = QComboBox()
        for key, label in sound_templates():
            self.snd_tpl.addItem(label, key)
        self.snd_tpl.setMinimumWidth(180)
        self.snd_try = QPushButton("试听")
        self.snd_try.setFixedWidth(60)
        self.snd_try.clicked.connect(self.preview_sound)
        row_t.addWidget(self.snd_tpl)
        row_t.addWidget(self.snd_try)
        row_t.addStretch(1)
        fs.addRow("声音模板", self._wrap(row_t))

        row_v = QHBoxLayout()
        self.snd_vol = QSlider(Qt.Orientation.Horizontal)
        self.snd_vol.setRange(5, 95)
        self.snd_vol.setFixedWidth(200)
        self.snd_vol_lbl = QLabel("70%")
        self.snd_vol_lbl.setFixedWidth(40)
        self.snd_vol.valueChanged.connect(
            lambda x: self.snd_vol_lbl.setText(f"{x}%"))
        row_v.addWidget(self.snd_vol)
        row_v.addWidget(self.snd_vol_lbl)
        row_v.addStretch(1)
        fs.addRow("音量", self._wrap(row_v))

        row_c = QHBoxLayout()
        self.snd_path = QLineEdit()
        self.snd_path.setPlaceholderText("模板选「自定义 wav 文件」时才用")
        self.snd_pick = QPushButton("浏览")
        self.snd_pick.setFixedWidth(56)
        self.snd_pick.clicked.connect(self.pick_sound)
        row_c.addWidget(self.snd_path, 1)
        row_c.addWidget(self.snd_pick)
        fs.addRow("自定义文件", self._wrap(row_c))
        fs.addRow("", hint("只认 wav。改完点保存即时生效，不用重启桌宠。"))
        v.addWidget(gs)

        # --- 定时任务：只叮不念，省 TTS 积分
        gh = QGroupBox("定时任务（WorkBuddy 联动）")
        fh = QFormLayout(gh)
        self.auto_mode = QComboBox()
        self.auto_mode.addItem("只叮不念（省积分）", "ding")
        self.auto_mode.addItem("照常语音播报", "speak")
        self.auto_mode.addItem("完全静音", "mute")
        fh.addRow("跑定时任务时", self.auto_mode)

        row_d = QHBoxLayout()
        self.ding_open = QSpinBox()
        self.ding_open.setRange(0, 5)
        self.ding_open.setFixedWidth(72)
        self.ding_close = QSpinBox()
        self.ding_close.setRange(0, 6)
        self.ding_close.setFixedWidth(72)
        row_d.addWidget(QLabel("开始"))
        row_d.addWidget(self.ding_open)
        row_d.addWidget(QLabel("声 ／ 结束"))
        row_d.addWidget(self.ding_close)
        row_d.addWidget(QLabel("声"))
        row_d.addStretch(1)
        fh.addRow("叮几声", self._wrap(row_d))

        # 中途卡住（WorkBuddy 的 Notification＝在等你授权/回话）必须念一句，
        # 光叮一声跟「跑完了」分不出来。台词可改，{detail} 会换成通知里的中文细节。
        row_s = QHBoxLayout()
        self.auto_stuck = QCheckBox("念一句提醒")
        self.auto_stuck.toggled.connect(self._sync_auto_stuck)
        row_s.addWidget(self.auto_stuck)
        row_s.addStretch(1)
        fh.addRow("中途卡住要授权", self._wrap(row_s))

        self.auto_line = QLineEdit()
        self.auto_line.setPlaceholderText(AUTO_STUCK_LINE)
        fh.addRow("卡住时念", self.auto_line)

        # 间隔也上控件：写死在提示文字里，用户改了 JSON 界面就开始撒谎
        row_g = QHBoxLayout()
        self.auto_gap = QSpinBox()
        self.auto_gap.setRange(15, 600)
        self.auto_gap.setSingleStep(15)
        self.auto_gap.setFixedWidth(90)
        self.auto_gap.setSuffix(" 秒")
        row_g.addWidget(self.auto_gap)
        row_g.addWidget(QLabel("内只提醒一次"))
        row_g.addStretch(1)
        fh.addRow("重复提醒间隔", self._wrap(row_g))

        fh.addRow("", hint("定时任务没人守着，念了白烧积分。0 声＝不响。"
                           "中途卡住那几句不算白念，所以要出声。"
                           "台词里写 {detail} 会换成通知的细节；"
                           "台词留空＝只叮不念。"))
        v.addWidget(gh)

        # --- 语料扩充：玩梗靠它自己长
        gv = QGroupBox("语料扩充（玩梗）")
        fv = QFormLayout(gv)
        self.lex_key = QLineEdit()
        self.lex_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.lex_key.setPlaceholderText("可留空 —— 不填就用 WorkBuddy 的模型补")
        fv.addRow("DeepSeek key", self.lex_key)

        row_b = QHBoxLayout()
        self.lex_count = QLabel("…")
        self.lex_grow = QPushButton("现在补一批")
        self.lex_grow.setFixedWidth(96)
        self.lex_grow.clicked.connect(self.grow_lexicon)
        row_b.addWidget(self.lex_count)
        row_b.addStretch(1)
        row_b.addWidget(self.lex_grow)
        fv.addRow("当前语料", self._wrap(row_b))

        self.lex_tired = QPushButton("刚念的那句我嫌烦")
        self.lex_tired.clicked.connect(self.mark_tired)
        fv.addRow("", self.lex_tired)
        fv.addRow("", hint("不填 key 也能长：补货写成订单，WorkBuddy 的后台助手"
                           "定时看一次，写好的句子自动进池。"
                           "填了 key 就当场直连，快一点。"
                           "嫌烦记三次就沉底，不再念。"))
        v.addWidget(gv)

        # --- 念法：同一副嗓子按场景换情绪
        gs = QGroupBox("念法（表演指令）")
        fs = QVBoxLayout(gs)
        row_s = QHBoxLayout()
        self.style_on = QCheckBox("按场景换语气")
        self.style_on.setChecked(True)
        self.style_lang = QComboBox()
        self.style_lang.addItem("中文专用模型", "zh")
        self.style_lang.addItem("自动（多语言）", "")
        row_s.addWidget(self.style_on)
        row_s.addStretch(1)
        row_s.addWidget(QLabel("走哪个"))
        row_s.addWidget(self.style_lang)
        fs.addLayout(row_s)

        self.style_tbl = QTableWidget(0, 4)
        self.style_tbl.setHorizontalHeaderLabels(
            ["类别", "力度", "停顿", "表演指令（怎么念）"])
        hh = self.style_tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.style_tbl.setColumnWidth(0, 76)
        self.style_tbl.setColumnWidth(1, 52)
        self.style_tbl.setColumnWidth(2, 62)
        self.style_tbl.verticalHeader().setVisible(False)
        self.style_tbl.setMinimumHeight(212)
        fs.addWidget(self.style_tbl)
        fs.addWidget(hint("同一副嗓子用一个情绪念完所有话，出来就是小声念稿。"
                          "力度 1–10，越大越听指令（1 等于几乎不听）。"
                          "停顿写 [停顿]/[啧嘴]/[叹气]，留空就不插。"
                          "紧急类留空——实测会空 1.5 秒，反而不像警报。"))
        v.addWidget(gs)

        # --- 其它
        g3 = QGroupBox("其它")
        f3 = QFormLayout(g3)
        self.city = QLineEdit()
        f3.addRow("城市（天气用）", self.city)
        self.auto_eat = QComboBox()
        for label, ms in (("停 1 秒就吃", 1000), ("停 1.5 秒就吃", 1500),
                          ("停 2 秒就吃", 2000), ("不自动吃（松手才算）", 0)):
            self.auto_eat.addItem(label, ms)
        self.auto_eat.setToolTip("文件拖到身上停住不动，到这点时间不用松手也会吃掉；\n"
                                 "光标一直在挪会重新计时，慢慢划过不会误吃。")
        f3.addRow("拖到身上停够了", self.auto_eat)
        self.menu_theme = QComboBox()
        for label, val in (("跟随系统（推荐）", "auto"), ("浅色", "light"), ("深色", "dark")):
            self.menu_theme.addItem(label, val)
        self.menu_theme.setToolTip("右键菜单的配色。\n"
                                   "跟随系统 = Windows 开了深色模式就用深色菜单。")
        f3.addRow("右键菜单配色", self.menu_theme)
        v.addWidget(g3)
        v.addStretch(1)

    # ---------------- 念法（表演指令） ----------------
    def _style_mod(self):
        """念法模块。缺了/旧了就当没有，一整组灰掉，别影响别的控件。"""
        return fresh_module("voice_style", ("load", "style_for", "save", "tts_text"))

    def _fill_styles(self):
        m = self._style_mod()
        if m is None:
            for w in (self.style_on, self.style_lang, self.style_tbl):
                w.setEnabled(False)
            self.style_tbl.setRowCount(1)
            self.style_tbl.setItem(0, 0, QTableWidgetItem("voice_style 没加载"))
            return
        try:
            cfg = m.load(force=True)
        except Exception:
            cfg = {}
        self.style_on.setChecked(bool(cfg.get("enabled", True)))
        cur = str(cfg.get("language") or "")
        i = self.style_lang.findData(cur)
        self.style_lang.setCurrentIndex(i if i >= 0 else 0)

        styles = cfg.get("styles") or {}
        kinds = self._lex_kinds()
        self.style_tbl.setRowCount(len(kinds))
        for r, k in enumerate(kinds):
            st = styles.get(k) or {}
            it0 = QTableWidgetItem(k)
            it0.setFlags(Qt.ItemFlag.ItemIsEnabled)      # 类别名只读，改名会丢配置
            self.style_tbl.setItem(r, 0, it0)
            self.style_tbl.setItem(r, 1, QTableWidgetItem(str(st.get("guidance", ""))))
            self.style_tbl.setItem(r, 2, QTableWidgetItem(str(st.get("tag") or "")))
            self.style_tbl.setItem(r, 3, QTableWidgetItem(str(st.get("instructions") or "")))

    def _save_styles(self):
        """写 voice_style.json。监听进程按 mtime 自己重读，不用重启。"""
        m = self._style_mod()
        if m is None:
            return True, ""
        try:
            base = dict(m.load(force=True))
        except Exception:
            base = {}
        styles = dict(base.get("styles") or {})
        for r in range(self.style_tbl.rowCount()):
            def cell(c):
                it = self.style_tbl.item(r, c)
                return (it.text() if it is not None else "").strip()
            k = cell(0)
            if not k:
                continue
            try:
                g = float(cell(1))
            except ValueError:
                g = float((styles.get(k) or {}).get("guidance") or 2.0)
            g = min(max(g, 1.0), 10.0)
            cur = dict(styles.get(k) or {})
            cur.update({"guidance": g, "tag": cell(2), "instructions": cell(3)})
            styles[k] = cur
        base["styles"] = styles
        base["enabled"] = self.style_on.isChecked()
        base["language"] = self.style_lang.currentData() or ""
        try:
            m.save(base)
        except Exception as e:
            return False, f"念法没写进去：{e}"
        return True, ""

    def _wrap(self, layout):
        w = QWidget()
        w.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return w

    # ---------------- 语料扩充 ----------------
    def _lex(self):
        """成长层。模块坏了/没了就返回 None，面板照开。"""
        try:
            import voice_lexicon
            return voice_lexicon.default_lexicon()
        except Exception:
            return None

    def _lex_kinds(self):
        try:
            import notify_voice
            return list(notify_voice.LAYERS)
        except Exception:
            return ["money", "work", "chat", "tech", "notice", "urgent",
                    "flood", "phone", "phone_back"]

    def refresh_lex_count(self):
        lex = self._lex()
        if lex is None:
            self.lex_count.setText("语料模块没加载")
            self.lex_grow.setEnabled(False)
            self.lex_tired.setEnabled(False)
            return
        n = sum(len(lex.pool(k)) for k in self._lex_kinds())
        grown = int((lex.data.get("meta") or {}).get("grown", 0))
        line = f"{n} 句（自己长了 {grown} 句）"
        try:
            import voice_lexicon as LX
            waiting = LX.inbox_waiting()
            need = LX.need_summary()
        except Exception:
            waiting, need = 0, {}
        if waiting:
            line += f" · 信箱待取 {waiting} 句"
        if need:
            line += f" · 待补 {len(need)} 类"
        self.lex_count.setText(line)

    def grow_lexicon(self):
        lex = self._lex()
        if lex is None:
            QMessageBox.warning(self, "语料", "voice_lexicon 模块没加载")
            return
        key = self.lex_key.text().strip()
        if key:                       # 填了才落盘，没填就不碰 config
            try:
                c = read_json(CONFIG_PATH, {})
                c["ds_api_key"] = key
                write_json(CONFIG_PATH, c)
                self.cfg["ds_api_key"] = key
            except Exception as e:
                QMessageBox.warning(self, "语料", f"key 没写进去：{e}")
                return

        # 先把 WorkBuddy 已经写好、还躺在信箱里的收进来（不用等池子见底）
        try:
            lex.absorb_inbox()
        except Exception:
            pass
        self.lex_grow.setEnabled(False)
        self.lex_grow.setText("在补…")
        QApplication.processEvents()        # 不刷一次的话按钮灰了都看不见
        got = 0
        try:
            for k in self._lex_kinds():
                got += lex.replenish(k, force=True)
        except Exception as e:
            QMessageBox.warning(self, "语料", f"补货出错：{e}")
        finally:
            self.lex_grow.setEnabled(True)
            self.lex_grow.setText("现在补一批")
        self.refresh_lex_count()
        if got:
            QMessageBox.information(self, "语料", f"补进 {got} 句，下条通知就念得到")
            return
        try:
            import voice_lexicon as LX
            pending = LX.need_summary()
        except Exception:
            pending = {}
        if pending:
            QMessageBox.information(
                self, "语料",
                f"已经给 WorkBuddy 下单了（{len(pending)} 类）。\n\n"
                "它的后台助手每 2 小时看一次订单，写好自动进池，\n"
                "到时候你听到的就是新句子。不用守着，也不用再点。\n\n"
                "想当场就要，就在上面填一把 DeepSeek key。")
        else:
            QMessageBox.warning(self, "语料",
                                "一句没补到。查 key、网络，或者模型没写出合格的句子")

    def mark_tired(self):
        """最近念过的那句，点一下直接沉底（= 嫌三次）。"""
        lex = self._lex()
        if lex is None:
            return
        newest = None
        for k in self._lex_kinds():
            for e in lex.data["pools"].get(k) or []:
                if isinstance(e, dict) and e.get("last") and (
                        newest is None or int(e["last"]) > newest[1]):
                    newest = (k, int(e["last"]), e["t"])
        if not newest:
            QMessageBox.information(self, "语料", "它还没念过几句，等会儿再来")
            return
        k, _, text = newest
        for _ in range(3):
            lex.learn(k, text, "bad")
        QMessageBox.information(self, "语料", f"记下了：「{text}」，以后少说")

    def _sync_auto_stuck(self):
        """勾不勾「念一句」决定台词框和间隔能不能改。"""
        line = getattr(self, "auto_line", None)   # 构造时先连信号、后建输入框
        if line is None:
            return
        on = self.auto_stuck.isChecked()
        line.setEnabled(on)
        # 两个方向都要设：以前只在取消勾选时替换，重新勾上那句「未启用」会一直留着
        line.setPlaceholderText(
            AUTO_STUCK_LINE if on else "（未启用语音提醒，只叮一声）")
        gap = getattr(self, "auto_gap", None)
        if gap is not None:
            gap.setEnabled(on)

    def preview_sound(self):
        """试听：按当前控件值直接播一次，不写文件（没保存也能听）。"""
        tpl = self.snd_tpl.currentData()
        vol = self.snd_vol.value() / 100.0
        if tpl == "custom":
            p = self.snd_path.text().strip()
            if not (p and os.path.isfile(p)):
                QMessageBox.information(self, "提示音", "先选一个 wav 文件再试听")
                return
            try:
                import winsound
                winsound.PlaySound(
                    p, winsound.SND_FILENAME | winsound.SND_ASYNC
                    | winsound.SND_NODEFAULT)
            except Exception as e:
                QMessageBox.warning(self, "提示音", f"这个文件播不了：{e}")
            return
        m = sound_module()
        if m is None:
            QMessageBox.warning(self, "提示音", "提示音模块没加载（ding.py 不见了）")
            return
        if not m.play(tpl, vol):
            QMessageBox.warning(self, "提示音", "没播出去，可能音频设备被占用")

    def pick_sound(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "选一个提示音", "", "wav 音频 (*.wav)")
        if p:
            self.snd_path.setText(p)
            self.snd_tpl.setCurrentIndex(self.snd_tpl.findData("custom"))

    def _fill(self):
        c = self.cfg
        self.key.setPlainText(str(c.get("tts_key") or ""))
        self.voice.setText(str(c.get("tts_voice") or ""))
        self.speed.setValue(float(c.get("tts_speed") or 1.0))
        self.volume.setValue(float(c.get("tts_volume") or 0.9))
        self.tts_on.setChecked(bool(c.get("tts_enabled", True)))
        self.tts_ambient.setChecked(bool(c.get("tts_ambient", False)))
        self.no_restart.setChecked(bool(c.get("clip_no_restart", True)))
        self.city.setText(str(c.get("city") or "汕头"))
        idx = self.auto_eat.findData(int(c.get("drag_auto_eat_ms", 1000) or 0))
        self.auto_eat.setCurrentIndex(idx if idx >= 0 else 0)
        tidx = self.menu_theme.findData(c.get("menu_theme") or "auto")
        self.menu_theme.setCurrentIndex(tidx if tidx >= 0 else 0)
        self.lex_key.setText(str(c.get("ds_api_key") or ""))
        self.refresh_lex_count()
        self._fill_styles()

        fg = self.anim.get("fidget_gap") or c.get("fidget_gap") or [10, 24]
        ag = self.anim.get("act_gap") or c.get("act_gap") or [90, 240]
        self.fg0.setValue(int(fg[0]))
        self.fg1.setValue(int(fg[1]))
        self.ag0.setValue(int(ag[0]))
        self.ag1.setValue(int(ag[1]))

        m = sound_module()
        if m is None:
            # 面板照开，只把这组按死，别让人点进去只收获一堆弹窗
            for w in (self.snd_on, self.snd_tpl, self.snd_try, self.snd_vol,
                      self.snd_path, self.snd_pick):
                w.setEnabled(False)
            self.snd_try.setToolTip("ding.py 版本不对，更新后重启桌宠")
        snd = m.load_cfg(force=True) if m is not None else sound_defaults()
        self.snd_on.setChecked(bool(snd.get("enabled", True)))
        i = self.snd_tpl.findData(snd.get("template", "ding"))
        self.snd_tpl.setCurrentIndex(max(0, i))
        pct = int(round(float(snd.get("volume", 0.7)) * 100))
        self.snd_vol.setValue(max(5, min(95, pct)))
        self.snd_vol_lbl.setText(f"{max(5, min(95, pct))}%")
        self.snd_path.setText(str(snd.get("custom_path") or ""))

        hk = read_json(HOOK_CFG_PATH, {})
        i = self.auto_mode.findData(str(hk.get("automation_mode") or "ding"))
        self.auto_mode.setCurrentIndex(max(0, i))
        self.ding_open.setValue(int(hk.get("automation_open_dings", 1) or 0))
        self.ding_close.setValue(int(hk.get("automation_close_dings", 2) or 0))
        self.auto_stuck.setChecked(bool(hk.get("automation_notify_speak", True)))
        self.auto_line.setText(str(hk.get("automation_notify_text") or AUTO_STUCK_LINE))
        gap = int(hk.get("automation_notify_gap", AUTO_STUCK_GAP) or AUTO_STUCK_GAP)
        self.auto_gap.setValue(max(15, min(600, gap)))
        self._sync_auto_stuck()

    def collect_cfg(self):
        c = dict(self.cfg)
        c["tts_key"] = "\n".join(
            w for w in split_words(self.key.toPlainText()))
        c["tts_voice"] = self.voice.text().strip()
        c["tts_speed"] = round(self.speed.value(), 2)
        c["tts_volume"] = round(self.volume.value(), 2)
        c["tts_enabled"] = self.tts_on.isChecked()
        c["tts_ambient"] = self.tts_ambient.isChecked()
        c["clip_no_restart"] = self.no_restart.isChecked()
        c["city"] = self.city.text().strip() or "汕头"
        c["drag_auto_eat_ms"] = int(self.auto_eat.currentData() or 0)
        c["menu_theme"] = self.menu_theme.currentData() or "auto"
        c["ds_api_key"] = self.lex_key.text().strip()
        c["fidget_gap"] = [self.fg0.value(), self.fg1.value()]
        c["act_gap"] = [self.ag0.value(), self.ag1.value()]
        return c

    def save(self):
        """有 pet 就走它（内存和磁盘一起改），没有就直接写文件。"""
        cfg = self.collect_cfg()
        if self.pet is not None:
            try:
                self.pet.apply_settings(cfg)
            except Exception as e:
                return False, f"应用失败：{e}"
        else:
            try:
                write_json(CONFIG_PATH, cfg)
            except Exception as e:
                return False, f"配置没写进去：{e}"

        # 动作节奏在 anim_config.json，那边才是权威
        try:
            import anim_config
            ac = anim_config.load(ANIM_PATH, quiet=True)
            ac["fidget_gap"] = cfg["fidget_gap"]
            ac["act_gap"] = cfg["act_gap"]
            anim_config.save(ac, ANIM_PATH, quiet=True)
            if self.pet is not None and hasattr(self.pet, "reload_anim_cfg"):
                self.pet.reload_anim_cfg()
        except Exception as e:
            return True, f"配置已存，但动作节奏没写进去：{e}"

        # 提示音是独立配置文件，ding 播放前按 mtime 自己重读，不用通知 pet
        m = sound_module()
        if m is not None:
            try:
                m.save_cfg({
                    "enabled": self.snd_on.isChecked(),
                    "template": self.snd_tpl.currentData(),
                    "volume": self.snd_vol.value() / 100.0,
                    "custom_path": self.snd_path.text().strip(),
                })
            except Exception as e:
                return True, f"配置已存，但提示音没写进去：{e}"

        # 定时任务提示音在 hooks/hook_config.json：hook 每次事件现读文件，
        # 所以写完就生效，不用重启什么
        try:
            hk = read_json(HOOK_CFG_PATH, {})
            hk["automation_mode"] = self.auto_mode.currentData()
            hk["automation_open_dings"] = self.ding_open.value()
            hk["automation_close_dings"] = self.ding_close.value()
            hk["automation_notify_speak"] = self.auto_stuck.isChecked()
            hk["automation_notify_text"] = self.auto_line.text().strip() \
                or AUTO_STUCK_LINE
            hk["automation_notify_gap"] = self.auto_gap.value()
            write_json(HOOK_CFG_PATH, hk)
        except Exception as e:
            return True, f"配置已存，但定时任务提示音没写进去：{e}"
        ok, msg = self._save_styles()
        if not ok:
            return False, msg
        return True, "运行设置已生效"


# ================================================================ 通知页
def notify_defaults():
    """默认规则从 phone_notify 拿，保证面板和监听进程用的是同一份。

    拿不到（模块被删、依赖缺、内存里是没 DEFAULT_RULES 的旧版）就退回一份
    最小可用规则——宁可面板显示得不全，也不能让「通知」页签打不开。
    """
    m = fresh_module("phone_notify", ("DEFAULT_RULES",))
    if m is not None:
        try:
            return dict(m.DEFAULT_RULES)
        except Exception as e:
            print("[panel] 读不到通知默认规则:", e)
    return {
        "_note": "手机通知播报规则。",
        "enabled": True, "chat_only_at": True, "chat_skip_media": True,
        "chat_pkgs": ["com.tencent.mm", "com.tencent.mobileqq", "com.tencent.tim"],
        "mute_pkgs": ["android"], "urgent_words": ["地震", "预警"],
        "skip_title": ["广告", "优惠"], "poll_seconds": 2.0,
        "flood_gap": 90, "flood_max": 2, "core_max": 30, "urgent_max": 46,
    }


class NotifyTab(QWidget):
    """手机通知：念什么、挡什么、多久抓一次。

    规则写程序根目录的 notify_config.json，监听进程按文件 mtime 自己重读，
    所以这里保存完不需要重启监听——这也意味着**不能**只改内存不落盘。
    """

    # adb 探测最长要几秒，放主线程会卡住整个面板，所以丢后台线程回传。
    # 回传 (文字, 颜色) 而不是在子线程里直接碰控件——Qt 控件只能在主线程动。
    status_done = Signal(str, str)

    def __init__(self, pet=None):
        super().__init__()
        self.pet = pet
        self.defaults = notify_defaults()
        self.raw = read_json(NOTIFY_CFG_PATH, {})
        self._build()
        self._fill()
        self.status_done.connect(self._set_status)
        self.refresh_status()

    def _set_status(self, text, color="#555"):
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{color};")

    # -------------------------------------------------- 界面
    def _build(self):
        v = QVBoxLayout(self)
        v.setSpacing(10)

        # --- 开关
        g1 = QGroupBox("播报开关")
        grid = QGridLayout(g1)
        grid.addWidget(hint(
            "这几条决定「念什么」。保存后监听进程两秒内自己重读规则，不用重启。"),
            0, 0, 1, 2)
        self.on = QCheckBox("启用手机通知播报（关掉只记账，不出声）")
        self.auto_connect = QCheckBox("开机自动连手机并开监听")
        self.chat_only_at = QCheckBox("聊天类只念「群里有人@我」")
        self.remind = QCheckBox("没监听时定时提醒我")
        self.chat_skip_media = QCheckBox("群里@我只发图片/视频/通话时，也不念")
        grid.addWidget(self.on, 1, 0)
        grid.addWidget(self.auto_connect, 1, 1)
        grid.addWidget(self.chat_only_at, 2, 0)
        grid.addWidget(self.remind, 2, 1)
        grid.addWidget(self.chat_skip_media, 3, 0)
        grid.addWidget(hint("微信私聊、文件、红包、语音一概不念；@所有人 不算点你。"),
                       3, 1)
        v.addWidget(g1, 0)

        # --- 关键词
        g2 = QGroupBox("关键词与 App")
        gv = QVBoxLayout(g2)
        gv.addWidget(hint(
            "每行一个，逗号分隔也行。判断顺序：聊天类 → 紧急词无条件放行 → "
            "静音 App → 营销词拦截 → 其余都念。"))
        row = QGridLayout()
        self.urgent = QPlainTextEdit()
        self.urgent.setPlaceholderText("地震\n台风\n停水\n停电")
        self.skip_title = QPlainTextEdit()
        self.skip_title.setPlaceholderText("广告\n限时\n下载中")
        self.mute_pkgs = QPlainTextEdit()
        self.mute_pkgs.setPlaceholderText("android\ncom.hihonor.systemmanager")
        for col, (lab, w) in enumerate((
                ("① 无条件念（紧急词）", self.urgent),
                ("② 标题命中就不念（营销词）", self.skip_title),
                ("③ 整类静音（App 包名）", self.mute_pkgs))):
            w.setMinimumHeight(72)
            row.addWidget(QLabel(lab), 0, col)
            row.addWidget(w, 1, col)
            row.setColumnStretch(col, 1)
        gv.addLayout(row, 1)
        v.addWidget(g2, 1)

        # --- 节奏（两列排，省一行高度，否则整页比窗口还高）
        g3 = QGroupBox("播报节奏")
        g3g = QGridLayout(g3)
        self.poll = QDoubleSpinBox()
        self.poll.setRange(0.5, 10.0)
        self.poll.setSingleStep(0.5)
        self.poll.setSuffix(" 秒")
        self.poll.setMaximumWidth(110)
        g3g.addWidget(QLabel("多久抓一次"), 0, 0)
        g3g.addWidget(self.poll, 0, 1)

        line1 = QHBoxLayout()
        self.flood_gap = QSpinBox()
        self.flood_gap.setRange(10, 3600)
        self.flood_gap.setSuffix(" 秒")
        self.flood_gap.setMaximumWidth(96)
        self.flood_max = QSpinBox()
        self.flood_max.setRange(1, 20)
        self.flood_max.setMaximumWidth(64)
        line1.addWidget(self.flood_gap)
        line1.addWidget(QLabel("内最多念"))
        line1.addWidget(self.flood_max)
        line1.addWidget(QLabel("条"))
        line1.addStretch(1)
        g3g.addWidget(QLabel("防刷屏（同一 App）"), 0, 2)
        g3g.addWidget(self._wrap(line1), 0, 3)

        self.core_max = QSpinBox()
        self.core_max.setRange(10, 80)
        self.core_max.setSuffix(" 字")
        self.core_max.setMaximumWidth(110)
        g3g.addWidget(QLabel("一句话上限"), 1, 0)
        g3g.addWidget(self.core_max, 1, 1)

        self.urgent_max = QSpinBox()
        self.urgent_max.setRange(20, 160)
        self.urgent_max.setSuffix(" 字")
        self.urgent_max.setMaximumWidth(110)
        g3g.addWidget(QLabel("紧急通知放宽到"), 1, 2)
        g3g.addWidget(self.urgent_max, 1, 3)
        g3g.setColumnStretch(1, 1)
        g3g.setColumnStretch(3, 1)
        g3g.setHorizontalSpacing(16)
        v.addWidget(g3, 0)

        # --- 状态与操作
        g4 = QGroupBox("手机与监听")
        gv4 = QVBoxLayout(g4)
        self.status = QLabel("读状态中……")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#555;")
        gv4.addWidget(self.status)
        hb = QHBoxLayout()
        for text, fn in (("刷新状态", lambda: self.refresh_status()),
                         ("连接手机", self.do_connect),
                         ("启动监听", self.do_start),
                         ("停止监听", self.do_stop),
                         ("看监听日志", self.open_log)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            hb.addWidget(b)
        hb.addStretch(1)
        gv4.addLayout(hb)
        v.addWidget(g4, 0)

    def _wrap(self, layout):
        w = QWidget()
        w.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return w

    def _merged(self):
        """默认值打底 + 磁盘上的覆盖。缺字段时按默认，不会算出空列表。"""
        d = dict(self.defaults)
        d.update({k: v for k, v in (self.raw or {}).items() if k in self.defaults})
        return d

    def _fill(self):
        d = self._merged()
        self.on.setChecked(bool(d.get("enabled", True)))
        self.chat_only_at.setChecked(bool(d.get("chat_only_at", True)))
        self.chat_skip_media.setChecked(bool(d.get("chat_skip_media", True)))
        self.urgent.setPlainText("\n".join(d.get("urgent_words") or []))
        self.skip_title.setPlainText("\n".join(d.get("skip_title") or []))
        self.mute_pkgs.setPlainText("\n".join(d.get("mute_pkgs") or []))
        self.poll.setValue(float(d.get("poll_seconds") or 2.0))
        self.flood_gap.setValue(int(d.get("flood_gap") or 90))
        self.flood_max.setValue(int(d.get("flood_max") or 2))
        self.core_max.setValue(int(d.get("core_max") or 30))
        self.urgent_max.setValue(int(d.get("urgent_max") or 46))

        cfg = read_json(CONFIG_PATH, {})
        self.auto_connect.setChecked(bool(cfg.get("phone_autoconnect", True)))
        self.remind.setChecked(bool(cfg.get("phone_remind", True)))

    # -------------------------------------------------- 状态与操作
    def _pp(self):
        try:
            import phone_panel as pp
            return pp
        except Exception as e:
            print("[panel] 手机模块不可用:", e)
            return None

    def refresh_status(self):
        """后台探一次手机 + 监听状态。adb 探测最长几秒，不能卡界面。"""
        self._set_status("读状态中……")

        def work():
            pp = self._pp()
            if pp is None:
                self.status_done.emit("手机模块没加载（缺 phone_panel.py 或依赖）", "#c0392b")
                return
            if not pp.adb_available():
                self.status_done.emit(
                    "找不到 adb：Escrcpy 没装在默认位置，手机功能用不了", "#c0392b")
                return
            parts = []
            try:
                parts.append("通知监听：" + ("在跑" if pp.notify_running() else "没在跑"))
            except Exception as e:
                parts.append(f"监听状态读不到：{e}")
            try:
                _ok, msg = pp.link_status()
                parts.insert(0, "手机：" + msg)
            except Exception as e:
                parts.insert(0, f"手机：查询失败（{e}）")
            tail = pp.notify_tail(1)
            if tail:
                parts.append("最近日志：" + tail[-1][-60:])
            self.status_done.emit("；".join(parts), "#555")

        threading.Thread(target=work, daemon=True).start()

    def _run_async(self, fn, busy_text):
        self._set_status(busy_text)

        def work():
            try:
                ok, msg = fn()
            except Exception as e:
                ok, msg = False, f"出错：{e}"
            self.status_done.emit(("OK " if ok else "失败：") + msg,
                                  "#2e7d32" if ok else "#c0392b")
            self.refresh_status()

        threading.Thread(target=work, daemon=True).start()

    def do_connect(self):
        pp = self._pp()
        if pp is None:
            self.status.setText("手机模块没加载，连不了")
            return
        self._run_async(pp.connect, "正在连手机……")

    def do_start(self):
        pp = self._pp()
        if pp is None:
            self.status.setText("手机模块没加载，起不了监听")
            return
        self._run_async(pp.notify_start, "正在启动通知监听……")

    def do_stop(self):
        pp = self._pp()
        if pp is None:
            self.status.setText("手机模块没加载")
            return
        self._run_async(pp.notify_stop, "正在停止通知监听……")

    def open_log(self):
        path = os.path.join(ROOT, "phone_notify.log")
        if not os.path.exists(path):
            self.status.setText("还没有监听日志（监听没启动过）")
            return
        try:
            os.startfile(path)
        except Exception as e:
            self.status.setText(f"打不开日志：{e}")

    # -------------------------------------------------- 存取
    def collect(self):
        d = self._merged()
        d["enabled"] = self.on.isChecked()
        d["chat_only_at"] = self.chat_only_at.isChecked()
        d["chat_skip_media"] = self.chat_skip_media.isChecked()
        d["urgent_words"] = split_words(self.urgent.toPlainText())
        d["skip_title"] = split_words(self.skip_title.toPlainText())
        d["mute_pkgs"] = split_words(self.mute_pkgs.toPlainText())
        d["poll_seconds"] = round(self.poll.value(), 2)
        d["flood_gap"] = self.flood_gap.value()
        d["flood_max"] = self.flood_max.value()
        d["core_max"] = self.core_max.value()
        d["urgent_max"] = self.urgent_max.value()
        # 聊天类包名面板没开放编辑，但必须保住：清空了聊天过滤就整体失效
        if not d.get("chat_pkgs"):
            d["chat_pkgs"] = list(self.defaults.get("chat_pkgs") or [])
        return d

    def save(self):
        d = self.collect()
        try:
            write_json(NOTIFY_CFG_PATH, d)
        except Exception as e:
            return False, f"通知规则没写进去：{e}"

        ok, msg = self._save_phone_switches()
        if not ok:
            return False, msg
        return True, "通知规则已存，监听两秒内自己读"

    def _save_phone_switches(self):
        """两个手机开关在 config.json 里，有 pet 就走它，别让内存里的旧值把盘盖回去。"""
        auto = self.auto_connect.isChecked()
        rem = self.remind.isChecked()
        try:
            if self.pet is not None and hasattr(self.pet, "set_phone_autoconnect"):
                # 只在真变了的时候调：set 会自动重排「开机自动连接」的触发时间
                if bool(self.pet.cfg.get("phone_autoconnect", True)) != auto:
                    self.pet.set_phone_autoconnect(auto)
                if bool(self.pet.cfg.get("phone_remind", True)) != rem:
                    self.pet.set_phone_remind(rem)
            else:
                cfg = read_json(CONFIG_PATH, {})
                if bool(cfg.get("phone_autoconnect", True)) != auto \
                        or bool(cfg.get("phone_remind", True)) != rem:
                    cfg["phone_autoconnect"] = auto
                    cfg["phone_remind"] = rem
                    write_json(CONFIG_PATH, cfg)
        except Exception as e:
            return False, f"通知规则已存，但手机开关没写进去：{e}"
        return True, ""


# ================================================================ 移机页
class PortableTab(QWidget):
    """换电脑时的体检：缺什么、路径对不对、配置怎么带走。"""

    DEPS = [
        ("PySide6", "界面（必需）"),
        ("requests", "语音请求（必需）"),
        ("psutil", "CPU 内存状态"),
        ("pynvml", "显卡温度"),
        ("PIL", "图片处理"),
    ]

    def __init__(self, pet=None):
        super().__init__()
        self.pet = pet
        self._build()
        self.check()

    def _build(self):
        v = QVBoxLayout(self)
        v.setSpacing(10)

        g = QGroupBox("环境体检")
        gv = QVBoxLayout(g)
        gv.addWidget(hint(
            "换电脑或重装后先跑一遍。红色项不解决，桌宠起不来或会哑。"))
        self.tbl = QTableWidget(0, 3)
        self.tbl.setHorizontalHeaderLabels(["检查项", "结果", "说明"])
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tbl.verticalHeader().setVisible(False)
        gv.addWidget(self.tbl, 1)

        hb = QHBoxLayout()
        b = QPushButton("重新体检")
        b.clicked.connect(self.check)
        hb.addWidget(b)
        b2 = QPushButton("打开项目目录")
        b2.clicked.connect(lambda: os.startfile(ROOT))
        hb.addWidget(b2)
        hb.addStretch(1)
        gv.addLayout(hb)
        v.addWidget(g, 1)

        g2 = QGroupBox("配置迁移")
        gv2 = QVBoxLayout(g2)
        gv2.addWidget(hint(
            "导出的文件里有 API Key 和端口 token，别直接发群里。"
            "换电脑时带上它，人设、语音、动作设置、通知规则一起搬过去。"))
        hb2 = QHBoxLayout()
        b3 = QPushButton("导出配置包")
        b3.clicked.connect(self.export_bundle)
        b4 = QPushButton("导入配置包")
        b4.clicked.connect(self.import_bundle)
        hb2.addWidget(b3)
        hb2.addWidget(b4)
        hb2.addStretch(1)
        gv2.addLayout(hb2)
        v.addWidget(g2, 0)

    # -------------------------------------------------- 体检
    def check(self):
        rows = []

        def add(name, ok, why, hard=True):
            rows.append((name, "OK" if ok else ("缺失" if hard else "注意"), why, ok))

        add("项目根目录", os.path.isdir(ROOT), ROOT)
        add("venv 解释器",
            os.path.exists(os.path.join(ROOT, "venv", "Scripts", "python.exe")),
            "没有 venv 就用系统 Python，注意依赖是否装全")
        add("启动脚本",
            os.path.exists(os.path.join(ROOT, "start-pet.cmd")),
            "start-pet.cmd")

        for mod, why in self.DEPS:
            try:
                ok = importlib.util.find_spec(mod) is not None
            except Exception:
                ok = False
            add(f"依赖 {mod}", ok, why)

        for name, path in (("人设", PERSONA_PATH), ("配置", CONFIG_PATH),
                           ("动作设置", ANIM_PATH)):
            add(f"{name}文件", os.path.exists(path), os.path.basename(path))

        # 体检要看真正在用的素材目录。早先这里写的是 assets_anim（已废弃的旧目录），
        # 结果删掉旧目录后体检就报「0 个动作」，看着像素材丢了。
        assets = os.path.join(HERE, "assets_anim_v2")
        n = 0
        if os.path.isdir(assets):
            n = len([d for d in os.listdir(assets)
                     if os.path.isdir(os.path.join(assets, d))])
        add("动作素材", n > 0, f"assets_anim_v2 里 {n} 个动作")

        # 端口：能 bind 说明空闲
        for port in (8790, 8795):
            free = True
            try:
                s = socket.socket()
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                s.close()
            except OSError:
                free = False
            add(f"端口 {port}", True,
                "空闲" if free else "已被占用（有实例在跑就是正常）", hard=False)

        # 日志目录可写
        try:
            logdir = os.path.join(ROOT, "logs")
            os.makedirs(logdir, exist_ok=True)
            probe = os.path.join(logdir, ".write_probe")
            with open(probe, "w") as f:
                f.write("1")
            os.remove(probe)
            writable = True
        except Exception:
            writable = False
        add("日志目录可写", writable, "logs/")

        self.tbl.setRowCount(len(rows))
        for i, (name, res, why, ok) in enumerate(rows):
            self.tbl.setItem(i, 0, QTableWidgetItem(name))
            it = QTableWidgetItem(res)
            it.setForeground(Qt.GlobalColor.darkGreen if ok
                             else Qt.GlobalColor.darkRed)
            self.tbl.setItem(i, 1, it)
            self.tbl.setItem(i, 2, QTableWidgetItem(why))
        self.tbl.resizeRowsToContents()

        bad = sum(1 for r in rows if not r[3])
        return bad

    # -------------------------------------------------- 迁移
    def export_bundle(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出配置包", os.path.join(ROOT, "dafeiyu-config.json"),
            "JSON (*.json)")
        if not path:
            return
        bundle = {
            "_note": "大肥鱼配置包。含 API Key，别外传。换电脑后导入即可。",
            "_from_root": ROOT,
            "config": read_json(CONFIG_PATH, {}),
            "persona": read_json(PERSONA_PATH, {}),
            "anim": read_json(ANIM_PATH, {}),
            "notify": read_json(NOTIFY_CFG_PATH, {}),
        }
        try:
            write_json(path, bundle)
            QMessageBox.information(self, "导出成功", f"已导出到：\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def import_bundle(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入配置包", ROOT, "JSON (*.json)")
        if not path:
            return
        b = read_json(path, {})
        if not b.get("config") and not b.get("persona"):
            QMessageBox.warning(self, "导入失败", "这个文件不像配置包。")
            return
        if QMessageBox.question(
                self, "确认导入",
                "会用包里的内容覆盖当前配置和人设。\n先备份当前的吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            self._backup_now()

        done = []
        try:
            if b.get("persona"):
                write_json(PERSONA_PATH, b["persona"])
                import pet_memory
                pet_memory.reload_persona()
                done.append("人设")
            if b.get("anim"):
                write_json(ANIM_PATH, b["anim"])
                if self.pet is not None and hasattr(self.pet, "reload_anim_cfg"):
                    self.pet.reload_anim_cfg()
                done.append("动作设置")
            if b.get("notify"):
                write_json(NOTIFY_CFG_PATH, b["notify"])
                done.append("通知规则")
            if b.get("config"):
                cfg = b["config"]
                if self.pet is not None:
                    self.pet.apply_settings(cfg)
                else:
                    write_json(CONFIG_PATH, cfg)
                done.append("运行配置")
        except Exception as e:
            QMessageBox.warning(self, "导入中断", f"已导入：{done}\n出错：{e}")
            return
        QMessageBox.information(self, "导入完成", "已导入：" + "、".join(done))

    def _backup_now(self):
        import shutil
        import time
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for src in (PERSONA_PATH, CONFIG_PATH, ANIM_PATH, NOTIFY_CFG_PATH):
            try:
                shutil.copy(src, f"{src}.bak-{stamp}")
            except Exception:
                pass


# ================================================================ 资源释放
class CleanTab(QWidget):
    """一张应用表 + 三个开关。

    表是每次打开/点「重新扫描」现算的，不是手写名单 —— 用户看到的永远是机器上
    真实存在的应用：新装的会自己冒出来，卸载的会自己消失，内存数也是当下的。

    分组按「应用」不按进程名：微信的 Weixin.exe + WeChatAppEx.exe + crashpad
    算一个应用，整组一起判，不会只清一半。

    光有应用名不够认人（office6、cef 这种名字谁看得懂），所以带一列「窗口标题」
    —— 那是它现在开着什么窗口，一眼就知道是什么软件。系统级进程整行灰掉，
    顶部还有筛选条，可以把它们滤掉只看自己装的。
    """

    done = Signal(str)

    MODES = (("protect", "保护"), ("close", "只关窗"),
             ("clean", "可清理"), ("ignore", "不管"))
    COLS = ("应用", "窗口标题", "内存", "进程", "类别", "状态", "这一趟会怎么处理")

    def __init__(self, pet=None):
        super().__init__()
        self.pet = pet
        self._mod = None
        self._busy = False
        self._loading = True
        self._apps = []
        self._build()
        # 开关改一个字符就写盘太频繁，攒 350ms 再刷新表格
        self._deb = QTimer(self)
        self._deb.setSingleShot(True)
        self._deb.setInterval(350)
        self._deb.timeout.connect(self.reload)
        self.reload()
        self._loading = False
        self.done.connect(self._on_done)

    # ---------- 界面
    def _build(self):
        v = QVBoxLayout(self)
        v.setSpacing(10)

        box = QGroupBox("应用清单")
        bv = QVBoxLayout(box)

        # ---- 筛选条：系统进程一大堆，不筛没法看自己装了什么
        fb = QHBoxLayout()
        fb.addWidget(QLabel("显示"))
        self.cb_filter = QComboBox()
        self.cb_filter.addItem("全部应用", "all")
        self.cb_filter.addItem("只看我装的", "user")
        self.cb_filter.addItem("只看系统", "system")
        self.cb_filter.currentIndexChanged.connect(lambda _i: self._apply_filter())
        fb.addWidget(self.cb_filter)
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("搜应用名、进程名或窗口标题…")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(lambda _t: self._apply_filter())
        fb.addWidget(self.ed_search, 1)
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color:#777;")
        fb.addWidget(self.lbl_count)
        bv.addLayout(fb)

        try:
            self.table = QTableWidget(0, len(self.COLS))
            self.table.setHorizontalHeaderLabels(self.COLS)
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.table.setAlternatingRowColors(True)
            self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
            hh = self.table.horizontalHeader()
            hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            for i in (2, 3, 4):
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
            hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(5, 96)
            # 说明列按内容自适应（最长也就「系统核心，锁定」几个字），
            # 省下的宽度全给「窗口标题」—— 那列才是用来认东西的。
            hh.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
            self.table.setMinimumHeight(300)
            bv.addWidget(self.table)
        except Exception as exc:      # noqa: BLE001  表坏了不能拖垮整页
            self.table = None
            print("[clean] 表格没建起来:", exc)
            bv.addWidget(hint(f"应用表没建起来：{exc}"))

        hb = QHBoxLayout()
        self.btn_scan = QPushButton("重新扫描")
        self.btn_scan.clicked.connect(self.reload)
        hb.addWidget(self.btn_scan)
        self.btn_run = QPushButton("立即释放一次")
        self.btn_run.setObjectName("primary")
        self.btn_run.clicked.connect(self._run_once)
        hb.addWidget(self.btn_run)
        self.lbl = QLabel("")
        self.lbl.setStyleSheet("color:#777;")
        hb.addWidget(self.lbl, 1)
        bv.addLayout(hb)
        v.addWidget(box)

        try:
            ob = QGroupBox("开关")
            of = QFormLayout(ob)
            self.ck_core = QCheckBox(
                "保护核心进程（系统 / 杀软 / 输入法 / 大肥鱼 / WorkBuddy 一律不碰）")
            self.ck_core.stateChanged.connect(lambda _s: self._save_switches())
            of.addRow(self.ck_core)
            self.ck_restart = QCheckBox(
                "杀完几秒又自己回来的应用，自动改成「保护」，以后不再白折腾")
            self.ck_restart.stateChanged.connect(lambda _s: self._save_switches())
            of.addRow(self.ck_restart)
            self.sp_min = QSpinBox()
            self.sp_min.setRange(0, 8192)
            self.sp_min.setSingleStep(10)
            self.sp_min.setSuffix(" MB")
            self.sp_min.valueChanged.connect(lambda _v: self._save_switches())
            of.addRow("内存门槛", self.sp_min)
            of.addRow("", hint(
                "「类别」是机器判的，不是名单：跑在系统账户下、exe 在 C:\\Windows 里、"
                "或者路径读不到的，都算「系统」，整行会灰掉。"
                "分不清某个应用是什么，就看「窗口标题」那列——那是它现在开着什么窗口。"))
            of.addRow("", hint(
                "整组内存低于门槛就不折腾。"
                "「只关窗」= 只把窗口关掉，进程全留着，适合豆包这种关了窗口还有用的。"
                "「可清理」= 先关窗口，再结束剩下挂着的进程（关不掉的不硬杀）。"))
            v.addWidget(ob)
        except Exception as exc:      # noqa: BLE001
            self.ck_core = self.ck_restart = self.sp_min = None
            print("[clean] 开关没建起来:", exc)
            v.addWidget(hint(f"开关没建起来：{exc}"))

        v.addStretch(1)

    # ---------- 数据
    def reload(self):
        mod = fresh_module("cleaner",
                           # window_info / fresh 是「按应用分组 + 窗口标题」之后
                           # 才有的：旧版 cleaner 没这两个，列到这里才会被换成新版，
                           # 否则面板会拿着旧模块渲染新表格，titles 一取就炸。
                           ("scan_apps", "set_mode", "run", "say_line",
                            "window_info", "fresh"))
        if mod is None:
            if self.table is not None:
                self.table.setRowCount(0)
            self.lbl.setText("cleaner.py 不可用，这张表先空着")
            return
        self._mod = mod

        try:
            cfg = mod.load_cfg()
            self._loading = True
            if self.ck_core is not None:
                self.ck_core.setChecked(bool(cfg.get("protect_core", True)))
            if self.ck_restart is not None:
                self.ck_restart.setChecked(bool(cfg.get("respect_auto_restart", True)))
            if self.sp_min is not None:
                self.sp_min.setValue(int(cfg.get("min_mb") or 40))
        except Exception as exc:      # noqa: BLE001
            print("[clean] 开关填值失败:", exc)
        finally:
            self._loading = False

        try:
            apps = mod.scan_apps()
        except Exception as exc:      # noqa: BLE001
            apps = []
            self.lbl.setText(f"扫描失败：{exc}")
        self._fill(apps)

    def _fill(self, apps):
        self._apps = list(apps)
        self._apply_filter()

    def _apply_filter(self):
        """按筛选条重画表格。只动显示，不重新扫描 —— 切筛选要立刻响应。"""
        if self.table is None:
            return
        apps = [a for a in getattr(self, "_apps", []) if a["mb"] >= 1]
        key = self.cb_filter.currentData() if self.cb_filter else "all"
        q = (self.ed_search.text() or "").strip().lower() if self.ed_search else ""

        rows = []
        for a in apps:
            if key == "user" and a.get("system"):
                continue
            if key == "system" and not a.get("system"):
                continue
            if q:
                hay = " ".join([str(a["label"]), str(a["id"]),
                                " ".join(a.get("titles") or []),
                                " ".join(a.get("names") or {})]).lower()
                if q not in hay:
                    continue
            rows.append(a)

        if self.lbl_count is not None:
            n_sys = sum(1 for a in apps if a.get("system"))
            # 这行在 880px 面板里只有一百来像素，写长了会被裁掉
            if len(rows) == len(apps):
                self.lbl_count.setText(f"共 {len(apps)} 个 · 系统 {n_sys}")
            else:
                self.lbl_count.setText(f"{len(rows)}/{len(apps)} · 系统 {n_sys}")
        self._render(rows)

    def _render(self, rows):
        t = self.table
        t.setRowCount(0)
        t.setRowCount(len(rows))
        gray = QColor("#8a8a8a")
        for i, a in enumerate(rows):
            titles = a.get("titles") or []
            # 应用名：系统级前面加个记号，一眼分得开
            it = QTableWidgetItem(a["label"])
            it.setData(Qt.ItemDataRole.UserRole, a["id"])
            tip = "\n".join(a["dirs"]) or "（读不到安装路径）"
            tip += "\n\n进程：" + "、".join(sorted(a["names"] or {}))
            it.setToolTip(tip)
            if a.get("system"):
                it.setForeground(gray)
            t.setItem(i, 0, it)

            # 窗口标题：有多个窗口时显示「第一条 +N」，全部标题放 tooltip
            if titles:
                head = titles[0] if len(titles) == 1 else f"{titles[0]}  +{len(titles) - 1}"
            else:
                head = "（无窗口）"
            wt = QTableWidgetItem(head)
            wt.setToolTip("\n".join(titles) if titles else "这个应用现在没有可见窗口")
            if a.get("system"):
                wt.setForeground(gray)
            t.setItem(i, 1, wt)

            for col, text, align in (
                    (2, f"{a['mb']:.0f} MB", Qt.AlignmentFlag.AlignRight),
                    (3, str(len(a["pids"])), Qt.AlignmentFlag.AlignRight),
                    (4, "系统" if a.get("system") else "我装的",
                     Qt.AlignmentFlag.AlignHCenter)):
                cell = QTableWidgetItem(text)
                cell.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                if a.get("system"):
                    cell.setForeground(gray)
                t.setItem(i, col, cell)

            combo = QComboBox()
            for mode, text in self.MODES:
                combo.addItem(text, mode)
            combo.blockSignals(True)
            idx = combo.findData(a["mode"])
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
            if a["locked"]:
                combo.setEnabled(False)
                combo.setToolTip("系统核心，锁定。只允许「保护」，连关窗都不做。")
            combo.currentIndexChanged.connect(
                lambda _i, c=combo, aid=a["id"]: self._on_mode(aid, c))
            t.setCellWidget(i, 5, combo)

            why = QTableWidgetItem(a["why"])
            if a.get("system"):
                why.setForeground(gray)
            t.setItem(i, 6, why)

    # ---------- 交互
    def _on_mode(self, app_id, combo):
        if self._mod is None:
            return
        ok, msg = self._mod.set_mode(app_id, combo.currentData())
        self.lbl.setText(msg)
        self.lbl.setStyleSheet("color:#2e7d32;" if ok else "color:#c0392b;")
        # 不能在 combo 自己的回调里把这张表重建掉（widget 正在处理信号，
        # 删掉它就是 use-after-free），挪到下一轮事件循环。
        QTimer.singleShot(0, self.reload)

    def _save_switches(self):
        if self._loading or self._mod is None:
            return
        try:
            cfg = self._mod.load_cfg()
            if self.ck_core is not None:
                cfg["protect_core"] = self.ck_core.isChecked()
            if self.ck_restart is not None:
                cfg["respect_auto_restart"] = self.ck_restart.isChecked()
            if self.sp_min is not None:
                cfg["min_mb"] = int(self.sp_min.value())
            self._mod.save_cfg(cfg)
            self._deb.start()
        except Exception as exc:      # noqa: BLE001
            self.lbl.setText(f"开关没存上：{exc}")

    def _run_once(self):
        if self._busy or self._mod is None:
            return
        self._busy = True
        self.btn_run.setEnabled(False)
        self.btn_run.setText("正在释放…")
        self.lbl.setText("正在处理…")
        self.lbl.setStyleSheet("color:#777;")
        mod = self._mod

        def work():
            try:
                line = mod.say_line(mod.run())
            except Exception as exc:      # noqa: BLE001
                line = f"释放没干成：{exc}"
            self.done.emit(line)

        threading.Thread(target=work, name="panel-clean", daemon=True).start()

    def _on_done(self, line):
        self._busy = False
        self.btn_run.setEnabled(True)
        self.btn_run.setText("立即释放一次")
        self.lbl.setText(line)
        self.lbl.setStyleSheet("color:#444;")
        if self.pet is not None:
            try:
                self.pet.say(line, force=True, speak=False, layer="system")
            except Exception:      # noqa: BLE001
                pass
        self.reload()

    def save(self):
        """底部「保存」也会走到这里，把三个开关落盘。"""
        if self._mod is None:
            return False, "cleaner.py 不在，开关没保存"
        try:
            cfg = self._mod.load_cfg()
            if self.ck_core is not None:
                cfg["protect_core"] = self.ck_core.isChecked()
            if self.ck_restart is not None:
                cfg["respect_auto_restart"] = self.ck_restart.isChecked()
            if self.sp_min is not None:
                cfg["min_mb"] = int(self.sp_min.value())
            ok = self._mod.save_cfg(cfg)
            return (True, "已保存") if ok else (False, "写文件失败")
        except Exception as exc:      # noqa: BLE001
            return False, f"保存出错：{exc}"


# ================================================================ 报时页
class ChimeTab(QWidget):
    """整点报时：开关 / 时段 / 念不念 / 台词。

    报时是个**会主动出声**的功能，这一页的重点因此不是「功能多」，而是
    「能立刻关掉」：一个总开关，两个小时数管住半夜别响，一个台词框管住别听腻。
    """

    def __init__(self, pet=None):
        super().__init__()
        self.pet = pet
        self.cfg = read_json(CONFIG_PATH, {})
        self._build()
        self._fill()

    def _wrap(self, layout):
        w = QWidget()
        w.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return w

    def _chime(self):
        """热加载 chime 模块：改了台词/逻辑不用重启桌宠。"""
        return fresh_module("chime", ("preview", "line_for", "should_chime",
                                      "in_window", "cn_hour"))

    def _build(self):
        v = QVBoxLayout(self)
        v.setSpacing(10)

        g = QGroupBox("整点报时")
        f = QFormLayout(g)
        self.on = QCheckBox("启用：每到整点说一句")
        f.addRow(self.on)

        row = QHBoxLayout()
        self.h0, self.h1 = QSpinBox(), QSpinBox()
        for s in (self.h0, self.h1):
            s.setRange(0, 23)
            s.setSuffix(" 点")
            s.setFixedWidth(84)
        row.addWidget(QLabel("从"))
        row.addWidget(self.h0)
        row.addWidget(QLabel("到"))
        row.addWidget(self.h1)
        row.addStretch(1)
        f.addRow("报时时段", self._wrap(row))
        f.addRow("", hint(
            "起止两头都算。填一样的（比如 8 到 8）就是全天都报；"
            "22 到 6 这种跨午夜的也认。"))

        self.speak = QCheckBox("念出来（关掉就只弹字，不出声）")
        f.addRow(self.speak)

        self.lines = QPlainTextEdit()
        self.lines.setPlaceholderText(
            "一行一句，留空就用内置那套。\n"
            "{time} 会换成「九点」「十四点」这种说法，"
            "比如：{time}整，该动了。")
        self.lines.setFixedHeight(96)
        f.addRow("自定义台词", self.lines)

        row_p = QHBoxLayout()
        self.btn_try = QPushButton("试一句")
        self.btn_try.setFixedWidth(80)
        self.btn_try.clicked.connect(self._preview)
        self.preview_lbl = QLabel("")
        self.preview_lbl.setWordWrap(True)
        self.preview_lbl.setStyleSheet("color:#555;")
        row_p.addWidget(self.btn_try)
        row_p.addWidget(self.preview_lbl, 1)
        f.addRow("", self._wrap(row_p))
        v.addWidget(g)

        v.addWidget(hint(
            "报时走大肥鱼正常的说话出口：开了「一键静音」就只弹字不念，"
            "关了气泡就一个字都不弹。它睡着的时候不报，拖来拖去的时候也不报。"))
        v.addStretch(1)

    def _fill(self):
        c = self.cfg
        self.on.setChecked(bool(c.get("chime_on", True)))
        self.h0.setValue(int(c.get("chime_from", 8)
                             if c.get("chime_from") is not None else 8))
        self.h1.setValue(int(c.get("chime_to", 22)
                             if c.get("chime_to") is not None else 22))
        self.speak.setChecked(bool(c.get("chime_speak", True)))
        self.lines.setPlainText(str(c.get("chime_lines") or ""))

    def _patch(self):
        """界面上现在的值。试一句和保存都读它，不会出现「试的和存的不一样」。"""
        return {
            "chime_on": self.on.isChecked(),
            "chime_from": int(self.h0.value()),
            "chime_to": int(self.h1.value()),
            "chime_speak": self.speak.isChecked(),
            "chime_lines": self.lines.toPlainText().strip(),
        }

    def _preview(self):
        mod = self._chime()
        if mod is None:
            self.preview_lbl.setText("chime.py 不在，报时功能用不了")
            return
        try:
            line = mod.preview(self._patch())
        except Exception as e:      # noqa: BLE001
            self.preview_lbl.setText(f"生成失败：{e}")
            return
        self.preview_lbl.setText(line)
        if self.pet is not None:
            try:
                self.pet.say(line, force=True, speak=self.speak.isChecked())
            except Exception as e:  # noqa: BLE001
                print("[chime] 试一句没说出来:", e)

    def save(self):
        patch = self._patch()
        if self.pet is not None:
            try:
                self.pet.apply_settings(patch)
            except Exception as e:  # noqa: BLE001
                return False, f"应用失败：{e}"
        else:
            cfg = read_json(CONFIG_PATH, {})
            cfg.update(patch)
            try:
                write_json(CONFIG_PATH, cfg)
            except Exception as e:  # noqa: BLE001
                return False, f"配置没写进去：{e}"
        return True, "整点报时已保存，立刻生效"


# ================================================================ 主窗口
class SettingsPanel(QDialog):
    def __init__(self, pet=None, parent=None):
        super().__init__(parent)
        self.pet = pet
        self.setWindowTitle("大肥鱼 · 设置")
        # 以前钉死 820×800，可内容实际要 868×1369：窗口比屏幕还高，
        # 居中一放，标题栏直接跑到屏幕上方外面，既被遮住又没处可拖。
        # 现在只兜底下限，实际尺寸交给 fit_to_screen() 按屏幕可用区域算。
        self.setMinimumSize(760, 460)
        self.setStyleSheet(QSS)
        f = QFont(UI_FONT, 10)
        self.setFont(f)

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 12)
        v.setSpacing(10)

        self.tabs = QTabWidget()
        self.persona = PersonaTab()
        self.runtime = RuntimeTab(pet)
        self.chime = ChimeTab(pet)
        self.notify = NotifyTab(pet)
        self.clean = CleanTab(pet)
        self.portable = PortableTab(pet)
        # 每个页签套一层滚动区：窗口高度被屏幕压小以后，内容不能被裁掉，
        # 而是竖着滚。以前没这层，矮屏上底部的保存/关闭按钮直接被推出窗外。
        for w, title in ((self.persona, "人设"), (self.runtime, "运行"),
                         (self.chime, "报时"), (self.notify, "通知"),
                         (self.clean, "资源释放"), (self.portable, "移机")):
            self.tabs.addTab(self._scrolled(w), title)
        v.addWidget(self.tabs, 1)

        hb = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("color:#777;")
        hb.addWidget(self.status, 1)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.close)
        b_save = QPushButton("保存")
        b_save.setObjectName("primary")
        b_save.clicked.connect(self.save_all)
        hb.addWidget(b_close)
        hb.addWidget(b_save)
        v.addLayout(hb)

        self.tabs.currentChanged.connect(self._tab_changed)

    def _scrolled(self, w):
        """把页签内容包进滚动区，屏幕矮时也能看到底部。"""
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setWidget(w)
        return sa

    def _inner(self, i):
        """第 i 个页签真正的内容控件（外面套了滚动区）。"""
        w = self.tabs.widget(i)
        if isinstance(w, QScrollArea) and w.widget() is not None:
            return w.widget()
        return w

    def fit_to_screen(self):
        """按屏幕可用区域定尺寸和位置：不许高过屏幕，不许跑到标题栏够不着。

        以桌宠所在的那块屏为准（多屏时它可能不在主屏）。
        """
        scr = None
        try:
            if self.pet is not None:
                scr = self.pet.screen()
        except Exception:
            scr = None
        scr = scr or QApplication.primaryScreen()
        avail = scr.availableGeometry()
        # 套了滚动区之后 sizeHint 会缩成很小的一块，不能拿它当尺寸，
        # 否则面板会缩成巴掌大。给一个舒服的目标尺寸，再拿屏幕可用区域打顶。
        w = min(880, avail.width() - 48)
        h = min(860, avail.height() - 48)
        w = max(560, w)
        h = max(420, h)
        self.resize(w, h)
        x = avail.x() + (avail.width() - w) // 2
        y = avail.y() + (avail.height() - h) // 2
        self.move(max(avail.x(), x), max(avail.y(), y))

    def _tab_changed(self, i):
        cur = self._inner(i)
        if cur is self.portable:          # 切到移机页自动体检
            self.portable.check()
        elif cur is self.notify:          # 切到通知页刷一次状态
            self.notify.refresh_status()

    def save_all(self):
        """保存当前页签 + 人设（人设永远一起存，避免改了忘存）。"""
        msgs = []
        ok1, m1 = self.persona.save()
        msgs.append(("人设", ok1, m1))
        ok2, m2 = self.runtime.save()
        msgs.append(("运行", ok2, m2))
        okc, mc = self.chime.save()
        msgs.append(("报时", okc, mc))
        ok3, m3 = self.notify.save()
        msgs.append(("通知", ok3, m3))
        ok4, m4 = self.clean.save()
        msgs.append(("资源释放", ok4, m4))

        text = "　|　".join(f"{n}：{m}" for n, _ok, m in msgs)
        bad = [n for n, ok, _ in msgs if not ok]
        self.status.setText(text)
        if bad:
            self.status.setStyleSheet("color:#c0392b;")
            QMessageBox.warning(self, "有项目没保存成功",
                                "\n".join(f"{n}：{m}" for n, _ok, m in msgs))
        else:
            self.status.setStyleSheet("color:#2e7d32;")
            if self.pet is not None:
                try:
                    self.pet.say("设置改好了，我按这个来", force=True, speak=False,
                                 layer="system")
                except Exception:
                    pass


def open_panel(pet=None, parent=None):
    """给 pet.py 用的入口：拿住同一个实例，别开一堆窗口。"""
    dlg = SettingsPanel(pet=pet, parent=parent)
    dlg.fit_to_screen()      # 大小和位置都得先塞进屏幕，否则标题栏可能在屏幕外
    dlg.exec()
    return dlg
