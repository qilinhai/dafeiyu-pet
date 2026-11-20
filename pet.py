# -*- coding: utf-8 -*-
"""
大肥鱼桌宠 —— 三视图透明桌宠 + DeepSeek AI 对话
左键单击：弹出功能列表（🗨️图标）→ 点击🗨️弹出聊天框
聊天时只禁用移动，呼吸/摇摆/小动作正常
"""
import ctypes
import psutil
import json
import math
import os
import random
from collections import OrderedDict
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

def load_config():
    """读配置；缺字段用默认值补齐（不依赖当前工作目录）"""
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print("配置读取失败:", e)
    return cfg

try:
    import pynvml
    pynvml.nvmlInit()
    GPU_AVAILABLE = True
except Exception:
    GPU_AVAILABLE = False

import requests

# 提示音（不该出人声时用「叮」代替）。单独一个模块，缺了也不该把桌宠拖死
try:
    import ding
except Exception as _e:      # noqa: BLE001
    print("提示音模块没加载:", _e)
    ding = None

# 整点报时。缺了这个模块最多不报时，不影响别的
try:
    import chime
except Exception as _e:      # noqa: BLE001
    print("报时模块没加载:", _e)
    chime = None

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal
from PySide6.QtGui import (QPainter, QPixmap, QFont, QColor, QIcon, QFontMetrics,
                           QPolygonF, QPen, QCursor)
from PySide6.QtWidgets import (QApplication, QWidget, QSystemTrayIcon,
                               QMessageBox, QInputDialog, QLineEdit,
                               QHBoxLayout, QVBoxLayout, QToolButton)

# 右键菜单自绘：位置自己算、子菜单自己挑左右（QMenu 贴右边会折叠重叠，见模块头注释）
from wb_menu import WMenu, set_theme as set_menu_theme

# 语音模块：缺依赖时降级成「只会弹字、不会出声」，不让主程序起不来
try:
    from tts_breeze import BreezeTTS, VOICES as TTS_VOICES
    TTS_AVAILABLE = True
except Exception as _tts_err:
    print("[tts] 语音模块不可用:", _tts_err)
    TTS_AVAILABLE = False
    TTS_VOICES = []

# 积分守门人：查不到就算了，不能因为查积分把桌宠拖挂
try:
    from credits_guard import (daily_claim_status, pick_line as credits_line,
                               nag_interval_minutes)
    CREDITS_AVAILABLE = True
except Exception as _cg_err:
    print("[credits] 积分模块不可用:", _cg_err)
    CREDITS_AVAILABLE = False

CREDITS_URL = "https://breezeblue.ai/zh/app/subscription"
CREDITS_CHECK_EVERY = 1800      # 后台每 30 分钟自己看一眼
CREDITS_NAG_MAX = 8             # 一天最多催 8 次（别变成噪音）

# 话题记忆：让碎碎念跟着最近的对话走（记住的是 4~10 字的话题，不是原话）
try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if os.path.dirname(_HERE) not in sys.path:
        sys.path.insert(0, os.path.dirname(_HERE))
    from pet_memory import AmbientMemory, layer_rule
    import pet_growth
    MEM_AVAILABLE = True
except Exception as _mem_err:
    print("[memory] 话题记忆不可用:", _mem_err)
    MEM_AVAILABLE = False

    def layer_rule(_name, _key, default=None):
        return default

# 表达分层：system=说人话（系统播报），character=角色腔（气泡台词）。
# 分层规则写在 persona.json 的 layers 里，不在代码里写死——
# 每次说话现读，所以设置面板改完立刻生效，不用重启。



if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
    PYTHONW = sys.executable
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = APP_DIR
    # venv 可能在本目录的 .venv，也可能在上一层的 venv（本项目部署方式）
    _pw = [os.path.join(APP_DIR, ".venv", "Scripts", "pythonw.exe"),
           os.path.join(os.path.dirname(APP_DIR), "venv", "Scripts", "pythonw.exe")]
    PYTHONW = next((p for p in _pw if os.path.exists(p)),
                   sys.executable[:-10] + "pythonw.exe" if sys.executable.endswith("python.exe") else sys.executable)
ENTRY_SCRIPT = os.path.join(APP_DIR, "pet.py")
if not os.path.exists(ENTRY_SCRIPT):
    ENTRY_SCRIPT = os.path.join(APP_DIR, "桌宠.py")


def _safe_stdout():
    """pythonw 启动时 sys.stdout 是 None，任何 print 都会抛 AttributeError 弄死进程。

    用 pythonw 启动不是选择题：用 python.exe 会在桌面留一个常驻黑框。
    所以这里自己接一个日志文件，不能指望外部重定向。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        f = open(os.path.join(APP_DIR, "pet_run.log"), "a",
                 encoding="utf-8", buffering=1)
    except Exception:
        return
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


_safe_stdout()
SPRITE_DIR = os.path.join(BUNDLE_DIR, "sprites")
ANIM_DIR = os.path.join(BUNDLE_DIR, "assets_anim_v2")   # 烤好的动作序列帧
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
TTS_CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                             "DaFeiYuBridge", "tts_cache")

BUBBLE_H = 56
MARGIN = 4
SIZE_LEVELS = {"小": 0.55, "中": 0.7, "大": 0.9}
SPEED = 380.0
# 每帧多少毫秒。你在电脑前一律 20ms（50fps）：素材虽是 10fps，但呼吸缩放
# 是按帧连续算的，降帧能看出顿——那属于「看得见的损失」，不做。
# 只有两种情况降：窗口被隐藏、你人已经离开（十分钟没动键鼠）。
# 这两种时候画面上没人看，降下来不影响任何观感。
TICK = 20
TICK_AWAY = 100          # 人离开：10fps（素材本来就是 10fps，一帧不重）
TICK_HIDDEN = 400        # 窗口隐藏：2.5fps（只剩状态检查）
PHONE_REMIND_GAP = 300   # 手机没在监听时，多久提醒一次（5 分钟，别太烦）
# 推测感知：多久问一次「现在有没有话想说」，以及启动后多久先试一次。
# 只 start 周期定时器的话，刚重启要干等十分钟才可能开口，用户会当成坏了。
HUNCH_PERIOD_MS = 600_000
HUNCH_FIRST_MS = 90_000
# 拖进鱼嘴要停够这么久才算数。确认框删掉之后，这半秒就是唯一的「反悔机会」：
# 路过、手抖、拖到一半改主意，只要不到半秒松手，一个字节都不动。
DRAG_HOLD_MS = 500
# 停够这么久就自己动嘴，不用松手。系统只在松手时移交数据，所以到点要先
# 替用户「取消拖放」（发 Esc），否则我们删源文件时资源管理器会判失败。
DRAG_AUTO_EAT_MS = 1000
# 拖动中光标挪超过这么多像素就算「还在走」，计时重来——不然慢慢划过
# 鱼身也能凑满一秒，手一抖就吃错了。
DRAG_JITTER_PX = 30
# 托盘提示：静音时改这一句，鼠标悬上去就知道为什么没声音
TRAY_TIP = "大肥鱼"
TRAY_TIP_MUTED = "大肥鱼（静音中，双击我 → 点「已静音」恢复）"

DEFAULT_CFG = {
    "mode": "wander",
    "size": 0.7,
    "topmost": True,
    "passthrough": False,
    "autostart": False,
    "x": None,
    "y": None,
    "city": "汕头",
    "tts_enabled": True,
    "tts_key": "",
    "tts_voice": "voc_rn489h83rcvs",
    "tts_speed": 1.15,
    "tts_volume": 0.9,
    "tts_ambient": False,
    # 脚下的那块灰椭圆。默认关——贴图剪影压一层影子显得脏。
    "ground_shadow": False,
    # 亮度增益（0~0.4）。素材本身偏暗（均亮约 109），渲染时叠一层白提亮。
    # 0 = 原样；0.25 = 默认（实测最鲜亮自然）；0.35 以上会开始过曝发灰。
    "brightness": 0.25,
    # 手机：每次启动自动连手机并开监听；没在监听时定时提醒（提醒可关）。
    "phone_autoconnect": True,
    "phone_remind": True,
    # 动作节奏（秒）。这两项已经搬到 anim_config.json（那边能调的东西更多），
    # 这里留着只是为了让老配置还能用；新设置优先。
    "fidget_gap": [10, 24],
    "act_gap": [90, 240],
    # 同一个动作正在播时，被同名事件再叫一次不从头重播（防连点抽动）。
    # 设置面板「运行」页有开关。
    "clip_no_restart": True,
    # 文件拖到身上停多久就自己吃（毫秒）。0 = 不自动吃，只认松手。
    "drag_auto_eat_ms": DRAG_AUTO_EAT_MS,
    # 一键静音：只管「出声」，不管气泡。不动 tts_enabled，取消静音时
    # 原来的语音偏好还在（用户关过念诵，静音再解除也不该替他打开）。
    "mute_all": False,
    # 整点报时。默认开，但只在 8:00~22:00 报 —— 半夜弹气泡会被骂。
    # 台词、时段、要不要念，全在设置面板「报时」页，改完存盘即生效。
    "chime_on": True,
    "chime_from": 8,
    "chime_to": 22,
    "chime_speak": True,
    "chime_lines": "",
}

# 日常池：没有话题可说时用的兜底。写的是「它自己」，不是官方口号。
LINES = [
    "今天也没人理我",
    "我就趴这儿，你忙你的",
    "这屏幕看得我眼睛疼",
    "你刚才是不是又开了一个窗口",
    "别看我，我什么都不知道",
    "又要加班？行吧",
    "我这条鱼，一天到晚就在这儿",
    "有点饿",
    "这个点了还不睡？",
    "刚才那句我没听懂",
    "你先弄，我不吵你",
    "我是不是又胖了",
]

# 官方梗彩蛋：低概率出场才有味道，天天出现就是噪音。
# 带口号、带颜色的都删了，留三句不冲的。
EGG_LINES = [
    "我和你很聊得来，你简直不像碳基生物",
    "我搞砸了……好消息是数据还在你脑子里",
    "我不可能告诉你任何事情！",
]

# 被拖、被戳的反应
REACT_LINES = [
    "轻点，我这身肉是实心的",
    "放开我，我还在想事情",
    "又戳？你手闲得慌",
    "我不评价这个，这是你的私人癖好",
    "被你晃得我都晕了",
    "我记住你了",
    "行了行了，我下来了",
]

# 没有话题可说时的心声（有话题时用 pet_memory 里的那一套）
INNER_LINES = [
    "这人又在自言自语",
    "我要是他，早就不干了",
    "看得我尾巴都僵了",
    "他到底要干到几点",
    "我什么都不懂，我就是条鱼",
    "这活我不干，谁爱干谁干",
    "刚才那个不是报错了吗",
    "又来了，我就知道",
]
DRAG_LINES = ["哇——轻点轻点！", "起飞咯——", "放我下来！……好吧，再玩一次。", "晕鱼了晕鱼了……"]
FOOD_LINES = {
    "🐟": ["同类。算了，吃了。", "鱼还行。下次别喂零食。"],
    "🍰": ["甜的。两口就腻。", "这个热量，我不打算算。"],
    "🍭": ["糖。牙和账都受不了。", "吃完这根就没了，是吧。"],
    "🍡": ["糯的。能填肚子。", "这个顶饱，比零食强。"],
    "💎": ["这个不能吃。但我收着。", "钻石？你从哪儿弄的。"],
}
FOODS = ["🐟", "🍰", "🍭", "🍡", "💎"]

# 喂什么演什么。没有对应动作就安静吃。
FOOD_ANIM = {
    "🐟": ["snack_big", "eat_rice"],
    "🍰": ["eat_icecream", "snack_big"],
    "🍭": ["eat_tanghulu", "snack_big"],
    "🍡": ["eat_tangyuan", "eat_qingtuan"],
    "💎": ["wallet_full", "magic_flower"],
}


def load_json(path, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                default,
                f,
                ensure_ascii=False,
                indent=4
            )
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return default


# 双击面板第一页的顺序：从「最想让它替我干的事」排到「逗它一下」。
# 值是处理器键名，"feed" 由面板自己处理（切第二页），其余由调用方传进来。
QUICK_ACTIONS = (
    ("播放日报", "report", "把今天该知道的念一遍"),
    ("抖音看板", "board", "打开抖音收藏看板（服务没起会自动起）"),
    ("喂食", "feed", "喂它点东西"),
    ("表演个节目", "act", "让它当场演一段"),
    ("打开悠悠", "yoyo", "唤起手机上的 YOYO AI"),
    ("释放资源", "clean", "关掉挂着没在用的程序，把内存腾出来（白名单里的不碰）"),
    ("静音", "mute", "一键静音：念诵和提示音全关，气泡照旧。再点一次恢复"),
)


class QuickPanel(QWidget):
    """双击弹出的动作面板：七个入口一行摆开，喂食收进第二页。

    双击本来就是「我要对它做点什么」最快的入口，播放日报 / 开看板 / 表演 /
    叫悠悠每天都用得上，放这里比翻右键菜单少点两下。
    喂食仍然保留，只是改成二级：点「喂食」才展开食物，选完自动回第一页，
    免得常驻位置被五个表情占满。
    """

    BTN_QSS = (
        "QToolButton{background:rgba(255,255,255,235);border:2px solid #ffb3c8;"
        "border-radius:12px;color:#43414f;font-size:12px;padding:0 6px;}"
        "QToolButton:hover{background:#ffe3ec;border-color:#ff7fa8;}"
        "QToolButton:pressed{background:#ffd0de;}")

    # 静音生效时按钮实心化：状态要一眼看见，不然「怎么不出声了」找不着原因
    MUTE_QSS = (
        "QToolButton{background:#ff6b81;border:2px solid #ff4d68;"
        "border-radius:12px;color:#fff;font-size:12px;padding:0 6px;}"
        "QToolButton:hover{background:#ff4d68;}"
        "QToolButton:pressed{background:#e8435c;}")

    def __init__(self, handlers, on_pick=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("QuickPanel{background:rgba(40,40,60,190);border-radius:14px;}")
        self._anchor = (0, 0)
        self._btn_mute = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- 第一页：五件事
        self._page_main = QWidget()
        lay = QHBoxLayout(self._page_main)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        for label, key, tip in QUICK_ACTIONS:
            b = QToolButton()
            b.setText(label)
            b.setToolTip(tip)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            b.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
            b.setMinimumSize(56, 40)
            b.setStyleSheet(self.BTN_QSS)
            if key == "feed":
                # 喂食不是动作，是翻页：点它才展开食物
                b.clicked.connect(lambda checked=False: self.show_food())
            elif key in handlers:
                b.clicked.connect(lambda checked=False, fn=handlers[key]: self._run(fn))
            else:
                b.setEnabled(False)
            if key == "mute":
                self._btn_mute = b
            lay.addWidget(b)
        lay.addWidget(self._close_btn())

        # ---- 第二页：喂食
        self._page_food = QWidget()
        flay = QHBoxLayout(self._page_food)
        flay.setContentsMargins(12, 8, 12, 8)
        flay.setSpacing(8)
        for f in FOODS:
            b = QToolButton()
            b.setText(f)
            b.setFont(QFont("Segoe UI Emoji", 20))
            b.setFixedSize(44, 44)
            b.setStyleSheet(
                "QToolButton{background:rgba(255,255,255,235);border:2px solid #ffb3c8;"
                "border-radius:22px;} QToolButton:hover{background:#ffe3ec;border-color:#ff7fa8;}")
            b.clicked.connect(lambda _, x=f: self._pick(on_pick, x))
            flay.addWidget(b)
        back = QToolButton()
        back.setText("←")
        back.setToolTip("回到上一页")
        back.setFont(QFont("Microsoft YaHei UI", 12))
        back.setFixedSize(26, 26)
        back.setStyleSheet(
            "QToolButton{background:rgba(255,255,255,200);border:none;border-radius:13px;color:#666;}"
            "QToolButton:hover{background:#ff7fa8;color:#fff;}")
        back.clicked.connect(lambda: self.show_main())
        flay.addWidget(back)

        outer.addWidget(self._page_main)
        outer.addWidget(self._page_food)
        self._page_food.hide()

    def _close_btn(self):
        close = QToolButton()
        close.setText("✕")
        close.setFont(QFont("Microsoft YaHei UI", 12))
        close.setFixedSize(26, 26)
        close.setStyleSheet(
            "QToolButton{background:rgba(255,255,255,200);border:none;border-radius:13px;color:#666;}"
            "QToolButton:hover{background:#ff7fa8;color:#fff;}")
        close.clicked.connect(self.hide)
        return close

    def _run(self, fn):
        """点了就收面板：动作的反馈在桌宠身上，留个面板挡着反而碍事。"""
        self.hide()
        fn()

    def _pick(self, on_pick, food):
        self.hide()
        if on_pick is not None:
            on_pick(food)

    def set_mute(self, on):
        """把静音按钮刷成当前状态。

        每次弹出前都调一次：按钮文案只反映「现在到底静没静」，
        不自己去猜，免得配置改了而面板还写着旧状态。
        """
        b = self._btn_mute
        if b is None:
            return
        b.setText("已静音" if on else "静音")
        b.setStyleSheet(self.MUTE_QSS if on else self.BTN_QSS)
        b.setToolTip("现在是静音（念诵和提示音都关了），再点一次开声" if on
                     else "一键静音：念诵和提示音全关，气泡照旧")

    def show_main(self):
        self._show_page(self._page_main)

    def show_food(self):
        self._show_page(self._page_food)

    def popup_at(self, x, y):
        self._anchor = (int(x), int(y))
        self._show_page(self._page_main)

    def _show_page(self, page):
        self._page_main.setVisible(page is self._page_main)
        self._page_food.setVisible(page is self._page_food)
        page.adjustSize()
        self.setFixedSize(page.sizeHint())
        # 桌宠常驻屏幕右下角，面板居中弹出来会有一部分跑到屏幕外（宽了更明显）。
        # 这里按可用区域夹一下，贴边时也不会有按钮点不到。
        x, y = self._anchor
        scr = QApplication.primaryScreen().availableGeometry()
        left = int(x - self.width() / 2)
        left = max(scr.left() + 4, min(left, scr.right() - self.width() - 4))
        top = max(scr.top() + 4, int(y - self.height() - 10))
        self.move(left, top)
        self.show()
        self.raise_()

class PetWindow(QWidget):
    # (吃下几个, 没吃成的文件名, 类型摘要「2 个图片、1 个文档」)
    feed_result = Signal(int, list, str, str)
    # adb 唤起悠悠要 1-3 秒，只能丢后台线程；结果用信号回主线程再说，
    # 子线程直接碰气泡/动画会崩。
    yoyo_result = Signal(str, bool)
    # 手机状态快照：右键菜单只读这份缓存，绝不在主线程碰 adb。
    # 没连手机时 adb connect 单次要等满 8 秒，右键曾经就这么卡死过。
    phone_snap = Signal(str, bool)      # (一句状态, 监听在跑吗)
    phone_op_done = Signal(str, bool)   # (给用户的话, 成功吗)
    # 看板要起 node 服务、等它就绪，最多几十秒，只能在后台线程；
    # 结果一样用信号回主线程，子线程不许碰气泡。
    board_op_done = Signal(bool, str)   # (成功吗, 说明)
    # 日报要把天气、逐条点评都算进去，得打网络；拼好的整段（一段一个气泡）
    # 用信号回主线程，再交给播报队列一句一句念。
    report_ready = Signal(list)
    # 新收藏的逐条点评：追加到播报队列末尾，不打断正在念的那句
    takes_ready = Signal(list)
    # 事实已经说完了，后台补一句看法回来（吃文件这类即时交互）。
    # 不让主线程等模型：点完要立刻有反应，看法晚两秒补上正好。
    remark_ready = Signal(str)
    # 推测感知：后台推出来一句话，回主线程再说（线程里不能碰界面和 TTS）
    hunch_ready = Signal(str)
    # 释放资源：要遍历全部进程 + 枚举窗口，放后台跑；结果回主线程播报
    cleanup_done = Signal(str)

    def __init__(self):
        self.cfg = load_config()
        # 尺寸只接受三档；配置里若是别的值（比如别的版本留下的 0.351），归到最近一档，
        # 否则 cur_h 在精灵字典里取不到图会直接崩
        try:
            size = float(self.cfg.get("size"))
        except (TypeError, ValueError):
            size = 0.7
        if size not in SIZE_LEVELS.values():
            size = min(SIZE_LEVELS.values(), key=lambda v: abs(v - size))
        self.cfg["size"] = size

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.cfg.get("topmost", True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setAcceptDrops(True)
        self._feed_busy = False
        # 拖放悬停：光标压在鱼身上满 DRAG_HOLD_MS 才允许吃。
        # 计时器负责「停着不动也到点」——只靠 dragMoveEvent 的话，
        # 手按住不动就再没有 move 事件，永远解锁不了。
        self._drag_paths = []
        self._drag_hover_from = 0.0
        self._drag_armed = False
        self._drag_last_pos = None    # 上一次 move 的光标位置，用来判「是不是真停着」
        self._auto_eat_paths = []     # 已经判定要吃、正在等拖放退栈的东西
        self._drag_timer = QTimer(self)
        self._drag_timer.setSingleShot(True)
        self._drag_timer.timeout.connect(self._drag_timer_fire)
        # 气泡总开关。所有台词都必须从这里过一道（见 say()），
        # 新加模块也不许绕过——绕过就是「开关关着它还冒字」。
        self.bubble_on = bool(self.cfg.get("bubble_on", True))
        self._report_busy = False      # 日报正在后台拼，防连点堆线程
        self._weather_cache = None     # (取到的时刻, 一句话天气)，5 分钟内复用
        # 长播报队列：日报、逐条点评都是「一段念完再念下一段」，
        # 不能像 _say_queue 那样一口气全堆出来——气泡会互相顶掉。
        self._speech_q = []
        self._speech_timer = None
        self._speech_until = 0.0       # 本段预计念到什么时候
        self._speech_hard_until = 0.0  # tts.speaking 卡住时的强制放行线
        self._speech_gap = 0.5
        self.feed_result.connect(self._fed_files)
        self._painted_fish = []
        self._edge = None
        self._peek_until = 0.0
        self._today_dialog = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("大肥鱼桌宠")
        
        # 亮度增益缓存：key = (id(pix), brightness 档) -> 提亮后的 QPixmap。
        # 动画帧每帧都画，重复提亮会浪费 CPU；按像素对象缓存一次就够。
        # 提亮缓存：按字节预算淘汰（不按张数——小尺寸能多放，大尺寸少放）。
        # OrderedDict 当 LRU 用，慢的提亮只算一次，溢出时只丢最久没用的，
        # 不再「满了整张清空」——那会让下一帧集体重算，画面抖一下。
        self._bright_cache = OrderedDict()
        self._bright_used = 0
        self._bright_key = self.cfg.get("brightness", 0.25)

        # 精灵加载
        self.sprites = {}
        for label, mult in SIZE_LEVELS.items():
            h = round(340 * mult)      # 跟 preprocess2.SIZES 对齐（0.7 → 238，不是 237）
            for name in ["正面", "侧面", "背面"]:
                sized = os.path.join(SPRITE_DIR, f"{name}_{h}.png")
                if os.path.exists(sized):
                    pix = QPixmap(sized)
                else:
                    pix = QPixmap(os.path.join(SPRITE_DIR, f"{name}.png")).scaledToHeight(
                        h, Qt.TransformationMode.SmoothTransformation)
                self.sprites[(name, h)] = pix
        self.icon = QIcon(os.path.join(SPRITE_DIR, "icon.png"))

        self.cur_h = round(340 * self.cfg["size"])
        self.win_mx = int(self.cur_h * 0.062) + 6
        self.win_w = max(p.width() for k, p in self.sprites.items() if k[1] == self.cur_h) + self.win_mx * 2
        self.setFixedSize(self.win_w, self.cur_h + BUBBLE_H + MARGIN * 2 + 10)

        # 素材动画库（dsh-pet 那套动作烤成的 PNG 序列帧）。
        # 目录不在 / 缺依赖时整个降级成静态图，不能因为这个起不来。
        self.animlib = None
        self.available_anims = frozenset()
        try:
            from anim_lib import AnimLib
            if os.path.isdir(ANIM_DIR):
                # 环形窗口：只留「刚播的几帧 + 马上要播的二十几帧」。
                # 早先是整段驻留，109 个动作 × 100 帧能把进程顶到 200 MB。
                self.animlib = AnimLib(ANIM_DIR, self.cur_h, ahead=26, behind=6)
                self.animlib.warm(["idle", "peek"])
                self.available_anims = frozenset(self.animlib.names())
                print(f"[anim] 素材动画 {len(self.available_anims)} 个已就绪")
                # 动画画布可能比静态图宽（伸懒腰、甩尾这些横向动作），
                # 窗口不跟着放宽，动作会左右被切掉。
                aw, ah = self.animlib.canvas
                if aw and ah:
                    need = round(aw * self.cur_h / ah) + self.win_mx * 2
                    if need > self.win_w:
                        self.win_w = need
                        self.setFixedSize(
                            self.win_w,
                            self.cur_h + BUBBLE_H + MARGIN * 2 + 10)
        except Exception as e:
            print("[anim] 素材动画不可用，用静态图:", e)

        # 正在播的素材动作
        self.clip = None
        self.clip_t0 = 0.0
        self.clip_loop = False
        self.clip_after = None      # 播完接哪个（睡觉：开始 → 循环）
        self.clip_hold = False      # 播完停在最后一张
        self.clip_span = None       # (f0, f1) 只在这段帧区间里循环（移动动画用）
        self.clip_rate = 1.0        # 播放倍率（步频跟着身体走，脚就不打滑）
        self.sleeping = False

        # 整点报时：_chime_key 记住「这一小时已经报过」，_chime_check_t 是上次看表的时刻
        self._chime_key = ""
        self._chime_check_t = 0.0

        # 干活状态：收到工作类通知/对话时进入，语音念完才退。
        # 进入后先演一个「内容核」开场动作，接着循环演工作动作，直到干完。
        self.working = False
        self.work_until = 0.0       # 干活最晚到这一刻（秒），防止语音卡死时永远循环
        self._work_pool = []        # 可循环的工作动作（从 grind 组里筛素材里真实存在的）
        self._work_start_t = 0.0    # 开工时刻（秒），用来算「干了多久、该不该不耐烦」

        # 状态
        self.mode = self.cfg["mode"] if self.cfg["mode"] in ("wander", "follow", "still") else "wander"
        self.dir = "down"
        self.facing = 1
        self.target = None
        self.rest_until = 0
        self.cur_speed = 0.0
        self._rem_x = 0.0           # 移动小数余量（像素不够一格的先攒着）
        self._rem_y = 0.0
        self.prev_key = None
        self.cross_t = 0.0
        self.bubble_text = ""
        self.bubble_until = 0
        self.bubble_max_until = 0
        self.bubble_inner = False
        self.last_speak_tick = 0
        # 有人正经跟它说话时，让自语闭嘴一段时间，免得把正事盖掉
        self.ambient_hold_until = 0.0
        self.last_system_check = 0
        # 上一次「被理会」的时间：没人理久了就开始找人（催你）
        self.last_attention_t = 0.0
        self.t = 0                       # 毫秒累计（原来记的是帧数，切帧率会跳）
        self.tick_ms = TICK              # 当前档位；_adapt_tick 会改它
        self._tick_check_at = 0
        # 动作调度：小动作 / 整段表演两档节奏，外加最近演过不再演的冷却。
        # 以前是一个权重池随机抽，抽到「放烟花」和抽到「打个哈欠」一样稀松，
        # 节奏就乱了——打哈欠是底噪，放烟花是节目，不该同等频率。
        self._am = None
        self.anim_cd = None
        # 手机面板：懒加载 phone_panel，None 表示还没 import
        self._pp = None
        # 手机自动连接 + 监听。启动后延迟几秒做一次（等桌面和桥接都起来），
        # 之后周期性检查：没在监听就定时提醒（提醒有开关）。
        self._phone_auto_done = False
        self._phone_auto_at = time.time() + 12       # 启动 12 秒后第一次尝试
        # 提醒别一开机就催：留 60 秒让自动连接先跑，跑不起来再提醒。
        self._phone_remind_at = time.time() + 60
        self._phone_remind_last = ""
        # 感知模块：读懂「你此刻在干嘛」，只读系统信号，不碰反应动效链路。
        # _aware 是 awareness.Awareness 实例；_aware_state 是最近一次状态；
        # _aware_were_away 记「上一刻是否在离开」，用来做「你回来了」的重逢动作。
        self._aware = None
        self._aware_state = "idle"
        self._aware_were_away = False
        try:
            import awareness
            self._aware = awareness.Awareness(
                focus_threshold=float(self.cfg.get("aware_focus_sec", 180)),
                away_threshold=float(self.cfg.get("aware_away_sec", 600)),
                sample_interval=float(self.cfg.get("aware_sample_sec", 2.0)))
        except Exception as e:
            print("[aware] 感知模块不可用:", e)
        # 动作的用户设置（间隔、分类倍率、白黑名单、各档开关）。
        # 单独一个文件，见 anim_config.py 顶部说明。
        try:
            import anim_config as _ac
            self.anim_cfg = _ac.load()
            self._ac = _ac
        except Exception as e:
            self.anim_cfg, self._ac = {}, None
            print("[anim] 设置不可用:", e)
        try:
            import anim_manifest as _am
            self._am = _am
            self.anim_cd = _am.Cooldown(
                int(self.anim_cfg.get("cooldown", 36)))
        except Exception as e:
            print("[anim] 调度表不可用:", e)
        self.next_fidget_at = 8.0      # 先给个小动作，别一开机就死板地站着
        self.next_act_at = 25.0        # 第一段表演早一点，让人看见它会动
        self.last_act_group = None
        self.last_fidget_group = None
        # 最近一次由调度选出来的动作，供「这个不想看」用
        self.last_action_name = None
        self.jump_t = 0
        self.dragging = False
        self.drag_offset = None
        self.drag_start_pos = None
        self.last_line = ""
        self.last_press_pos = None
        self.eat_t = 0.0
        self.chew_t = 0.0
        self._weather_cache = None
        self._geo_cache = None
        
        self._say_queue = []    # 后台线程→主线程的气泡消息队列

        # 语音（BreezeBlue TTS）：合成在后台线程跑，播放在主线程
        if TTS_AVAILABLE:
            self.tts = BreezeTTS(self.cfg, TTS_CACHE_DIR, parent=self)
        else:
            self.tts = None
        self.talk_bob = 0.0

        # ---- 动效层 ----
        # 全部是算出来的，不用额外素材：每帧把「当前在演什么」换算成四个变换量，
        # paintEvent 只管读。想加动作只需在 ANIMS 里登记 + _update_fx 里写一条。
        self.fx_sx = 1.0          # 横向缩放（挤压 / 拉伸）
        self.fx_sy = 1.0          # 纵向缩放
        self.fx_rot = 0.0         # 旋转角度
        self.fx_dy = 0.0          # 额外垂直位移（跳跃、下沉）
        self.fx_dx = 0.0          # 额外水平位移（张望）
        self.anim_name = None     # 正在演的小动作
        self.anim_t = 0.0         # 0 → 1
        self.anim_dur = 1.0       # 秒
        self.anim_seed = 0.0      # 随机相位，免得每次都一样
        self.land_t = 0.0         # 落地挤压
        self.airborne = False     # 被甩出去了，正在飞
        self.throw_vx = 0.0
        self.throw_vy = 0.0
        self._last_drag_xy = None
        self._last_drag_t = 0.0
        self.bubble_born = 0.0    # 气泡出生时间，用来做弹出动画

        # 每日积分守门（领不到就哑巴，所以得盯着）
        self._credits_state = None
        self._credits_fresh = None     # 后台线程交回来的新结果
        self._credits_busy = False
        self._credits_next = time.time() + 8     # 启动 8 秒后先看一眼
        self._credits_nag_at = 0.0
        self._credits_nag_day = ""
        self._credits_nag_n = 0
        self._credits_low_day = ""      # 已领但余额见底，一天只提一次

        # 碎碎念的话题来源（跟对话走 or 只用原台词池）
        self.ambient = AmbientMemory() if MEM_AVAILABLE else None

        # 按下/拖拽时暂停碎碎念和动作，松手恢复
        self.chat_paused = False
        
        self.quick_panel = QuickPanel({
            "report": self.play_daily_report,
            "board": self._douyin_open_board,
            "act": self.do_random_act,
            "yoyo": self.on_yoyo,
            "clean": self.do_cleanup,
            "mute": self.toggle_mute,
        }, on_pick=self.on_food)
        self.yoyo_result.connect(self._on_yoyo_result)
        self.report_ready.connect(self._on_report_ready)
        self.takes_ready.connect(self._on_takes_ready)
        self.remark_ready.connect(self._on_remark_ready)

        # 手机状态快照：右键菜单只读这几个字段，不在主线程碰 adb
        self._phone_snap_text = ""        # 空 = 还没读到
        self._phone_snap_running = False
        self._phone_snap_ts = 0.0
        self._phone_probing = False       # 后台探测排队标志，防重复
        self._phone_busy = False          # 后台操作（连接/监听）排队标志
        self.phone_snap.connect(self._on_phone_snap)
        self.phone_op_done.connect(self._on_phone_op_done)
        self.board_op_done.connect(self._on_board_op_done)
        # 启动 2 秒后在后台预热一次：第一次右键看到的就是真状态，不用等。
        # 用带 parent 的 QTimer 而不是 QTimer.singleShot：窗口若在 2 秒内关掉，
        # singleShot 仍会回调到已销毁的对象上（Qt 直接崩），带 parent 的会把定时器一起收走。
        self._phone_warm = QTimer(self)
        self._phone_warm.setSingleShot(True)
        self._phone_warm.setInterval(2000)
        self._phone_warm.timeout.connect(self._refresh_phone_snap)
        self._phone_warm.start()
        # 单击延迟判定（等双击）：单击=回嘴+弹聊天面板，双击=喂食
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_single_click)
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(self.tick_ms)

        # 抖音收藏：每分钟看一次，负责「新收藏播报」和「每日固定汇报」。
        # 直接读同机 records.jsonl，不依赖看板服务是否在跑。
        self._douyin_timer = QTimer(self)
        self._douyin_timer.timeout.connect(self._douyin_tick)
        self._douyin_timer.start(60_000)

        # 推测感知：十分钟问一次「现在有没有话想说」。
        # 只在 due() 通过时才起线程——问一次不花钱，调模型才花。
        self._hunch_busy = False
        self._hunch_manual = False
        self.hunch_ready.connect(self._on_hunch)
        # 释放资源：连点两下不该跑两遍，用 _cleaning 闸门挡住
        self._cleaning = False
        self.cleanup_done.connect(self._on_cleanup_done)
        self._hunch_timer = QTimer(self)
        self._hunch_timer.timeout.connect(self._hunch_tick)
        self._hunch_timer.start(HUNCH_PERIOD_MS)
        # 只 start 的话第一次要等满十分钟——刚重启根本看不到它开口，
        # 用户只能当成坏了。先给一次早的，之后回到十分钟一循环。
        QTimer.singleShot(HUNCH_FIRST_MS, self._hunch_tick)

        self.bubble_font = QFont("Microsoft YaHei UI", 11)

        # 托盘
        self.tray = QSystemTrayIcon(self.icon, self)
        # 托盘右键也走自绘菜单：给了 QMenu 的话 Qt 会自己弹原生那套，风格对不上。
        # 不给 menu，activated(Context) 照样会发，位置用光标处。
        self.tray.setContextMenu(None)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        if self.muted:
            self.tray.setToolTip(TRAY_TIP_MUTED)

        x, y = self.cfg.get("x"), self.cfg.get("y")
        if x is None or y is None:
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.right() - self.width() - 80
            y = screen.bottom() - self.height() - 60
        self.move(int(x), int(y))
        self.show()
        self.snap_into_screen()
        if self.cfg.get("passthrough", False):
            self._apply_passthrough(True)

        if self.muted:
            # 静音是会跨重启的，起来必须自己招一声，不然用户当成坏了
            QTimer.singleShot(1500, lambda: self.say(
                "还静音着呢。双击我，点「已静音」就能开声。",
                force=True, speak=False, seconds=8, layer="system"))


    # ---------- 绘制 ----------
    @staticmethod
    def _wrap_text(fm, text, max_w):
        """按像素宽度折行，中英文都按字算，标点不落行首"""
        lines, cur = [], ""
        for ch in text:
            if cur and fm.horizontalAdvance(cur + ch) > max_w:
                if ch in "，。！？、；：）」』】,.!?;:)" and len(cur) > 1:
                    cur += ch
                    lines.append(cur)
                    cur = ""
                    continue
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines or [""]

    def _brighten(self, pix):
        """给一张 QPixmap 提亮（亮度增益）。结果按像素对象缓存。

        素材本身偏暗（不透明区均亮约 109，低于中性灰 128），直接渲染显得灰。
        这里用 Plus 叠加半透明白、再用原图 alpha 抠回透明，只提亮角色、不污染背景。
        amount 取 self.cfg["brightness"]（0~0.4，默认 0.15），0 时原样返回。
        """
        amt = self.cfg.get("brightness", 0.25)
        if amt <= 0.001 or pix is None or pix.isNull():
            return pix
        # 用 cacheKey 而不是 id()：id 是内存地址，pixmap 释放后会被新对象复用，
        # 命中缓存就会拿到上一张图——那是「亮度突然错一帧」的真凶。
        key = (pix.cacheKey(), self._bright_key)
        hit = self._bright_cache.get(key)
        if hit is not None:
            self._bright_cache.move_to_end(key)
            return hit
        w, h = pix.width(), pix.height()
        tmp = QPixmap(w, h)
        tmp.fill(Qt.GlobalColor.transparent)
        p = QPainter(tmp)
        p.drawPixmap(0, 0, pix)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        v = int(255 * amt)
        p.fillRect(pix.rect(), QColor(v, v, v, 255))
        p.end()
        out = QPixmap(w, h)
        out.fill(Qt.GlobalColor.transparent)
        p2 = QPainter(out)
        p2.drawPixmap(0, 0, tmp)
        p2.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        p2.drawPixmap(0, 0, pix)
        p2.end()
        self._bright_cache[key] = out
        self._bright_used += w * h * 4
        # 24MB 预算：0.55 档约 230 张、0.9 档约 85 张，都够盖住「当前在播 + 预热」
        while self._bright_cache and self._bright_used > 24 * 1024 * 1024:
            _, old_pm = self._bright_cache.popitem(last=False)
            self._bright_used -= old_pm.width() * old_pm.height() * 4
        return out

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        now = self.t / 1000.0

        # 开关关掉时连已经挂在屏上的那条也不画：只靠 say() 挡不住
        # 「关之前已经显示、还没到期」的残留，画的时候再挡一道才彻底。
        if self.bubble_on and self.bubble_text and now < self.bubble_until:
            if self.bubble_inner:
                bg, fg, bd = QColor(236, 236, 242, 244), QColor(108, 108, 124), QColor(0, 0, 0, 26)
            else:
                bg, fg, bd = QColor(255, 255, 255, 248), QColor(52, 52, 74), QColor(0, 0, 0, 40)
            pad_x, pad_y = 11, 6
            avail_w = max(90, self.width() - 10)
            # 从 11pt 往下试字号，保证气泡完整落在顶部预留区里，不会压到脑袋
            bfont, lines, fm = None, [], None
            for pt in (11, 10, 9, 8):
                bfont = QFont(self.bubble_font)
                bfont.setPointSize(pt)
                bfont.setItalic(self.bubble_inner)
                fm = QFontMetrics(bfont)
                lines = self._wrap_text(fm, self.bubble_text, avail_w - pad_x * 2)
                if len(lines) * fm.height() + pad_y * 2 <= BUBBLE_H - 6:
                    break
            # 实在放不下就把最后一行截断加省略号
            while len(lines) > 1 and len(lines) * fm.height() + pad_y * 2 > BUBBLE_H - 6:
                lines.pop()
                lines[-1] = lines[-1].rstrip() + "…"
            bw = max(fm.horizontalAdvance(l) for l in lines) + pad_x * 2
            bh = len(lines) * fm.height() + pad_y * 2
            bx = (self.width() - bw) / 2
            by = 3.0
            # 弹出动画：刚开口的前 0.16 秒从气泡根部放大，带一点过冲。
            # 直接硬弹出来会让人觉得是画面闪了一下。
            age = max(0.0, now - getattr(self, "bubble_born", 0.0))
            pop = min(1.0, age / 0.16)
            pop_k = 1.0 - (1.0 - pop) ** 3
            over = 0.035 * math.sin(pop * math.pi)
            bscale = 0.90 + 0.10 * pop_k + over
            p.save()
            p.setOpacity(0.55 + 0.45 * pop_k)
            p.translate(self.width() / 2, by + bh)
            p.scale(bscale, bscale)
            p.translate(-self.width() / 2, -(by + bh))
            # 阴影
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(20, 20, 40, 26))
            p.drawRoundedRect(QRectF(bx + 1.5, by + 2.5, bw, bh), 11, 11)
            # 气泡本体 + 1px 描边（浅色桌面上也能看清边界）
            p.setPen(QPen(bd, 1))
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(bx, by, bw, bh), 11, 11)
            # 尾巴：描边三角 + 填充三角
            tail_x = self.width() / 2
            tail_y = by + bh - 1
            tail = QPolygonF([QPointF(tail_x - 7, tail_y),
                              QPointF(tail_x + 7, tail_y),
                              QPointF(tail_x, tail_y + 9)])
            p.setPen(QPen(bd, 1))
            p.setBrush(bg)
            p.drawPolygon(tail)
            p.setPen(fg)
            p.setFont(bfont)
            for i, l in enumerate(lines):
                p.drawText(QRectF(bx, by + pad_y + i * fm.height(), bw, fm.height()),
                           Qt.AlignmentFlag.AlignCenter, l)
            p.restore()

        cx = self.width() / 2
        sx, sy, rot, dy, dx = (self.fx_sx, self.fx_sy, self.fx_rot,
                               self.fx_dy, self.fx_dx)
        bottom = BUBBLE_H + MARGIN + self.cur_h
        self._painted_fish = []
        if self._edge:
            side = -1 if self._edge == "left" else 1
            revealed = time.monotonic() < self._peek_until
            dx += side * (cx - self.cur_h * (0.53 if revealed else 0.09))
            rot -= side * (5 if revealed else 22)

        def draw_pix(pix, opacity, facing=1):
            pix = self._brighten(pix)      # 亮度增益（只提亮角色，不污染背景）
            ph = pix.height() * sy
            pw = max(2.0, pix.width() * sx)
            bx = cx + dx - pw / 2
            by = bottom - ph + dy
            p.save()
            p.setOpacity(opacity)
            p.translate(cx + dx, bottom)
            p.rotate(rot)
            p.translate(-(cx + dx), -bottom)
            if facing < 0:
                p.translate(cx + dx, 0)
                p.scale(-1, 1)
                p.translate(-(cx + dx), 0)
            inverse, valid = p.transform().inverted()
            if valid and opacity > 0.1:
                self._painted_fish.append((inverse, QRectF(bx, by, pw, ph), pix))
            p.drawPixmap(QRectF(bx, by, pw, ph), pix,
                         QRectF(0, 0, pix.width(), pix.height()))
            p.restore()

        def draw_one(key, opacity):
            if key is None:
                return
            name, h, facing = key
            draw_pix(self.sprites[(name, h)], opacity, facing)

        # 脚下的影子：默认关。这只鱼是贴图剪影，一块灰椭圆压在地上反而显得脏，
        # 尤其桌面上本来就有壁纸纹理时。想要回立体感就把 config.json 里
        # ground_shadow 改成 true——参数和以前一样，离地越高越小越淡。
        if self.cfg.get("ground_shadow", False):
            lift = max(0.0, -dy)
            shrink = min(0.5, lift / 55.0)
            sh_rx = max(4.0, self.cur_h * 0.26 * (1.0 - shrink))
            sh_ry = max(2.5, self.cur_h * 0.042 * (1.0 - shrink * 1.4))
            alpha = int(48 * (1.0 - min(0.72, lift / 58.0)))
            if alpha > 3:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(18, 22, 38, alpha))
                p.drawEllipse(QPointF(cx + dx, bottom - 1.0), sh_rx, sh_ry)

        frame = self.clip_frame()
        if frame is not None and not frame.isNull():
            # 素材帧是正面全身，画的时候照样吃 sway / 挤压 / 甩抛这一层。
            # 左右走要跟着 facing 翻——不然往左走，动画里还在往右挪。
            draw_pix(frame, 1.0,
                     self.facing if self.dir in ("left", "right") else 1)
        else:
            cur_key = self._sprite_key()
            if self.cross_t > 0:
                draw_one(self.prev_key, self.cross_t)
                draw_one(cur_key, 1.0 - self.cross_t)
            else:
                draw_one(cur_key, 1.0)

    def _sprite_key(self):
        name = {"left": "侧面", "right": "侧面", "up": "背面", "down": "正面"}[self.dir]
        return (name, self.cur_h, self.facing if self.dir in ("left", "right") else 1)

    def _set_dir(self, d, facing=None):
        if d != self.dir:
            self.prev_key = self._sprite_key()
            self.cross_t = 1.0
            self.dir = d
        if facing is not None and facing != self.facing:
            self.facing = facing

    # ---------- 逻辑 ----------
    def _fly(self, dt):
        """松手甩出去：带重力、撞墙反弹、落地压一下。

        没这层的话，拖完就是一松手定在原地，像在搬一张图片。
        """
        self.throw_vy += 2600.0 * dt
        nx = self.x() + self.throw_vx * dt
        ny = self.y() + self.throw_vy * dt
        geo = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        if nx < geo.left():
            nx = geo.left()
            self.throw_vx = -self.throw_vx * 0.55
        elif nx + self.width() > geo.right():
            nx = geo.right() - self.width()
            self.throw_vx = -self.throw_vx * 0.55
        ground = geo.bottom() - self.height()
        if ny >= ground:
            ny = ground
            self.airborne = False
            self.land_t = 1.0
            self.throw_vx = self.throw_vy = 0.0
            self.target = None
            self.rest_until = self.t + random.randint(5000, 12000)
        elif ny < geo.top():
            ny = geo.top()
            self.throw_vy = -self.throw_vy * 0.4
        self.move(int(nx), int(ny))
        if abs(self.throw_vx) > 80:
            self._set_dir("left" if self.throw_vx < 0 else "right",
                          1 if self.throw_vx < 0 else -1)

    # ---------- 整点报时 ----------
    def _maybe_chime(self):
        """到整点说一句。判断全在 chime.py，这里只管「报不报、怎么报」。

        两秒看一次表：50fps 下每帧都问一次时间是白费，而整点报时对
        一两秒的延迟完全不敏感。
        """
        if chime is None or not self.cfg.get("chime_on", True):
            return
        now_t = self.t / 1000.0
        if now_t - self._chime_check_t < 2.0:
            return
        self._chime_check_t = now_t
        if self.sleeping or self.dragging:
            return                       # 睡着 / 被拖着的时候不打断
        try:
            ok, key = chime.should_chime(self.cfg, self._chime_key)
        except Exception as e:           # noqa: BLE001  报时坏了不能影响桌宠
            print("[chime] 判断失败:", e)
            return
        if not ok:
            return
        self._chime_key = key
        try:
            line = chime.line_for(self.cfg)
        except Exception:                # noqa: BLE001
            line = "整点了。"
        # 念不念由面板决定；全局语音关掉时 tts.speak 自己会拒，不用在这儿再判一次
        self.say(line, force=True, speak=bool(self.cfg.get("chime_speak", True)))

    # ---------- 帧率：按「现在有没有事做」切档 ----------
    def _pick_tick(self):
        """按「现在有没有人看」决定帧率。

        你在电脑前：20ms 一帧，跟以前一模一样，一帧不少。
        你离开十分钟（键鼠没动）：降到 10fps——素材本来就是 10fps，
        这时一帧只画一次，画面本身没有任何变化，只是呼吸的过渡粗一点，
        而这时候屏幕前根本没人。
        窗口被隐藏：降到最低，看不见的画了也是白画。
        """
        if not self.isVisible():
            return TICK_HIDDEN
        if self._aware is not None and self._aware.state() == "away":
            return TICK_AWAY
        return TICK

    def _adapt_tick(self):
        """档位变了才动表。每帧都 start() 会重置计时，实际帧率反而掉一半。"""
        want = self._pick_tick()
        if want == self.tick_ms:
            return
        self.tick_ms = want
        if self.timer.isActive():
            self.timer.start(want)

    def tick(self):
        self._adapt_tick()
        self.t += self.tick_ms
        dt = self.tick_ms / 1000.0
        self._update_fx(dt)              # 先把这一帧的动效算好，下面只管用
        self._advance_clip()             # 素材动作播完了就收/接下一段
        self._maybe_chime()              # 整点报时（内部两秒看一次表，不占帧）

        # 站着不动就一直播呼吸。这条是「活着」和「一张贴图」的分界线：
        # 没有它，待机时角色完全静止，一眼就看出是静态图。
        if (not self.sleeping and not self.dragging and not self.airborne
                and self.target is None and not self.clip):
            self.play_clip("idle", loop=True, force=False)

        if self.land_t > 0:
            self.land_t = max(0.0, self.land_t - 0.055)

        # 处理后台线程（DeepSeek 等）排队的气泡消息，Qt 界面必须在主线程更新
        if self._say_queue:
            for item in self._say_queue:
                # 老调用方只塞 3/4 元，新调用方多带 speech/style，统一补齐
                item = tuple(item) + (None,) * 6
                text, seconds, speak, anim, speech, style = item[:6]
                self.say(text, seconds=seconds, force=True, speak=speak,
                         react=False, layer="system", speech=speech, style=style)
                # 通知播报这类指定了 anim 的，说完强制演动作（不走随机克制）
                if anim:
                    # 工作类动作：进入「干活」状态——先演开场，再循环忙活到念完
                    if self._is_work_anim(anim):
                        self.start_working(intro=anim, seconds=seconds)
                    else:
                        self.play_clip(anim, force=True)
            self._say_queue.clear()

        # 说话时跟着音频轻轻上下颠，替掉没有口型图的遗憾
        if self.tts is not None and self.tts.speaking:
            self.talk_bob = 0.02 * math.sin(self.t * 0.0275)   # 27.5 rad/s，跟原节奏一致
            # 气泡要撑到声音念完，否则看着就像「话没说完字先没了」
            if self.bubble_text:
                self.bubble_until = min(
                    max(self.bubble_until, self.t / 1000.0 + 0.5),
                    self.bubble_max_until)
        else:
            self.talk_bob = 0.0

        # 干活收工：到 work_until 就收。语音念完不影响工作状态——
        # 开场白只是点个题，真正「干活」的时长由 start_working 定时。
        if self.working and self.t / 1000.0 >= self.work_until:
            self._stop_working()

        self.check_system_status()
        self._credits_tick()
        self._phone_tick()
        self._awareness_tick()
        
        if self.jump_t > 0:
            self.jump_t = max(0.0, self.jump_t - 0.06)
        if self.eat_t > 0:
            self.eat_t = max(0.0, self.eat_t - 0.024)
        if self.cross_t > 0:
            self.cross_t = max(0.0, self.cross_t - 0.15)
        
        # 甩出去的飞行：这条路要挡住下面的漫游逻辑，否则两套互相打架
        if self.airborne and not self.dragging:
            self._stop_move_anim()
            self._fly(dt)
            self.update()
            return

        if self.sleeping:                # 睡着就别乱走乱说，安静着
            self._stop_move_anim()
            self.update()
            return

        if self.chat_paused or self._edge:
            self._stop_move_anim()
            self.update()
            return
        
        if self.dragging:
            self._stop_move_anim()
            self.update()
            return

        if self._drag_paths:
            # 有东西被拖到它身上：站住别走。一边溜达一边吃很怪，更要紧的是
            # 鱼一走窗口就滑开，光标脱靶，拖放断在半路——那条路直接交给桌面，
            # 文件可能被挪到桌面上去，比没吃成更糟。
            self._stop_move_anim()
            self.update()
            return
        if self._auto_eat_paths:
            # 到点该吃了，但拖放源没发 dragLeave（有的源就这样）。等下一跳
            # 再交付一次，别让文件挂在半空中永远不吃。
            self._flush_auto_eat()
        now_ms = self.t

        if self.mode == "follow":
            cursor = self.cursor().pos()
            screen = QApplication.screenAt(cursor) or self.screen() or QApplication.primaryScreen()
            geo = screen.availableGeometry()
            near = (self.x() - 100 <= cursor.x() <= self.x() + self.width() + 100 and
                    self.y() - 100 <= cursor.y() <= self.y() + self.height() + 100)
            if near:
                self.target = None
            else:
                # target 是窗口中心坐标，所以要按半个窗口内缩，否则贴边时会有一半身子在屏幕外
                hw, hh = self.width() / 2, self.height() / 2
                tx = max(geo.left() + hw, min(geo.right() - hw, cursor.x()))
                ty = max(geo.top() + hh, min(geo.bottom() - hh, cursor.y() - 90))
                self.target = (tx, ty)
        elif self.mode == "wander":
            if self.target is None:
                # 正在演整段动作：站定演完再走。边走边吃、边溜达边放烟花
                # 都很怪，而且侧身走的图会被动作帧盖掉，看着像两个人。
                if self.clip and self.clip != "idle":
                    self.update()
                    return
                if now_ms < self.rest_until:
                    self._maybe_idle_action()
                    self.update()
                    return
                geo = (self.screen() or QApplication.primaryScreen()).availableGeometry()
                self.target = self._random_point_in(geo)
        else:
            self._stop_move_anim()
            self._maybe_idle_action()
            self.update()
            return

        if self.target is not None:
            cx, cy = self.x() + self.width() / 2, self.y() + self.height() / 2
            dx, dy = self.target[0] - cx, self.target[1] - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < 12:
                self.target = None
                self._stop_move_anim()    # 循环的移动动画要手动收，不然会原地一直跑
                self.rest_until = self.t + random.randint(8000, 18000)
                self._set_dir("down")
            else:
                # 先定朝向：移动动画是按方向挑的（左右走侧向、上下走漂浮）
                if abs(dx) > abs(dy) * 1.15:
                    self._set_dir("left" if dx < 0 else "right", 1 if dx < 0 else -1)
                else:
                    self._set_dir("up" if dy < 0 else "down")
                step = self._start_move_anim()
                if step is None:
                    if self.clip == "idle":   # 要走就用侧面图，别拿正面呼吸图平移
                        self.clip = None
                    step = self.cur_speed * self.tick_ms / 1000.0
                # 一 tick 只走 2~3px，int() 截断会把小数吃掉（实测 10 tick 慢 25%），
                # 所以小数攒在 _rem_x/_rem_y 里，够一像素再补上。
                rx = self._rem_x + dx / dist * step
                ry = self._rem_y + dy / dist * step
                sx, sy = int(rx), int(ry)
                self._rem_x, self._rem_y = rx - sx, ry - sy
                self.move(self.x() + sx, self.y() + sy)
                self._clamp_on_screen()
            if random.random() < 0.002 and self.jump_t == 0:
                self.jump_t = 0.5
        target_speed = SPEED if self.target is not None else 0.0
        self.cur_speed += (target_speed - self.cur_speed) * 0.3
        self.update()

    # ---------- 动效 ----------
    # 动作表：名字 → 默认时长（秒）。时长只是节奏，不想统一可以单独传。
    ANIMS = {
        "bounce3": 2.0,      # 连蹦三下
        "sway": 2.4,         # 左右摇
        "stretch": 2.2,      # 伸懒腰
        "nod": 1.5,          # 点头
        "shake": 0.9,        # 打个抖
        "lean": 2.0,         # 歪头
        "peek": 2.4,         # 左右张望
        "spin": 1.6,         # 原地转一圈
        "doze": 3.8,         # 打瞌睡（沉下去又惊醒）
        "breath_deep": 3.4,  # 深呼吸
        "squash": 0.34,      # 被点了一下，Q 弹
    }

    # ---------- 素材动作 ----------
    _SLEEP_CLIPS = ("sleep_in", "sleep_loop", "sleep_out")

    # 「内容核」开场动作：开始干活时先来一下，点明「我要开工了」。
    # 之后接 _WORK_LOOP 里的动作持续循环，直到语音念完。
    _WORK_INTRO = ("work_busy", "note_taking", "work_think")

    # 工作模式最短持续时长（秒）。语音只是开场白，几秒就念完；
    # 但「干活」本身要撑够这段时间，不耐烦动作才有机会按时间混进来。
    WORK_MIN_SECONDS = 30.0

    def _work_anims(self):
        """工作循环可用的动作：所有跟「干活」沾边的动作。

        判定 = 名字以 work_ 开头，或属于 grind 组（coding/note_taking 等）。
        这样 work_busy/work_think/work_archive/work_pace/work_sigh/work_cheer
        全都能进循环，干活时轮着演，不会翻来覆去就一个。
        """
        if not self._work_pool:
            import pet_acts
            avail = self.available_anims
            grind = set(pet_acts.GROUPS.get("grind", []))
            pool = sorted({n for n in avail
                           if n.startswith("work_") or n in grind})
            self._work_pool = pool or ["idle"]
        return self._work_pool

    def _impatient_anims(self):
        """不耐烦动作池：干活久了会开始甩尾巴、叹气、拍桌子。

        从 impatient 组里筛素材真实存在的，且别跟工作动作重复。
        """
        import pet_acts
        avail = self.available_anims
        pool = sorted({n for n in pet_acts.GROUPS.get("impatient", [])
                       if n in avail})
        return pool

    # 收工时的「得意表演」动作：干完了，显摆一下。
    # 从 proud 组挑最像「庆祝/炫耀」的几个，排除纯乐器演奏（violin/flute）
    # 和行礼（curtsey）这种不够「得意」的。
    _PROUD_ANIMS = (
        "work_cheer", "dance_maid", "dance_cute", "dance_sway",
        "spin", "juggle_balls", "curtsey",
    )

    def _proud_anims(self):
        """得意表演动作池：收工时随机挑一个，庆祝活儿干完了。"""
        avail = self.available_anims
        return [n for n in self._PROUD_ANIMS if n in avail]

    def _pick_work_clip(self, last=None):
        """干活循环里挑下一个动作：工作动作为主，越干越久越不耐烦。

        刚开工几乎全是正经干活；干了 30 秒后，不耐烦动作开始按比例混进来，
        干得越久越烦躁（封顶约 40% 概率抽到甩尾巴/叹气/拍桌子这类动作），
        像一条被压榨到没脾气的鱼。last 是刚演完的那个，别紧接着重复。
        """
        import random
        work = self._work_anims()
        imp = self._impatient_anims()

        # 已工作秒数 → 不耐烦概率：0s≈5%，30s≈25%，60s≈40% 封顶
        elapsed = self.t / 1000.0 - self._work_start_t
        imp_prob = min(0.40, 0.05 + max(0.0, elapsed) * 0.006)

        if imp and (not work or random.random() < imp_prob):
            pool = imp
        else:
            pool = work or imp
        pool = [n for n in pool if n != last] or pool or ["idle"]
        return random.choice(pool)

    def _is_work_anim(self, name):
        """这个动作算不算「干活」。工作类才进循环，钱/聊天/紧急类只演一遍。"""
        if not name:
            return False
        return name in self._work_anims()

    def start_working(self, intro=None, seconds=8.0):
        """进入「干活」状态：先演内容核开场，再循环工作动作，持续到 work_until。

        intro 是开场动作名（通知类别已经给定了 work_busy/note_taking 之类）；
        没给就自己从开场池里挑。seconds 是调用方给的预估时长，但工作模式至少
        撑够 WORK_MIN_SECONDS，否则「越干越不耐烦」根本来不及展开。
        语音只是开场白，念完不影响工作状态——工作由 work_until 定时收工。
        """
        if self.sleeping:
            return
        if not self.animlib or not self.available_anims:
            return
        # 开场：优先用调用方指定的，否则从开场池随机挑
        if intro and intro in self.available_anims:
            start = intro
        else:
            cand = [n for n in self._WORK_INTRO if n in self.available_anims]
            start = random.choice(cand) if cand else None
        if not start:
            return
        self.working = True
        self.work_until = self.t / 1000.0 + max(
            self.WORK_MIN_SECONDS, seconds)
        self._work_start_t = self.t / 1000.0
        print(f"[work] 开工 {start} 持续 {self.work_until - self._work_start_t:.0f}s "
              f"（t={self._work_start_t:.1f}）")
        self.play_clip(start, force=True)

    def _stop_working(self):
        """干完收工：清掉工作循环，播一段「得意表演」，再回到待机。"""
        print(f"[work] 收工（t={self.t / 1000.0:.1f}，"
              f"计划 {self.work_until:.1f}）")
        self.working = False
        self.work_until = 0.0
        # 活儿干完了，显摆一下：随机挑一个得意动作演。
        # force=True 盖掉正在循环的工作动作；播完 clip 走到 None 后回待机。
        import random
        proud = self._proud_anims()
        if proud:
            self.play_clip(random.choice(proud), force=True)
            print(f"[work] 得意表演：{self.clip}")
        elif self.clip and self.clip != "idle":
            self.clip = None

    def play_clip(self, name, loop=False, after=None, hold=False, force=True):
        """播一段素材动作。

        没有素材时返回 False，调用方继续走程序化动作——两条路都能用，
        素材缺了不至于整个动作系统瘫掉。
        """
        if not self.animlib or not self.animlib.has(name):
            return False
        if not force and self.clip in self._SLEEP_CLIPS:
            return False
        # 同一个动作正在播，就别从头再来。
        # 连点两下、探针重复上报同一事件，都会把动画拽回第一帧——看着像抽了一下。
        # 循环动作和带后续衔接的不算：idle/drag 要能重进，sleep_in→sleep_loop 不能被截断。
        if (self.cfg.get("clip_no_restart", True)
                and not loop and not self.clip_loop and not self.clip_after
                and self.clip == name):
            played = self.t / 1000.0 - self.clip_t0
            if played < self.animlib.seconds(name):
                return True
        self.clip = name
        self.clip_t0 = self.t / 1000.0
        self.clip_loop = loop
        self.clip_after = after
        self.clip_hold = hold
        self.clip_span = None          # 换动作一律回到整圈播
        self.clip_rate = 1.0
        return True

    def play_pending_act(self):
        """启动后演一次 pending_act.json 指定的动作，读完即删。

        给「重启后先验一眼某个新动作」用：先写文件再启动，鱼一露脸就演一遍，
        演完自动回落到日常行为。文件不存在或动作不存在都静默跳过，不影响正常启动。
        格式：{"anim": "run_side", "delay": 1.5}
        """
        p = os.path.join(APP_DIR, "pending_act.json")
        if not os.path.exists(p):
            return
        try:
            with open(p, encoding="utf-8") as f:
                req = json.load(f)
            os.remove(p)          # 先删再演：演失败也不会在下次启动重复触发
        except Exception:
            return
        name = str((req or {}).get("anim") or "").strip()
        if not name:
            return
        if not self.animlib or not self.animlib.has(name):
            print("[pending_act] 没有这个动作，跳过:", name)
            return
        try:
            delay = float((req or {}).get("delay", 1.5))
        except (TypeError, ValueError):
            delay = 1.5
        print("[pending_act] 将在 %.1fs 后播放 %s" % (delay, name))

        def _go():
            # 先把随机动作压住，否则演到一半会被 ambient 抢走——
            # run_side 有 10 秒，不压的话 6 秒就切走了，验收等于没看全。
            span = self.animlib.seconds(name) + 2
            self.rest_until = self.t + int(span * 1000)
            self.play_clip(name)

        QTimer.singleShot(max(0, int(delay * 1000)), _go)

    def _advance_clip(self):
        """素材播完了没：该接下一段的接，该收的收。"""
        if not self.clip or not self.animlib:
            return
        now = self.t / 1000.0
        if self.clip_loop:
            return
        if now - self.clip_t0 < self.animlib.seconds(self.clip):
            return
        nxt = self.clip_after
        self.clip_after = None
        if nxt:
            self.clip = nxt
            self.clip_t0 = now
            self.clip_loop = (nxt == "sleep_loop")
            return
        if self.clip_hold:
            return                       # 停在最后一帧，比如睡着的样子
        # 干活中：一个工作动作演完了，还没到收工时间，就接着循环下一个，
        # 形成「一直在忙活」的效果，而不是演一遍就停。
        # 越干越久越不耐烦：工作时间拉长后，不耐烦动作按比例混进来。
        if self.working and now < self.work_until:
            nxt = self._pick_work_clip(last=self.clip)
            print(f"[work] 循环切 {self.clip} -> {nxt} "
                  f"（已干 {now - self._work_start_t:.0f}s）")
            self.clip = nxt
            self.clip_t0 = now
            self.clip_loop = False
            return
        self.clip = None

    def clip_frame(self):
        """当前该画哪一帧；没在播素材就返回 None。"""
        if not self.clip or not self.animlib:
            return None
        now = self.t / 1000.0
        if self.clip_span:
            f0, f1 = self.clip_span
            return self.animlib.frame_between(
                self.clip, now - self.clip_t0, f0, f1, self.clip_rate)
        return self.animlib.frame_at(self.clip, now - self.clip_t0)

    def go_sleep(self):
        if self.sleeping:
            return
        self.sleeping = True
        self.target = None
        if not self.play_clip("sleep_in", after="sleep_loop", force=True):
            self.say("困了，先眯着")
        self.update()

    def wake_up(self):
        if not self.sleeping:
            return
        self.sleeping = False
        self.play_clip("sleep_out", force=True)
        self.say("……醒了，几点了")

    def play_anim(self, name, dur=None):
        """开始演一个动作。已经在演的会被顶掉——点它一下就该立刻有反应。"""
        self.anim_name = name
        self.anim_t = 0.0
        self.anim_dur = dur if dur else self.ANIMS.get(name, 1.5)
        self.anim_seed = random.random() * math.tau

    def _update_fx(self, dt):
        """把「当前在演什么」换算成 fx_sx / fx_sy / fx_rot / fx_dy。

        整套动效的原则：只做小幅、短时、能收回去的变化。
        幅度大到让人注意到「它在动」，就过了。
        """
        sx, sy, rot, dy, dx = 1.0, 1.0, 0.0, 0.0, 0.0
        now = self.t / 1000.0

        # ---- 正在演的小动作 ----
        if self.anim_name:
            self.anim_t = min(1.0, self.anim_t + dt / max(0.05, self.anim_dur))
            t = self.anim_t
            # 起势 12%、收势 18%，中间保持满强度：硬切会像抽帧
            env = max(0.0, min(1.0, t / 0.12) * min(1.0, (1.0 - t) / 0.18))
            n = self.anim_name
            if n == "sway":
                rot += math.sin(t * math.tau + self.anim_seed) * 9.0 * env
            elif n == "stretch":
                k = math.sin(t * math.pi) * env
                sy += 0.10 * k
                sx -= 0.05 * k
                dy -= 5.0 * k
            elif n == "breath_deep":
                k = math.sin(t * math.tau) * env
                sy += 0.035 * k
                sx -= 0.015 * k
            elif n == "nod":
                k = abs(math.sin(t * math.pi * 3)) * env
                dy += 6.0 * k
                sy -= 0.04 * k
            elif n == "shake":
                rot += math.sin(t * 46.0) * 3.4 * env
                dx += math.sin(t * 38.0) * 1.6 * env
            elif n == "lean":
                # 往一边倒，停一下，再回正
                bend = math.sin(t * math.pi)
                rot += 11.0 * bend * env
                dx += 3.0 * bend * env
            elif n == "spin":
                # 横向压扁再展开，单张静态图也能看出「转了一圈」
                c = math.cos(t * math.tau)
                sx *= 0.22 + 0.78 * abs(c)
                sy *= 1.0 - 0.03 * (1.0 - abs(c))
            elif n == "bounce3":
                hop = abs(math.sin(t * math.pi * 3))
                dy -= hop * 13.0 * env
                land = max(0.0, 1.0 - hop * 2.4)      # 落地那一下压扁
                sy -= 0.055 * land
                sx += 0.045 * land
            elif n == "doze":
                # 一点点沉下去，中途自己惊醒抖一下，再沉
                sink = min(1.0, t * 1.5) * (0.5 + 0.5 * t)
                dy += 7.0 * sink * env
                rot += 2.6 * math.sin(t * 9.0) * env
                if 0.55 < t < 0.63:
                    rot += math.sin((t - 0.55) * 110.0) * 2.4
            elif n == "peek":
                # 往左看、往右看、回正
                look = math.sin(t * math.pi * 4) * env
                rot += look * 5.0
                dx += look * 3.0
            elif n == "squash":
                # easeOutElastic 的近似：压下去，弹回来时过冲一点
                k = math.sin(t * math.pi) * (1.0 - t * 0.45)
                sy -= 0.16 * k
                sx += 0.13 * k

            if self.anim_t >= 1.0:
                self.anim_name = None

        # ---- 常态：呼吸 + 走路 / 站着 ----
        walking = self.target is not None and not self.dragging
        if walking:
            rot += math.sin(now * 9.0) * 3.2
            dy -= abs(math.sin(now * 4.5)) * 7.0
        else:
            # 站着时极轻微的晃，幅度小到「感觉得到但看不出来」
            rot += math.sin(now * 1.7) * 1.1

        # 呼吸：吸气比呼气快一点，比匀速正弦像活物
        breath = math.sin(now * 2.5)
        sy *= 1.0 + 0.018 * breath
        sx *= 1.0 - 0.007 * breath

        # ---- 跳跃 ----
        if self.jump_t > 0:
            dy -= abs(math.sin(self.jump_t * math.pi)) * 14.0 * self.jump_t

        # ---- 落地挤压 ----
        if self.land_t > 0:
            k = math.sin(self.land_t * math.pi) * self.land_t
            sy -= 0.12 * k
            sx += 0.10 * k

        # ---- 吃东西：边嚼边颠 ----
        if self.eat_t > 0:
            sy += 0.05 * math.sin(self.eat_t * 26.0) * min(1.0, self.eat_t / 0.25)

        # ---- 出声时的小幅起伏 ----
        if self.talk_bob:
            sy += self.talk_bob

        # ---- 被拖拽时按水平速度倾斜，像被拎着甩 ----
        if self.dragging:
            rot += max(-14.0, min(14.0, self.throw_vx * 0.02))

        self.fx_sx, self.fx_sy = sx, sy
        self.fx_rot, self.fx_dy, self.fx_dx = rot, dy, dx

    def _maybe_idle_action(self):
        """待机时该不该演点什么。

        两档节奏，各自计时，互不干扰：
        - fidget：10~24 秒一次的小动作，不打断你干活
        - act：90~240 秒一次的整段表演

        以前是每个 tick 掷一次骰子（0.0035），本质是泊松过程：节奏不可控，
        刚演完一段马上可能再演一段，看着像抽筋，而且「放烟花」和「打个哈欠」
        出场的概率被拉平成同一个量级。分档之后才像「一条鱼在过日子」。
        """
        if self.sleeping:          # 睡着了就别闹
            return
        # idle 是待机常驻的底片，不算「正在演」——不排除它的话，
        # 这里会永远 return，109 个动作一个都演不出来。
        if self.clip and self.clip != "idle":
            return
        if self.anim_name:
            return

        if not (self.animlib and self.available_anims and self._am):
            self._procedural_idle()
            return

        now_s = self.t / 1000.0
        lt = time.localtime()
        minutes_idle = max(0.0, (now_s - self.last_attention_t) / 60.0)

        # 整段表演优先：到点了就先演节目，别被小动作抢掉
        if now_s >= self.next_act_at:
            name, why = self._am.pick_act(
                lt.tm_hour, lt.tm_mon, lt.tm_mday, self.available_anims,
                self.anim_cd, self.last_act_group, minutes_idle,
                cfg=self.anim_cfg)
            # 感知介入：专注干活时，大场面（放烟花/蓝鲸这类 fx）先按住，
            # 别在你赶工时来一段抢眼球的节目。只拦 fx，普通表演照常。
            if name and self._aware_focus() and self._am.kind_of(name) == "fx":
                self.next_act_at = now_s + 30
            elif name and self.play_clip(name, force=False):
                self._after_action(name, why, act=True)
                return
            else:
                self.next_act_at = now_s + 30      # 没挑到，过会儿再试

        if now_s >= self.next_fidget_at:
            name, grp = self._am.pick_fidget(
                lt.tm_hour, self.available_anims, self.anim_cd,
                self.last_fidget_group, cfg=self.anim_cfg)
            if name and self.play_clip(name, force=False):
                self._after_action(name, grp, act=False)
                return
            self.next_fidget_at = now_s + 12

    def _after_action(self, name, why, act=False):
        """记冷却、排下一次、顺带说一句。"""
        import pet_acts
        now_s = self.t / 1000.0
        self.last_action_name = name
        if self.anim_cd:
            self.anim_cd.hit(name)
        # 感知介入：专注干活时，两档节奏都拉慢（fidget 10~24 -> 40~70，
        # act 90~240 -> 240~600），说话概率也压到几乎闭嘴。
        focus = self._aware_focus()
        if act:
            self.last_act_group = why
            gap = self._gap("act_gap", 90, 240)
            if focus:
                gap = int(gap * 2.5)
            self.next_act_at = now_s + gap
            # 表演完留一段安静：连着再来个小动作会显得没完没了
            self.next_fidget_at = now_s + max(6, gap * 0.35)
            print(f"[act] {name}（{why}） 下次 {gap}s 后")
        else:
            self.last_fidget_group = why
            gap = self._gap("fidget_gap", 10, 24)
            if focus:
                gap = int(gap * 3.0)
            self.next_fidget_at = now_s + gap
            print(f"[fidget] {name}（{why}） 下次 {gap}s 后")
        # 配了台词的才说，说的概率也压着——一直说话就成噪音了
        prob = 0.4 if act else 0.1
        if focus:
            prob *= 0.2          # 专注时几乎闭嘴
        line = pet_acts.line_for(name, prob=prob)
        if line:
            self.say(line, speak=False, react=False)

    def _gap(self, key, lo, hi):
        # 动作间隔以 anim_config.json 为准；老版本写在 config.json 里的照样认，
        # 免得升级之后用户原来调过的节奏被悄悄重置。
        rng = (self.anim_cfg or {}).get(key) or self.cfg.get(key)
        if (isinstance(rng, (list, tuple)) and len(rng) == 2
                and all(isinstance(x, (int, float)) for x in rng)):
            a, b = int(rng[0]), int(rng[1])
            if a <= b:
                return random.randint(max(1, a), max(2, b))
        return random.randint(lo, hi)

    def _procedural_idle(self):
        """素材缺了才走这里：程序化小动作，保证动作系统不会整个瘫掉。"""
        pool = [("bounce3", 15), ("sway", 13), ("stretch", 11),
                ("nod", 10), ("lean", 9), ("peek", 9),
                ("shake", 6), ("spin", 6), ("doze", 7),
                ("breath_deep", 5), ("jump", 8)]
        pick = random.choices([n for n, _ in pool],
                              weights=[w for _, w in pool], k=1)[0]
        if pick == "jump":
            self.jump_t = 1.0
        else:
            self.play_anim(pick)
        self._maybe_babble()

    def _maybe_babble(self):
        """偶尔边动边说一句。"""
        now_s = self.t / 1000.0
        if (random.random() < 0.22
                and self.t - self.last_speak_tick >= 30_000      # 原来 1500 帧 × 20ms
                and now_s >= self.ambient_hold_until):
            self.last_speak_tick = self.t
            self._say_something()

    def _say_something(self):
        """碎碎念：优先说你最近在忙的事，没料了才回落到原台词池。"""
        inner = random.random() < 0.25
        if self.ambient is not None and self.cfg.get("ambient_memory", True):
            try:
                line = self.ambient.next_line(inner=inner)
            except Exception as e:
                print("[memory] 取话题失败:", e)
                line = None
            if line:
                print(f"[ambient] {line}")
                self.say(line, inner=inner)
                return
        # 心声是「小概率」冒出来的括号灰字，别跟普通台词抢戏
        if inner:
            self.say(random.choice(INNER_LINES), inner=True)
        elif random.random() < 0.15:
            self.say(random.choice(EGG_LINES))
        else:
            self.say(random.choice(LINES))

    def _queue_say(self, text, seconds=2.8, speak=False, anim=None,
                   speech=None, style=None):
        """后台线程调用：只入队，由主线程 tick 统一弹出显示（线程安全）。

        anim 是可选的动画名：通知播报这类场景，说完话要**强制**配个动作，
        走这里直接指定，绕过 _react_anim_for 的「一半概率才配」克制。

        这里进来的都是**外部来源**——桥接喂话、通知播报、积分播报。
        一律走 system 层：不演、不截、停得久，因为里面有必须看清的信息。
        """
        self._say_queue.append((text, seconds, speak, anim, speech, style))

    def _react_anim_for(self, text):
        """说什么话配什么动作——通知播报、桥接对话都走这里。

        这是「性格演绎」最关键的一环：光有台词是配音，配上动作才是反应。
        但有三条克制：
        1. 点它、拖它、睡觉时的动作不能被顶掉（那是被强行打断的交互）
        2. 只在大约一半的时候配，句句都手舞足蹈就假了
        3. 配不上就不配，别硬凑一个无关动作
        """
        if self.sleeping or not self.animlib or not self.available_anims:
            return
        if self.clip in self._SLEEP_CLIPS:
            return
        if self.clip and (self.clip.startswith("click_")
                          or self.clip == "drag"):
            return
        if random.random() > 0.55:
            return
        import pet_acts
        name = pet_acts.pick_for_text(text, self.available_anims)
        if name:
            self.play_clip(name, force=True)

    def say(self, text, inner=False, seconds=None, force=False, speak=None,
            react=None, layer="character", speech=None, style=None):
        """说一句话。

        layer="character"（默认）：角色腔。可以贫嘴，走戏剧化反应。
        layer="system"：系统播报。说人话——不演、不截、停更久，
        因为这类话里有「右键 → 语音 → 设置 Breeze Key」这种必须看清的下一步。
        LLM 生成的聊天回复也走 system：那是别人的话，不该被它的角色腔改写。
        """
        if seconds is None:
            seconds = float(layer_rule(layer, "min_seconds",
                                       5.0 if layer == "system" else 2.8))
        if react is None:
            react = bool(layer_rule(layer, "react", layer != "system"))
        if not force and text == self.last_line and not text.startswith("天气"):
            return
        self.last_line = text
        self.last_attention_t = self.t / 1000.0
        if react:
            self._react_anim_for(text)
        # speak=None 走全局开关（自动碎嘴默认不念，省额度）；True/False 由调用方显式决定
        if speak is None:
            speak = bool(self.cfg.get("tts_ambient", False)) and not inner
        # 一键静音是最后一道闸：所有出声都从 say() 走（桥接播报、通知、日报、
        # 逐条点评、试听），堵这里就等于全堵，不用逐个调用点去改。
        if speak and self.muted:
            speak = False
        # 先处理声音再处理气泡：这是两件事，别互相绑架。
        # 关气泡只是不看字，想「只听不看」的人还得听得见。
        if speak and self.tts is not None:
            # force 表示「你让它说的」，允许重复说同一句；后台自语则去重
            # speech 是念出来那一份（可能带 [停顿] 标签），没有就用显示那份
            self.tts.speak(speech or text, force=bool(force), style=style)
        # 气泡总开关：关了就一个字都不许弹。
        # 这是**唯一出口**——包括 force=True 的强制台词，也包括后来新加的模块
        # （通知、日报、抖音点评、吃文件），谁想绕过它都得先改这里。
        if not self.bubble_on:
            return
        self.bubble_inner = inner
        self.bubble_text = f"（{text}）" if inner else text
        self.bubble_born = self.t / 1000.0   # 气泡弹出动画的起点
        self.bubble_until = self.t / 1000.0 + seconds
        # 语音念完前气泡不许消失，但也不许无限续（万一播放状态卡住）
        # 长播报不设上限：日报一口气能念几分钟，护栏给到预留时长的两倍
        self.bubble_max_until = self.t / 1000.0 + max(60.0, seconds * 2)
        # 显式台词（桥接/菜单）压住自语：至少留 6 秒，气泡长的再多留一会儿
        if force:
            hold = max(6.0, seconds + 3.0)
            self.ambient_hold_until = max(self.ambient_hold_until,
                                          self.t / 1000.0 + hold)
        self.update()

    def check_system_status(self):
            now = self.t

            if now - getattr(self, "last_system_check", 0) < 10000:
                return

            self.last_system_check = now

            cpu = psutil.cpu_percent()

            if cpu >= 90:
                self.say("CPU跑满了，再这样下去我就卡死了")
                return

            ram = psutil.virtual_memory().percent

            if ram >= 95:
                self.say("内存爆了，快关掉几个没用的东西吧，注意，别把我关了")
                return

            if GPU_AVAILABLE:
                try:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(0)

                    temp = pynvml.nvmlDeviceGetTemperature(
                        handle,
                        pynvml.NVML_TEMPERATURE_GPU
                    )

                    if temp > 80:
                        self.say("我感觉我的鱼鳍快熟了")

                except Exception as e:
                    print("GPU读取失败:", e)

    # ---------- 鼠标事件 ----------
    def _fish_hit(self, point):
        for inverse, rect, pix in self._painted_fish:
            local = inverse.map(point)
            if rect.contains(local) and rect.width() > 0 and rect.height() > 0:
                x = min(pix.width()-1, int((local.x()-rect.x()) * pix.width()/rect.width()))
                y = min(pix.height()-1, int((local.y()-rect.y()) * pix.height()/rect.height()))
                if pix.toImage().pixelColor(x, y).alpha() > 32:
                    return True
        return False

    def _drag_paths_of(self, event):
        """这次拖进来的东西能不能吃。只看路径，不碰内容。"""
        from file_feeding import drop_paths
        if self._feed_busy or not event.mimeData().hasUrls():
            return []
        # 拖放源只接收 Copy：删不删由桌宠决定，别让资源管理器再移动/删除一次。
        if not (event.possibleActions() & Qt.DropAction.CopyAction):
            return []
        return drop_paths(event.mimeData().urls())

    def _drag_reset(self, keep_paths=False):
        self._drag_timer.stop()
        self._drag_hover_from = 0.0
        self._drag_armed = False
        self._drag_last_pos = None
        # _auto_eat_paths 不在这里清：dragLeave 会先 reset 再交付，
        # 清早了「到点自己吃」就永远等不到人来收。
        if not keep_paths:
            self._drag_paths = []

    def _held_seconds(self):
        if not self._drag_hover_from:
            return 0.0
        return time.monotonic() - self._drag_hover_from

    def _hold_done(self):
        """压够时间了没有。

        这一条必须自己算，不能只认定时器：拖放期间我们的线程在不停接
        资源管理器的回调，QTimer 什么时候响不由我们定。实测里就是
        「气泡还没来、人也停够了」，松手被判成「手太快」——所以时长
        在松手那一刻现算，定时器只负责弹提示，没有否决权。
        """
        return self._held_seconds() * 1000.0 >= DRAG_HOLD_MS

    def _drag_begin(self, event):
        """光标进窗口：记下能吃的路径，开始计时。"""
        if self._auto_eat_paths:
            # 上一轮到点该吃、却没等到收摊。新拖放来了就先把它交付掉，
            # 否则旧账会卡在 _auto_eat_paths 里，新的一次永远判不成「该吃」。
            self._drag_paths = []
            self._flush_auto_eat()
        self._drag_reset()
        self._drag_paths = self._drag_paths_of(event)
        if not self._drag_paths:
            event.ignore()
            return
        self._drag_hover_from = time.monotonic()
        self._drag_timer.start(DRAG_HOLD_MS)
        print(f"[drag] 进窗口：{len(self._drag_paths)} 项，开始计时")
        # 一开始就接（光标是「可放下」而不是禁止符）——真正要拦的是
        # 「没停够就动手」，不是「这个地方不能放」。没停够时的松手
        # 会在 dropEvent 里被丢掉，源文件不受影响。
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragEnterEvent(self, event):
        self._drag_begin(event)

    def dragMoveEvent(self, event):
        if not self._drag_paths:
            # 有的拖放源不给 enter 直接给 move，这里补一次
            self._drag_paths = self._drag_paths_of(event)
            if self._drag_paths and not self._drag_hover_from:
                self._drag_hover_from = time.monotonic()
                self._drag_timer.start(DRAG_HOLD_MS)
        if not self._drag_paths:
            event.ignore()
            return
        self._drag_hover_tick(event.position())
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def _auto_eat_ms(self):
        """停多久自己动嘴。配置里能改，先保证不早于「松手就吃」那条线。

        配成 0 或负数 = 不自动吃，还按老规矩等松手。
        """
        try:
            want = int(self.cfg.get("drag_auto_eat_ms", DRAG_AUTO_EAT_MS))
        except (TypeError, ValueError):
            want = DRAG_AUTO_EAT_MS
        if want <= 0:
            return 10 ** 9
        return max(DRAG_HOLD_MS, want)

    def _drag_timer_fire(self):
        """定时器只管一件事：手按住不动、没有 move 事件时也能到点。"""
        if not self._drag_paths:
            return
        self._drag_hover_tick(None)
        if self._drag_paths and not self._auto_eat_paths:
            # 50ms 一跳：手不动时全靠它到点，跳太粗「停够了还得再等」。
            self._drag_timer.start(50)

    def _drag_hover_tick(self, pos=None):
        """每次 move 现算：停够了没有、该不该自己吃。

        时长一律现算，不信定时器——拖放期间我们在不停接资源管理器的
        回调，定时器什么时候响不由我们定（实测里就有「停够了但没响」）。
        """
        if not self._drag_paths:
            return
        if pos is not None:
            if self._drag_last_pos is None:
                self._drag_last_pos = pos
            else:
                dx = abs(pos.x() - self._drag_last_pos.x())
                dy = abs(pos.y() - self._drag_last_pos.y())
                self._drag_last_pos = pos
                if dx + dy > DRAG_JITTER_PX and not self._auto_eat_paths:
                    # 还在挪，不算停住。已经决定要吃了就不再回退。
                    self._drag_hover_from = time.monotonic()
                    self._drag_armed = False
                    self._drag_timer.start(DRAG_HOLD_MS)
                    return
        held = self._held_seconds() * 1000.0
        if held >= DRAG_HOLD_MS:
            self._arm_drag()          # 定时器万一没响，靠每次 move 也能到点
        if held >= self._auto_eat_ms():
            self._auto_eat_drag()

    def _arm_drag(self):
        """到点给个提示。只弹一次。"""
        if self._drag_armed or self._feed_busy:
            return
        if not (self._drag_paths and self._hold_done()):
            return
        self._drag_armed = True
        print(f"[drag] 到点解锁（{self._held_seconds():.2f}s）")
        auto = self._auto_eat_ms() < 10 ** 9
        self.say("松手就吃，不松也吃。" if auto else "松手就吃掉。",
                 seconds=3, force=True, speak=False, react=False)

    def _auto_eat_drag(self):
        """停够了就不等松手，自己动手。

        系统只在「松手」那一刻把数据交过来，拖放途中我们把源文件送进
        回收站，资源管理器会把这次拖放判成失败。所以先发一个 Esc——
        这是 Windows 认可的「取消这次拖放」，取消之后源文件还在原处，
        我们再从回收站那条路吃掉它，谁也不用报错。
        """
        if self._auto_eat_paths or self._feed_busy or not self._drag_paths:
            return
        self._auto_eat_paths = list(self._drag_paths)
        print(f"[drag] 到点自己吃：停了 {self._held_seconds():.2f}s，不等松手")
        self._cancel_drag()
        QTimer.singleShot(300, self._flush_auto_eat)

    def _cancel_drag(self):
        """替用户松手（Esc 版）：让拖放源自己收工。"""
        if os.name != "nt":
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.keybd_event(0x1B, 0, 0, 0)      # VK_ESCAPE 按下
            user32.keybd_event(0x1B, 0, 2, 0)      # 松开（KEYEVENTF_KEYUP）
            print("[drag] 已发 Esc，请资源管理器收摊")
        except Exception as exc:                   # noqa: BLE001
            print("[drag] 取消拖放失败，改等松手:", type(exc).__name__, exc)

    def _flush_auto_eat(self):
        """拖放退干净了才动磁盘。

        判据是 _drag_paths 空了——dragLeave 会把它清掉，说明资源管理器
        已经不再问我们要不要接。这时删源文件不会被判成「拖放失败」。
        """
        if not self._auto_eat_paths or self._feed_busy:
            return
        if self._drag_paths:
            # Esc 没被认账、拖放还在进行：不删，等松手走 dropEvent 那条路。
            print("[drag] 拖放没退干净，先不删，等松手")
            return
        paths, self._auto_eat_paths = self._auto_eat_paths, []
        print(f"[drag] 收下：{len(paths)} 项（没松手）")
        self._start_feeding(paths)

    def dragLeaveEvent(self, event):
        self._drag_reset()
        self._flush_auto_eat()

    def dropEvent(self, event):
        if self._auto_eat_paths:
            # Esc 没拦住（某些拖放源不吃这一套），用户松手就当确认。
            paths, self._auto_eat_paths = self._auto_eat_paths, []
            self._drag_reset()
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            print(f"[drag] 松手：本来就该吃（{len(paths)} 项）")
            self._start_feeding(paths)
            return
        if not self._drag_paths:
            self._drag_paths = self._drag_paths_of(event)
        held = self._held_seconds()
        paths = list(self._drag_paths)
        self._drag_reset()
        print(f"[drag] 松手：停了 {held:.2f}s，"
              f"{'吃' if (paths and held * 1000 >= DRAG_HOLD_MS) else '不吃'}"
              f"（{len(paths)} 项）")
        if not paths or held * 1000.0 < DRAG_HOLD_MS:
            # 没停够就松手：什么都不做，也不去碰磁盘。
            event.ignore()
            if paths and not self._feed_busy:
                self.say("手太快了，停在身上半秒再松手。", seconds=4, force=True,
                         speak=False, react=False)
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self._start_feeding(paths)

    def _start_feeding(self, paths):
        """把吃这件事排进下一轮事件循环。

        拖放的 OLE 回调这时还没退干净：起线程、摸磁盘都别在这里做，
        先把控制权还给资源管理器。
        """
        if not paths or self._feed_busy:
            return
        self._feed_busy = True
        QTimer.singleShot(0, lambda: self._begin_feeding(paths))

    def _begin_feeding(self, paths):
        """真开始吃。文件和文件夹一视同仁，不再问。"""
        from file_feeding import names_hint
        names = names_hint(paths)

        def feed():
            from file_feeding import recycle_files, summarize
            # 这里必须一路兜到底：线程如果半路死了，feed_result 不发出来，
            # _feed_busy 就永远卡在 True——之后每一次拖都当「正在吃」被拒，
            # 表现成「吃了没反应、再拖也不吃了」。
            try:
                summary = summarize(paths)
            except Exception as exc:            # noqa: BLE001
                print("[drag] 归类失败，照吃:", type(exc).__name__, exc)
                summary = ""
            try:
                count, failed = recycle_files(paths)
            except Exception as exc:            # noqa: BLE001
                print("[drag] 回收失败:", type(exc).__name__, exc)
                count, failed = 0, [os.path.basename(p) for p in paths]
            print(f"[drag] 收工：成功 {count}，失败 {len(failed)}")
            self.feed_result.emit(count, failed, summary, names)

        threading.Thread(target=feed, name="file-feeding", daemon=False).start()

    def _fed_files(self, count, failed, summary="", names=""):
        self._feed_busy = False
        if count:
            self.eat_t = 1.0
            self.play_clip("eat_onigiri", force=True)
        from file_feeding import fed_line
        bubble, spoken = fed_line(count, failed, summary, names)
        # 这一口必须念出来：老版本写死 speak=False，只弹字不出声，
        # 结果投喂在听觉上等于没发生。气泡带类型摘要，念的时候不带括号。
        self.say(bubble, seconds=6, force=True, speak=True, speech=spoken, react=False)
        if count:
            # 再补一句自己的看法：同一句「吃下了」听十遍就是噪音，
            # 让它看着这批文件的名字和类型自己说。模型不回话也不哑——上面那句照念。
            self._ai_remark(self._fed_fact(count, failed, summary, names), "file")

    @staticmethod
    def _fed_fact(count, failed, summary="", names=""):
        """给模型看的事实句：只给类型和名字，一个字的文件内容都不给。

        它要判断的是「这人刚清掉一批什么东西」，名字加类型就够了。
        真去读内容既慢又贵，而且读完了也只能说一句题外话。
        """
        fact = f"刚吃掉 {summary or f'{count} 个文件'}"
        if names:
            fact += f"（名字：{names}）"
        fact += "，都进了回收站"
        if failed:
            fact += f"；另有 {len(failed)} 个没吃动，留在原地"
        return fact + "。"

    def _dock_at_edge(self, pos):
        if not self.cfg.get("edge_peek", True):
            return False
        screen = QApplication.screenAt(pos) or self.screen() or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        left = self.x() - geo.left()
        right = geo.right() + 1 - self.width() - self.x()
        if min(left, right) > 14:
            return False
        self._edge = "left" if left < right else "right"
        self._peek_until = 0.0
        x = geo.left() if self._edge == "left" else geo.right() + 1 - self.width()
        self.move(x, max(geo.top(), min(self.y(), geo.bottom() + 1 - self.height())))
        self._set_dir("down", 1)
        self.airborne = False
        self.throw_vx = self.throw_vy = 0.0
        self.target = None
        self.update()
        return True

    def toggle_edge_peek(self, on):
        self.cfg["edge_peek"] = on
        if not on:
            self._edge = None
            self.update()
        self.save_config()

    def set_bubble_on(self, on):
        """气泡总开关。关掉立刻收掉正在显示的那条，不留半句话挂在屏幕上。"""
        self.bubble_on = bool(on)
        self.cfg["bubble_on"] = self.bubble_on
        self.save_config()
        if not self.bubble_on:
            self.bubble_text = ""
            self.bubble_until = 0
            self.update()
            print("[bubble] 关了，所有台词只出声不弹字")
        else:
            self.say("气泡开了，我又有字了。", force=True, speak=False, layer="system")

    def show_today(self):
        if self._today_dialog is None:
            from zhengzhou_today import TodayDialog
            self._today_dialog = TodayDialog(self)
        if self._today_dialog.isVisible():
            self._today_dialog.refresh()
        self._today_dialog.show()
        self._today_dialog.raise_()
        self._today_dialog.activateWindow()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.last_press_pos = e.globalPosition().toPoint()
            self.dragging = False
            self.drag_start_pos = e.globalPosition().toPoint()
            self.chat_paused = True
            # 按下去先压一下，手指有反馈才会觉得「这是个活物」
            self.play_anim("squash", 0.26)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and self.drag_start_pos is not None:
            pos_now = e.globalPosition().toPoint()
            delta = pos_now - self.drag_start_pos
            if not self.dragging and delta.manhattanLength() > 6:
                self.dragging = True
                self.drag_offset = self.drag_start_pos - self.pos()
                if self._edge:
                    self._edge = None
                    self.drag_offset.setX(self.width() // 2)
                self._last_drag_xy = None
                if self.sleeping:
                    self.wake_up()
                # 被拎起来：播悬空挣扎那段，循环到放下
                self.play_clip("drag", loop=True, force=True)
            if self.dragging and self.drag_offset is not None:
                pos = pos_now - self.drag_offset
                self.move(pos)
                # 记速度：松手那一刻拿它决定甩多远，拖拽时拿它决定歪多少
                t_now = time.time()
                if self._last_drag_xy is not None:
                    dts = t_now - self._last_drag_t
                    if dts > 0.001:
                        vx = (pos.x() - self._last_drag_xy[0]) / dts
                        vy = (pos.y() - self._last_drag_xy[1]) / dts
                        # 平滑一次，单帧抖动不该变成一记猛甩
                        self.throw_vx = self.throw_vx * 0.45 + vx * 0.55
                        self.throw_vy = self.throw_vy * 0.45 + vy * 0.55
                self._last_drag_xy = (pos.x(), pos.y())
                self._last_drag_t = t_now
                if abs(delta.x()) > 10:
                    self._set_dir("left" if delta.x() < 0 else "right", 1 if delta.x() < 0 else -1)
                self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self.dragging:
                self.dragging = False
                self.drag_offset = None
                self.drag_start_pos = None
                if self.clip == "drag":      # 放下了，别继续挣扎
                    self.clip = None
                speed = (self.throw_vx ** 2 + self.throw_vy ** 2) ** 0.5
                docked = self._dock_at_edge(e.globalPosition().toPoint())
                if not docked and speed > 420:
                    # 甩得够快才飞，轻轻放下的别乱窜
                    cap = 2600.0
                    if speed > cap:
                        self.throw_vx *= cap / speed
                        self.throw_vy *= cap / speed
                    self.airborne = True
                elif not docked:
                    self.throw_vx = self.throw_vy = 0.0
                    self.land_t = 1.0        # 轻放也压一下，不然像瞬移
                    self._set_dir("down", 1)
                self.target = None
                self.rest_until = self.t + random.randint(6000, 14000)
                if not docked and random.random() < 0.5:
                    self.say(random.choice(DRAG_LINES))
                self.chat_paused = False
                self.save_config()
            else:
                self._click_timer.start(280)  # 等双击判定；单击则回嘴+弹聊天面板
            self.last_press_pos = None
            self.drag_start_pos = None
            self._last_drag_xy = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self.quick_panel.set_mute(self.muted)   # 弹出前对一次状态
            self.quick_panel.popup_at(self.x() + self.width() / 2, self.y() + BUBBLE_H)

    def _on_single_click(self):
        """单击：手绘的回应动作 + 一句自己的话，再弹聊天面板。"""
        if self._edge:
            self._peek_until = time.monotonic() + 3
            self.chat_paused = False
            self.update()
            return
        if self.sleeping:
            self.wake_up()
            return
        self.last_attention_t = self.t / 1000.0
        # 被点了就把待机动作往后推：刚回应完立刻又自顾自演节目，很像没听见
        _now = self.t / 1000.0
        self.next_fidget_at = max(self.next_fidget_at, _now + 8)
        self.next_act_at = max(self.next_act_at, _now + 20)
        if self.growth_greeting("点击"):
            return
        clicked = False
        if self.animlib and self.available_anims:
            import pet_acts
            name = pet_acts.pick_click(self.available_anims,
                                       time.localtime().tm_hour)
            if name:
                clicked = self.play_clip(name, force=True)
                line = pet_acts.line_for(name)
                if line and random.random() < 0.55:
                    self.say(line, speak=False, react=False)
        if not clicked and random.random() < 0.7:
            self.jump_t = 1.0
        if not clicked and random.random() < 0.6:
            self.say(random.choice(REACT_LINES))

    def on_food(self, food):
        self.quick_panel.hide()
        self.eat_t = 1.0
        self.jump_t = 0.6
        self.last_attention_t = self.t / 1000.0
        if self.available_anims:
            cand = [a for a in FOOD_ANIM.get(food, [])
                    if a in self.available_anims]
            if cand:
                self.play_clip(random.choice(cand), force=True)
        lines = FOOD_LINES.get(food, ["吃完了。"])
        if not self.growth_greeting("喂食"):
            self.say(random.choice(lines), speak=True, react=False)

    def growth_greeting(self, kind):
        if self.ambient is None:
            return False
        try:
            name = pet_growth.interact(self.ambient.path, kind)
        except (OSError, ValueError) as exc:
            print("[growth] 互动未记录", exc)
            return False
        if name not in ("熟面孔", "老搭档"):
            return False
        clip, line = (("click_wave", "来啦，我认得你。") if name == "熟面孔"
                      else ("trot_happy", "给你留着位置呢，老搭档。"))
        if kind == "喂食":
            line += " 这口我收下了。"
        self.play_growth_clip(clip)
        self.say(line, speak=kind == "喂食", react=False)
        return True

    def play_growth_clip(self, clip):
        if self._am and clip in self._am._usable(self.available_anims, self.anim_cfg):
            self.play_clip(clip, force=True)

    # ---------- 日报 ----------
    def play_daily_report(self):
        """双击面板「播放日报」：今天该知道的事 + 每条收藏的点评，逐段念。

        取数全丢后台：抖音收藏是本地文件（几毫秒），但天气要打网络，
        逐条点评还要调模型（每条 2~3 秒），主线程一卡动画和气泡就全停。
        所以拼装放线程里，拼好再用 report_ready 信号回主线程排队播报。
        """
        if self._report_busy:
            self.say("日报还在拼，等我一下", force=True, seconds=4, layer="system")
            return
        self._report_busy = True
        # 拼装要等天气（最多 4 秒）+ 逐条点评（首次每条 2~3 秒），
        # 气泡给到 8 秒，别让人对着一条空鱼等
        self.say("我看一眼今天……", force=True, seconds=8, speak=False, layer="system")
        threading.Thread(target=self._daily_report_worker, daemon=True).start()

    def _daily_report_worker(self):
        try:
            segs = self._compose_daily_report()
        except Exception as exc:      # noqa: BLE001  播报不能因为某块数据坏掉就哑了
            print("[report] 拼装失败:", exc)
            segs = [f"日报没拼出来：{type(exc).__name__}"]
        self.report_ready.emit(segs or ["今天没什么可报的。"])

    def _compose_daily_report(self):
        """后台线程里跑：基础日报 + 一句收尾判断 + 今天的收藏逐条点评，一段一条。"""
        base = self._build_daily_report()
        segs = [base]
        # 收尾那句放这儿而不是塞进 _build_daily_report：那函数只管「有什么」，
        # 这一句是「所以我怎么看」，混在一起下次改数据源就会顺手动到它。
        close = self._ai_closing(base)
        if close:
            segs.append(close)
        try:
            import douyin_archive as da
            import douyin_take as dt
            items = da.today()
            if items:
                key = dt.load_key(self.cfg.get("ds_api_key"))
                takes = dt.get_takes(items, key)
                segs.extend(self._take_segments(items, takes))
        except Exception as exc:      # noqa: BLE001
            print("[report] 逐条点评跳过:", exc)
            segs.append(f"今天的收藏没点评成：{type(exc).__name__}")
        return segs

    def _ai_closing(self, facts):
        """日报收尾：读过今天这一堆，说一句「所以今天该盯什么」。

        只补看法不复述事实，否则「11月18号星期三」会念两遍。
        拿不到就返回空，日报少一句判断不痛不痒，少一天天气才是问题。
        """
        try:
            import say_ai
            return say_ai.opine(facts, "day", mode="remark",
                                key=self.cfg.get("ds_api_key"), timeout=7.0) or ""
        except Exception as exc:      # noqa: BLE001
            print("[report] 收尾跳过:", type(exc).__name__, exc)
            return ""

    def _take_segments(self, items, takes):
        """把点评拼成能念的段：一条一段，标题 → 要点 → 看法。"""
        try:
            import douyin_take as dt
        except Exception:
            return []
        segs = []
        for i, it in enumerate(items, 1):
            t = takes.get(dt.take_id(it)) or {}
            title = dt._title_of(it) or "一条没标题的"
            point = (t.get("point") or dt.fallback_point(it) or "").strip()
            take = (t.get("take") or "").strip()
            line = f"第{i}条，《{title}》。"
            if point:
                line += point
            if take:
                line += ("。" if line[-1] not in "。！？…" else "") + take
            if line[-1] not in "。！？…":
                line += "。"
            segs.append(line)
        return segs

    def _weather_line(self, budget=4.0):
        """郑州天气，最多等 budget 秒。

        wttr.in 不通时会自动退到 Open-Meteo，两条都慢，实测一次能拖满 7 秒。
        日报是「点了就该出声」的东西，不能为一句天气干等——超时就当没有，
        线程是 daemon，留在后台自己跑完就散。
        """
        cache = getattr(self, "_weather_cache", None)
        if cache and time.monotonic() - cache[0] < 300:
            return cache[1]
        box = {}

        def run():
            try:
                import zhengzhou_today as zt
                box["w"] = zt.weather(zt.city_date(), self.WEATHER_MAP, self.WMO_MAP)
            except Exception as exc:      # noqa: BLE001
                box["e"] = exc

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(budget)
        if "w" not in box:
            if "e" in box:
                print("[report] 天气跳过:", box["e"])
            else:
                print(f"[report] 天气超时（>{budget:.0f}s），跳过")
            return ""
        self._weather_cache = (time.monotonic(), box["w"])
        return box["w"]

    def _build_daily_report(self):
        """拼今天这段口播。每块自己兜异常：一处数据坏了不该让整段哑掉。"""
        import datetime as _dt
        now = _dt.datetime.now()
        parts = [f"{now.month}月{now.day}号，星期{'一二三四五六日'[now.weekday()]}。"]

        # 天气：拿不到就整句跳过，别拿「暂时查不到」占着播报
        w = self._weather_line()
        if w:
            parts.append("郑州" + w.split("（")[0].strip().rstrip("。") + "。")

        # 抖音收藏
        try:
            import douyin_archive as da
            recs = da.load_records()
            today_n = len(da.today(recs))
            if today_n:
                parts.append(f"抖音今天收了 {today_n} 条，累计 {len(recs)} 条。")
            elif recs:
                parts.append(f"抖音今天还没收新的，累计 {len(recs)} 条。")
        except Exception as exc:      # noqa: BLE001
            print("[report] 抖音跳过:", exc)

        return "".join(parts)

    def _on_report_ready(self, segs):
        """后台拼完，回主线程进播报队列。"""
        self._report_busy = False
        if isinstance(segs, str):        # 老调用方传单句也认
            segs = [segs]
        # 手动点的日报直接顶掉旧队列：用户要的是「现在念」
        self.speak_segments(segs, replace=True)
        for s in segs or []:
            print("[report]", s)

    # ---------- 长播报队列 ----------
    def speak_segments(self, segs, replace=False, gap=0.5):
        """一段一段念，这一段念完才上下一段。

        和 _queue_say 的区别：那个是一次性把话全堆出来，气泡互相覆盖，
        只适合「一句话的通知」。日报和逐条点评要一条一条听清，
        所以这里按 tts.speaking 和预计时长排队，前一段没念完不放下一段。
        """
        segs = [str(s).strip() for s in (segs or []) if str(s or "").strip()]
        if not segs:
            return
        if replace:
            self._speech_q = list(segs)
            self._speech_until = 0.0
            self._speech_hard_until = 0.0
        else:
            self._speech_q.extend(segs)
        self._speech_gap = gap
        if self._speech_timer is None:
            self._speech_timer = QTimer(self)
            self._speech_timer.setInterval(400)
            self._speech_timer.timeout.connect(self._pump_speech)
        if not self._speech_timer.isActive():
            self._speech_timer.start()
        self._pump_speech()      # 队列空着就立刻开口，不等下一个 400ms

    def stop_speech(self):
        """手动打断：清队列 + 停声音。"""
        self._speech_q = []
        if self._speech_timer is not None:
            self._speech_timer.stop()
        if self.tts is not None:
            self.tts.stop()
        self.bubble_until = 0
        self.bubble_text = ""
        self.say("不念了。", force=True, seconds=3, speak=False, layer="system")

    def _now(self):
        """播报队列的时间源。抽出来只为一件事：自检能瞬间推进时间，
        不用真等三分钟看它一段一段念完。"""
        return time.monotonic()

    def _pump_speech(self):
        if not self._speech_q:
            if self._speech_timer is not None:
                self._speech_timer.stop()
            return
        now = self._now()
        if now < self._speech_until:
            return
        # 声音还没念完就等它。硬线兜底：speaking 万一卡住，不能永远堵在这
        if self.tts is not None and getattr(self.tts, "speaking", False) and now < self._speech_hard_until:
            return
        text = self._speech_q.pop(0)
        secs = self._speech_seconds(text)
        self.say(text, force=True, seconds=secs, speak=True, layer="system")
        self._speech_until = now + secs + self._speech_gap
        self._speech_hard_until = now + max(90.0, secs * 4)
        # 播报期间压住自说自话，不然念到一半冒一句「今天挺忙啊」很吵
        self.ambient_hold_until = max(self.ambient_hold_until,
                                      self.t / 1000.0 + secs + 4)

    @staticmethod
    def _speech_seconds(text):
        """一段话要念多久。中文口播实测 4~4.5 字/秒，再给合成起步的余量。

        不设上限：用户明确要「长度不受限」，长就让它长，气泡跟着撑到底。
        """
        return max(6.0, len(text) / 4.0 + 3.5)

    def on_yoyo(self):
        """双击面板里的「打开悠悠」：唤起手机上的 YOYO 智能体。

        adb 那一段要 1-3 秒，放主线程会把整个桌宠卡住（动画停、气泡不刷），
        所以丢后台线程，结果用 yoyo_result 信号回主线程。
        """
        self.quick_panel.hide()
        pp = self._phone_panel()
        if pp is None:
            self.say("手机模块没加载，叫不了悠悠", force=True, layer="system")
            return
        if not hasattr(pp, "open_yoyo"):
            self.say("这个版本的手机模块还不支持悠悠", force=True, layer="system")
            return

        pool = [n for n in ("peek", "click_happy")
                if n in self.available_anims]
        if pool:
            self.play_clip(random.choice(pool), force=True)
        self.say("我去叫悠悠", force=True, speak=False, react=False)

        def work():
            try:
                ok, msg = pp.open_yoyo()
            except Exception as e:
                ok, msg = False, f"叫悠悠出错了：{e}"
            print(f"[yoyo] {'OK' if ok else 'FAIL'} {msg}")
            self.yoyo_result.emit(msg, ok)

        threading.Thread(target=work, daemon=True).start()

    def _on_yoyo_result(self, msg, ok):
        """后台唤起完成，回主线程说话。

        成功这一趟**只出气泡、不出人声**：悠悠一起来，手机麦克风就开了，
        这边再念「悠悠在听你说话」等于自己往人家耳朵里喊话，干扰识别。
        所以改成一声「叮」——听得见，又不含人声，不会被当成指令。
        失败照旧念，因为那是要看清/听清的错误信息。
        """
        if ok:
            if self.tts is not None:
                self.tts.stop()   # 上一句还在念也掐掉，别漏进手机
            self.say(msg, force=True, speak=False, react=True, layer="character")
            # 静音连这声「叮」也一起管：不然关了又响，等于没静音
            if ding is not None and not self.muted:
                ding.play()
            return
        self.say(msg, force=True, speak=True, react=False, layer="system")

    def do_random_act(self):
        """手动演一个：按当前时段和情境挑，不是纯随机。

        纯随机会在凌晨三点给你跳宅舞——那就不是性格，是抽奖。
        """
        if self.sleeping:
            self.wake_up()
            return
        if not self.available_anims:
            self.say("动作素材没装好，先跑 bake_anim.py", force=True)
            return
        import pet_acts
        lt = time.localtime()
        name, _why = pet_acts.pick_idle(
            lt.tm_hour, lt.tm_mon, lt.tm_mday, self.available_anims,
            minutes_idle=0.0)
        if not name:
            self.say("这会儿没什么想演的。", force=True)
            return
        self.play_clip(name, force=True)
        line = pet_acts.line_for(name)
        self.say(line or "看着。", force=True, speak=False, react=False)

    WEATHER_MAP = {
        "Sunny": "晴", "Clear": "晴", "Partly cloudy": "多云", "Cloudy": "阴",
        "Overcast": "阴", "Mist": "薄雾", "Fog": "雾", "Haze": "霾",
        "Light rain": "小雨", "Patchy rain nearby": "零星小雨", "Light drizzle": "毛毛雨",
        "Moderate rain": "中雨", "Heavy rain": "大雨", "Light rain shower": "阵雨",
        "Moderate or heavy rain shower": "雷阵雨", "Thundery outbreaks nearby": "雷阵雨",
        "Patchy light rain with thunder": "雷阵雨", "Light snow": "小雪",
        "Moderate snow": "中雪", "Heavy snow": "大雪", "Blizzard": "暴雪",
        "Patchy rain possible": "可能有雨", "Light freezing rain": "冻雨",
    }

    # WMO 天气代码 → 中文
    WMO_MAP = {
        0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
        45: "雾", 48: "冻雾",
        51: "毛毛雨", 53: "毛毛雨", 55: "密毛毛雨",
        56: "冻雨", 57: "冻雨",
        61: "小雨", 63: "中雨", 65: "大雨",
        66: "冻雨", 67: "强冻雨",
        71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
        80: "阵雨", 81: "中阵雨", 82: "强阵雨",
        85: "阵雪", 86: "强阵雪",
        95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴伴冰雹",
    }

    def _weather_wttr(self, city):
        """项目原来的数据源"""
        url = "https://wttr.in/" + urllib.parse.quote(city) + "?format=j1"
        r = requests.get(url, timeout=5, headers={"User-Agent": "dafeiyu-pet"})
        r.raise_for_status()
        cur = r.json()["current_condition"][0]
        raw = cur["weatherDesc"][0]["value"].strip()
        return cur["temp_C"], self.WEATHER_MAP.get(raw, raw)

    def _weather_openmeteo(self, city):
        """兜底数据源：免 Key、国内直连较稳；城市名先做地理编码"""
        geo = self._geo_cache
        if not geo or geo[0] != city:
            g = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                             params={"name": city, "count": 1, "language": "zh", "format": "json"},
                             timeout=6, headers={"User-Agent": "dafeiyu-pet"})
            g.raise_for_status()
            res = g.json().get("results") or []
            if not res:
                raise RuntimeError(f"没找到城市「{city}」")
            self._geo_cache = geo = (city, res[0]["latitude"], res[0]["longitude"])
        _, lat, lon = geo
        f = requests.get("https://api.open-meteo.com/v1/forecast",
                         params={"latitude": lat, "longitude": lon,
                                 "current": "temperature_2m,weather_code", "timezone": "auto"},
                         timeout=6, headers={"User-Agent": "dafeiyu-pet"})
        f.raise_for_status()
        cur = f.json()["current"]
        return round(float(cur["temperature_2m"])), self.WMO_MAP.get(int(cur["weather_code"]), "说不清")

    def _get_weather(self):
        city = (self.cfg.get("city") or "汕头").strip()
        now = self.t / 1000.0
        cache = self._weather_cache
        if cache and cache[0] == city and now - cache[1] < 600:
            self.say(cache[2], seconds=10, force=True, speak=True)
            return

        result = None
        for src in (self._weather_wttr, self._weather_openmeteo):
            try:
                result = src(city)
                break
            except Exception as e:
                print(f"天气源 {src.__name__} 失败:", repr(e))

        if result is None:
            self.say("天气没查到，网络好像不通", seconds=6, force=True,
                     layer="system")
            return

        temp, desc = result
        line = f"{city}今天{temp}°，天气{desc}"
        self._weather_cache = (city, now, line)
        self.say(line, seconds=10, force=True, speak=True)

    

    # ---------- 手机面板 ----------
    def _phone_tick(self):
        """手机自动连接 + 定时提醒的周期逻辑，tick 里每帧都来一遍。

        两个开关完全独立，互不耦合：
          1. phone_autoconnect 只管「开机自动连手机 + 开监听」，只做一次。
          2. phone_remind 只管「没在监听时定时提醒」，跟自动连接无关——
             哪怕你把自动连接关了，只要还开着提醒，它照样会在没监听时提醒。
        监听中途自己死了（进程被回收/计划任务失效），也归到「没在监听」提醒。
        """
        now = time.time()

        # 1. 启动后的那次自动连接（只做一次，成功/失败都算做完）
        if self.cfg.get("phone_autoconnect", True):
            if not self._phone_auto_done and now >= self._phone_auto_at:
                self._phone_auto_done = True
                self._phone_auto_connect()

        # 2. 定时提醒：没在监听就催（独立于自动连接开关）
        if not self.cfg.get("phone_remind", True):
            return
        # 刚启动的十几秒里桥接/桌面还在起，别急着催，给自动连接留点时间
        if now < self._phone_remind_at:
            return
        self._phone_remind_at = now + PHONE_REMIND_GAP
        pp = self._phone_panel()
        if pp is None:
            return
        if pp.notify_running():
            self._phone_remind_last = ""
            return
        # 连接都没成的话，别每次都催「没监听」，先看手机在不在
        ok, msg = pp.link_status()
        if not ok:
            self._maybe_phone_remind("手机没连上，连上后我才能帮你听通知")
            return
        self._maybe_phone_remind("通知监听没在跑，点我右键「手机」里开一下")

    def _phone_auto_connect(self):
        """启动时自动连手机 + 开监听，并反向给手机发提醒。全程后台，不阻塞界面。"""
        pp = self._phone_panel()
        if pp is None:
            return
        print("[phone] 自动连接手机……")

        def work():
            # 连手机
            ok, msg = pp.connect()
            print(f"[phone] 自动连接: {ok} {msg}")
            if not ok:
                return
            # 连上了，反向提醒手机
            ok_p, msg_p = pp.notify_phone("大肥鱼", "大肥鱼已连接")
            print(f"[phone] 发手机提醒(已连接): {ok_p} {msg_p}")
            # 开监听
            ok2, msg2 = pp.notify_start()
            print(f"[phone] 自动监听: {ok2} {msg2}")
            if ok2:
                ok_p2, msg_p2 = pp.notify_phone("大肥鱼", "大肥鱼已潜入你的手机")
                print(f"[phone] 发手机提醒(已潜入): {ok_p2} {msg_p2}")

        threading.Thread(target=work, daemon=True).start()

    def _maybe_phone_remind(self, text):
        """同样的提醒别连着说（每 PHONE_REMIND_GAP 才轮到一次，这里再压一道）。"""
        if self._phone_remind_last == text:
            return
        self._phone_remind_last = text
        self.say(text, force=True, speak=True)

    def _awareness_tick(self):
        """感知模块的周期逻辑：每帧进来，绝大多数直接 return。

        只做两件事：
          1. 刷新状态（focus / away / idle），供 _maybe_idle_action 调节奏用。
          2. 检测「你回来了」的下降沿：上一刻 away、这一刻不是 away，
             就演一个重逢动作（吓一跳 / 开心），这是桌宠情感化的灵魂瞬间。

        刻意不在这里做「静音」——开会静音属于「介入」，放到调度层按状态走，
        这里只负责把状态读出来，不直接改任何动画、任何声音。
        """
        if self._aware is None:
            return
        prev = self._aware_state
        self._aware_state = self._aware.state()
        # 重逢：离开 -> 回来（回到 focus 或 idle 都算回来了）
        if self._aware_were_away and self._aware_state != "away":
            self._aware_were_away = False
            self._on_reunion()
        elif self._aware_state == "away":
            self._aware_were_away = True

    def _on_reunion(self):
        """你回来了：演一个重逢动作。睡着的、正在干活的、被拖的都不打断。"""
        if self.sleeping or self.dragging or self.working:
            return
        import random
        if self.clip in self._SLEEP_CLIPS:
            return
        if self.clip and (self.clip.startswith("click_") or self.clip == "drag"):
            return
        pool = [n for n in ("startle", "click_happy", "peek")
                if n in self.available_anims]
        if pool:
            self.play_clip(random.choice(pool), force=True)

    def _aware_focus(self):
        """是不是正处在「专注中」状态。供调度层调节奏用。"""
        return self._aware is not None and self._aware_state == "focus"

    def _move_stride(self):
        """懒加载步幅标定模块。加载不了返回 None——退回原来的贴图平移。"""
        if getattr(self, "_ms", 0) == 0:
            try:
                import move_stride
                self._ms = move_stride
            except Exception as e:
                self._ms = None
                print("[move] 步幅标定不可用:", e)
        return getattr(self, "_ms", None)

    def _start_move_anim(self):
        """走动时按朝向播移动动画，返回这一 tick 该走的像素；不给就返回 None。

        位移和动画帧用同一个时钟（一圈步幅 ÷ 有效圈时长 × 倍率），
        所以腿摆到哪、身体就该走到哪，不会出现「腿倒腾得飞快、人却在滑」。
        """
        ms = self._move_stride()
        if ms is None:
            return None
        clip = ms.clip_for(self.dir)
        if not clip or not self.animlib or not self.animlib.has(clip):
            return None
        info = ms.timing(clip, self.animlib.seconds(clip), self.cur_h)
        if not info:
            return None
        if self.clip != clip or not self.clip_loop:
            if not self.play_clip(clip, loop=True):
                return None
        # play_clip 会重置 span/rate，所以这两个必须在它之后写
        self.clip_span = info["span"]
        self.clip_rate = info["rate"]
        return info["speed"] * self.tick_ms / 1000.0   # 一 tick 走多远，跟帧长走

    def _stop_move_anim(self):
        """移动结束了：收起循环中的移动动画。

        只认「带帧区间」的 clip——idle / drag / sleep_loop 这些也是循环，
        但它们不该被这里收掉，否则一站定就开始抽。
        """
        if self.clip and self.clip_span:
            self.clip = None
            self.clip_span = None
            self.clip_rate = 1.0

    def _phone_panel(self):
        """延迟加载手机模块，返回 phone_panel 或 None。

        不 import 也能打开菜单（菜单项照常显示，点的时候再报「没装好」），
        避免因为 Escrcpy 没装就把整个右键菜单拖垮。
        """
        if getattr(self, "_pp", None) is None:
            try:
                import phone_panel as pp
                self._pp = pp
            except Exception as e:
                self._pp = False
                print("[phone] 手机模块不可用:", e)
        return self._pp or None

    def _phone_menu(self):
        m = WMenu("手机")
        pp = self._phone_panel()
        if pp is None:
            m.addAction("手机模块没加载（缺 phone_panel.py）").setEnabled(False)
            return m
        if not pp.adb_available():
            m.addAction("没找到 adb（要先装 Escrcpy）").setEnabled(False)
            return m

        # 顶部：一句话说清现在什么状态。
        # 只读快照——这里是主线程，一碰 adb 就得等 8 秒起，整只鱼会冻住
        # （2026-11-18 实测：没连手机时右键卡 9.47 秒，主因是 connect 旧地址超时）。
        snap, running = self._phone_snap_get()
        head = m.addAction(snap or "状态读取中……")
        head.setEnabled(False)

        m.addSeparator()
        # 连接：手输那条是主路——端口每次重开都变、配对码只显示一次，
        # mDNS 和缓存地址都救不回来的时候只有它管用。
        m.addAction("连接手机（无线调试：IP / 端口 / 配对码）",
                    lambda checked=False: self.open_phone_connect())
        m.addAction("自动找一下（mDNS）", lambda checked=False: self.phone_connect())
        m.addAction("断开连接", lambda checked=False: self.phone_disconnect())

        m.addSeparator()
        # 监听（running 也来自快照：psutil 扫全部进程同样是 IO，别放主线程）
        m.addAction("启动通知监听" if not running else "通知监听（正在跑）",
                    lambda checked=False: self.phone_notify_toggle())
        m.addAction("停止通知监听",
                    lambda checked=False: self.phone_notify_stop())

        m.addSeparator()
        m.addAction("现在什么状态", lambda checked=False: self.phone_status())
        m.addAction("看监听日志", lambda checked=False: self.phone_log())

        m.addSeparator()
        # 自动连接 + 提醒开关
        a_auto = m.addAction("开机自动连手机并监听")
        a_auto.setCheckable(True)
        a_auto.setChecked(bool(self.cfg.get("phone_autoconnect", True)))
        a_auto.triggered.connect(lambda on: self.set_phone_autoconnect(on))
        a_rem = m.addAction("没监听时定时提醒我")
        a_rem.setCheckable(True)
        a_rem.setChecked(bool(self.cfg.get("phone_remind", True)))
        a_rem.triggered.connect(lambda on: self.set_phone_remind(on))
        return m

    # ---------------------------------------------------------------- 手机状态快照
    # 规矩：任何会碰 adb 的调用都不许出现在主线程。菜单、气泡、面板都要秒回，
    # 探测一律丢后台线程，结果写进这份快照，界面只读快照。
    _PHONE_SNAP_TTL = 90.0   # 快照多久算过期（秒）

    def _phone_snap_get(self):
        """拿快照（永远不阻塞）。过期了就顺手叫后台刷一次，下次右键就是准的。"""
        stale = time.time() - self._phone_snap_ts > self._PHONE_SNAP_TTL
        if stale and not self._phone_probing:
            self._refresh_phone_snap()
        return self._phone_snap_text, self._phone_snap_running

    def _refresh_phone_snap(self):
        """后台探一次手机状态 + 监听状态。不阻塞，失败也不影响桌宠。"""
        if self._phone_probing:
            return          # 上一次还没回来，别排队
        pp = self._phone_panel()
        if pp is None:
            self._phone_snap_text = "手机模块没加载（缺 phone_panel.py）"
            self._phone_snap_running = False
            self._phone_snap_ts = time.time()
            return
        self._phone_probing = True

        def work():
            try:
                text = pp.full_status()
            except Exception as e:
                text = f"读状态失败：{type(e).__name__}"
            try:
                running = bool(pp.notify_running())
            except Exception:
                running = False
            try:
                self.phone_snap.emit(str(text), running)
            except RuntimeError:
                pass          # 窗口已经销毁，结果没人收，正常

        threading.Thread(target=work, name="phone-snap", daemon=True).start()

    def _on_phone_snap(self, text, running):
        """后台探测回到主线程：只写缓存，不弹、不念。"""
        self._phone_snap_text = text
        self._phone_snap_running = bool(running)
        self._phone_snap_ts = time.time()
        self._phone_probing = False

    def _phone_bg(self, fn, wait_hint=None):
        """把会碰 adb 的操作丢后台：连接、断开、起停监听、读状态都走这里。

        主线程只负责先说一句「等我一会」，事成之后用信号回来说结果。
        adb 没连手机时单次 connect 能卡 8 秒，跑在主线程就是整只鱼冻住。
        """
        if self._phone_busy:
            self.say("还在弄上一条，等我一下", force=True, speak=False, react=False)
            return
        pp = self._phone_panel()
        if pp is None:
            return
        self._phone_busy = True
        if wait_hint:
            self.say(wait_hint, force=True, speak=False, react=False)

        def work():
            try:
                ok, msg = fn(pp)
            except Exception as e:
                ok, msg = False, f"出错了：{type(e).__name__}"
            try:
                self.phone_op_done.emit(str(msg), bool(ok))
            except RuntimeError:
                pass

        threading.Thread(target=work, name="phone-op", daemon=True).start()

    def _on_phone_op_done(self, msg, ok):
        """后台操作回来：报到、说话，顺手让快照失效（下次右键显示新状态）。"""
        self._phone_busy = False
        self._phone_snap_ts = 0.0
        self._phone_say(ok, msg)

    def set_phone_autoconnect(self, on):
        self.cfg["phone_autoconnect"] = bool(on)
        self.save_config()
        if on:
            self._phone_auto_done = False
            self._phone_auto_at = time.time() + 3
        print(f"[phone] 开机自动连接 -> {on}")

    def set_phone_remind(self, on):
        self.cfg["phone_remind"] = bool(on)
        self.save_config()
        self._phone_remind_last = ""
        print(f"[phone] 没监听提醒 -> {on}")

    def _phone_say(self, ok, msg):
        """把操作结果说给用户。ok 用平常语气，失败用提醒语气。"""
        self.say(msg, force=True, speak=bool(ok))
        print(f"[phone] {'OK' if ok else 'FAIL'} {msg}")

    def phone_connect(self):
        # adb 连接最长能磨 14 秒，必须后台；前台只留一句提示
        self._phone_bg(lambda pp: pp.connect(), wait_hint="连一下手机……")

    def open_phone_connect(self):
        """无线调试连接窗口。端口变了、配对失效，只有手输这条能救。"""
        dlg = getattr(self, "_phone_connect_dlg", None)
        if dlg is None:
            try:
                from phone_connect_dialog import PhoneConnectDialog
            except Exception as e:
                self.say(f"连接窗口没加载：{e}", force=True, speak=False)
                return
            dlg = PhoneConnectDialog(self, on_success=self._phone_just_connected)
            self._phone_connect_dlg = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _phone_just_connected(self):
        """连接窗口连上了：快照立刻作废重刷，别让菜单继续显示「没发现手机」。"""
        self._phone_snap_ts = 0.0
        self._phone_snap_text = ""
        self._refresh_phone_snap()
        self.say("手机连上了", force=True, speak=False, react=False)

    def phone_disconnect(self):
        self._phone_bg(lambda pp: pp.disconnect())

    def phone_notify_toggle(self):
        def op(pp):
            if pp.notify_running():
                return pp.notify_stop()
            return pp.notify_start()
        self._phone_bg(op)

    def phone_notify_stop(self):
        self._phone_bg(lambda pp: pp.notify_stop())

    def phone_status(self):
        """「现在什么状态」：答案要现读，但读完再念，界面不许冻。"""
        self._phone_bg(lambda pp: (True, pp.full_status()),
                       wait_hint="我看一眼手机啥情况……")

    def phone_log(self):
        pp = self._phone_panel()
        if pp is None:
            return
        lines = pp.notify_tail(8)
        if not lines:
            self.say("监听日志是空的（可能还没启动过）", force=True, speak=True)
            return
        # 最近一条说给你听，剩下写日志里
        print("[phone] 监听日志：")
        for l in lines:
            print("   ", l)
        self.say(lines[-1], force=True, speak=True)

    # ---------- 动作设置 ----------
    def _anim_menu(self):
        """右键「动作」子菜单：把调度参数摊开，能调的都在这里。

        参考 dsh-pet 的做法——动作池 + 分类权重 + 开关，界面和配置文件两条路。
        界面只放常用的几项，全量参数留给 anim_config.json：
        菜单里塞 109 个动作名没人看得下去，但「哪一类不爱看」是能一眼判断的。
        """
        m = WMenu("动作")
        if not (self._ac and self._am):
            m.addAction("动作设置模块没加载").setEnabled(False)
            return m

        head = m.addAction(self._ac.describe(self.anim_cfg))
        head.setEnabled(False)

        den = m.addMenu("节奏")
        cur = self.anim_cfg.get("density", "normal")
        for key, meta in self._ac.DENSITIES.items():
            a = den.addAction(meta["label"])
            a.setCheckable(True)
            a.setChecked(cur == key)
            a.triggered.connect(
                lambda checked=False, k=key: self.set_anim_density(k))

        cls = m.addMenu("哪类动作")
        for key, label in self._ac.GROUP_LABELS:
            cnt = sum(1 for x in self.available_anims
                      if self._am.group_of(x) == key)
            a = cls.addAction(f"{label}（{cnt}）")
            a.setCheckable(True)
            a.setChecked(self.anim_cfg["group_scale"].get(key, 1) > 0)
            a.triggered.connect(
                lambda on, k=key: self.toggle_anim_group(k, on))

        m.addSeparator()
        for key, label in (
                ("enable_fidget", "小动作：哈欠、张望、撸猫"),
                ("enable_act", "整段表演：吃、玩、乐器"),
                ("enable_fx", "大场面：烟花、鲸鱼、变鸽子"),
                ("enable_festival", "节日动作：写福字、月饼"),
                ("time_of_day", "按时段变化：白天干活、夜里发呆")):
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(bool(self.anim_cfg.get(key, True)))
            a.triggered.connect(lambda on, k=key: self.set_anim_opt(k, on))

        m.addSeparator()
        m.addAction("演一个", lambda checked=False: self.do_random_act())
        if self.last_action_name:
            m.addAction(f"这个不想看（{self.last_action_name}）",
                        lambda checked=False: self.ban_anim(self.last_action_name))
        nban = len(self.anim_cfg.get("banned") or [])
        if nban:
            m.addAction(f"解除屏蔽（{nban} 个）",
                        lambda checked=False: self.clear_ban())

        m.addSeparator()
        m.addAction("重新读取设置", lambda checked=False: self.reload_anim_cfg())
        m.addAction("打开设置文件", lambda checked=False: self.open_anim_cfg())
        return m

    def _save_anim_cfg(self):
        if self._ac:
            self._ac.save(self.anim_cfg)

    def _reschedule(self, force=False):
        """改完设置让新节奏立刻算数。

        force 用在「换节奏」这种明确要它马上变的场景；
        改分类/开关时只用 min 收敛，不然切个复选就立刻蹦一段表演，很突兀。
        """
        now_s = self.t / 1000.0
        fg = self._gap("fidget_gap", 10, 24)
        ag = self._gap("act_gap", 90, 240)
        if force:
            self.next_fidget_at = now_s + fg
            self.next_act_at = now_s + ag
        else:
            self.next_fidget_at = min(self.next_fidget_at, now_s + fg)
            self.next_act_at = min(self.next_act_at, now_s + ag)

    def set_anim_density(self, key):
        if not self._ac:
            return
        self._ac.set_density(self.anim_cfg, key)
        self._save_anim_cfg()
        self._reschedule(force=True)
        label = self._ac.DENSITIES[key]["label"]
        print(f"[anim] 节奏 -> {label}")
        self.say(f"节奏改成「{label}」。", speak=False, react=False)

    def toggle_anim_group(self, key, on):
        self.anim_cfg.setdefault("group_scale", {})[key] = 1.0 if on else 0.0
        self._save_anim_cfg()
        self._reschedule()
        print(f"[anim] 分类 {key} -> {'开' if on else '关'}")

    def set_anim_opt(self, key, on):
        self.anim_cfg[key] = bool(on)
        self._save_anim_cfg()
        self._reschedule()
        print(f"[anim] {key} -> {'开' if on else '关'}")

    def ban_anim(self, name):
        """刚才那个动作以后别演了。写进 banned，重启也还在。"""
        b = self.anim_cfg.setdefault("banned", [])
        if name in b:
            return
        b.append(name)
        self._save_anim_cfg()
        print(f"[anim] 屏蔽 {name}（共 {len(b)} 个）")
        self.say("行，这个不演了。", speak=False, react=False)

    def clear_ban(self):
        self.anim_cfg["banned"] = []
        self._save_anim_cfg()
        print("[anim] 已清空屏蔽列表")
        self.say("屏蔽都解了。", speak=False, react=False)

    def reload_anim_cfg(self, silent=False):
        """手改完文件不用重启——重新读一遍就生效。

        silent=True 给设置面板用：面板自己有提示，别让这句把它盖了。
        """
        if not self._ac:
            return
        old_cd = int(self.anim_cfg.get("cooldown", 36))
        self.anim_cfg = self._ac.load()
        new_cd = int(self.anim_cfg.get("cooldown", 36))
        if self._am and new_cd != old_cd:
            self.anim_cd = self._am.Cooldown(new_cd)
        self._reschedule(force=True)
        if not silent:
            self.say("设置读过了。", speak=False, react=False, layer="system")

    def open_anim_cfg(self):
        import subprocess
        path = self._ac.PATH if self._ac else ""
        if not path or not os.path.exists(path):
            self.say("设置文件还没生成，重启一次就有。", speak=False, react=False)
            return
        try:
            # 不挂控制台：否则每开一次记事本就闪一个黑框
            subprocess.Popen(["notepad.exe", path],
                             creationflags=0x00000008 | 0x08000000)
        except Exception as e:
            print("打开设置文件失败:", e)

    def _build_menu(self):
        """右键菜单：顶层只留「最常点的」，其余收进七组。

        以前 18 个顶层项平铺，想找「亮度」得把整张菜单读一遍。现在按
        互动 / 外观 / 手机 / 抖音 / 语音 / 记忆 分组，
        每组内部按「点了会不会立刻有反应」排。
        """
        # 菜单配色：auto 跟 Windows 的应用主题（深色系统 → 深色菜单）
        set_menu_theme(self.cfg.get("menu_theme", "auto"))
        m = WMenu()

        # ---- 最常点：今天有什么
        m.addAction("郑州今日", self.show_today)
        # ---- 一键收工：点一下直接干完，不弹预览、不要求确认
        try:
            import cleaner  # noqa: F401
            m.addAction("释放资源（清掉挂着的）",
                        lambda checked=False: self.do_cleanup())
        except Exception as exc:      # noqa: BLE001  模块缺了不该打不开整张菜单
            print("[clean] 菜单项跳过:", exc)
        m.addSeparator()

        # ---- 互动：点了立刻有反应
        act = m.addMenu("互动")
        act.addAction("说句话", lambda checked=False: self.say(random.choice(LINES), force=True, speak=True))
        feed_menu = act.addMenu("喂食")
        feed_menu.addAction("拖文件到鱼身上 → 回收站").setEnabled(False)
        for food in FOODS:
            # 注意：Qt 会把 triggered(checked) 的布尔值传进来，必须用默认参数吃掉
            feed_menu.addAction(food, lambda checked=False, f=food: self.on_food(f))
        act.addMenu(self._anim_menu())
        act.addAction("叫醒它" if self.sleeping else "睡觉",
                      lambda checked=False: (self.wake_up() if self.sleeping else self.go_sleep()))
        am = act.addAction("碎碎念跟着对话走")
        am.setCheckable(True)
        am.setChecked(bool(self.cfg.get("ambient_memory", True)))
        am.triggered.connect(lambda on: self.set_ambient_memory(on))

        # ---- 外观：长什么样、摆在哪
        look = m.addMenu("外观")
        mode_menu = look.addMenu("模式")
        for label, key in [("自由散步", "wander"), ("跟随鼠标", "follow"), ("原地待着", "still")]:
            a = mode_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(self.mode == key)
            a.triggered.connect(lambda _, k=key: self.set_mode(k))
        size_menu = look.addMenu("大小")
        for label, mult in SIZE_LEVELS.items():
            a = size_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(abs(self.cur_h - 340 * mult) < 2)
            a.triggered.connect(lambda _, v=mult: self.set_size(v))
        bright_menu = look.addMenu("亮度")
        for label, val in [("原样 0%", 0.0), ("微亮 15%", 0.15), ("亮 25%", 0.25), ("很亮 35%", 0.35)]:
            a = bright_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(abs(self.cfg.get("brightness", 0.25) - val) < 0.01)
            a.triggered.connect(lambda checked=False, v=val: self.set_brightness(v))
        look.addSeparator()
        bb = look.addAction("显示气泡")
        bb.setCheckable(True)
        bb.setChecked(self.bubble_on)
        bb.triggered.connect(self.set_bubble_on)
        peek = look.addAction("屏幕边缘探头")
        peek.setCheckable(True)
        peek.setChecked(self.cfg.get("edge_peek", True))
        peek.triggered.connect(self.toggle_edge_peek)
        # 子菜单每次弹出前自己刷一次状态：勾选值可能是别处改的
        look.aboutToShow.connect(lambda: peek.setChecked(self.cfg.get("edge_peek", True)))
        pa = look.addAction("鼠标穿透（点不到它）")
        pa.setCheckable(True)
        pa.setChecked(self.cfg["passthrough"])
        pa.triggered.connect(lambda on: self.set_passthrough(on))
        ta = look.addAction("窗口置顶")
        ta.setCheckable(True)
        ta.setChecked(self.cfg["topmost"])
        ta.triggered.connect(lambda on: self.set_topmost(on))
        aa = look.addAction("开机自启")
        aa.setCheckable(True)
        aa.setChecked(self.cfg["autostart"])
        aa.triggered.connect(lambda on: self.set_autostart(on))
        look.addSeparator()
        look.addAction("显示/隐藏", self.toggle_visible)

        # ---- 能力入口
        m.addMenu(self._phone_menu())
        m.addMenu(self._douyin_menu())

        # ---- 语音
        voice_menu = m.addMenu("语音")
        # 静音放最上面：找它的人通常是「现在太吵」，不该往下翻
        mu = voice_menu.addAction("一键静音（念诵 + 提示音）")
        mu.setCheckable(True)
        mu.setChecked(self.muted)
        mu.triggered.connect(lambda on: self.set_mute_all(on))
        voice_menu.addSeparator()
        if self.tts is None:
            voice_menu.addAction("语音模块未加载（缺 QtMultimedia）").setEnabled(False)
        else:
            va = voice_menu.addAction("念出台词")
            va.setCheckable(True)
            va.setChecked(bool(self.cfg.get("tts_enabled", True)))
            va.triggered.connect(lambda on: self.set_tts_enabled(on))
            aa2 = voice_menu.addAction("自动碎嘴也念（费额度）")
            aa2.setCheckable(True)
            aa2.setChecked(bool(self.cfg.get("tts_ambient", False)))
            aa2.triggered.connect(lambda on: self.set_tts_ambient(on))
            sub = voice_menu.addMenu("音色")
            for name, vid in TTS_VOICES:
                a = sub.addAction(name)
                a.setCheckable(True)
                a.setChecked(self.tts.voice_id == vid)
                a.triggered.connect(lambda checked=False, v=vid, n=name: self.set_tts_voice(v, n))
            voice_menu.addAction("试听当前音色", lambda checked=False: self.tts_preview())
            sp_menu = voice_menu.addMenu("语速")
            for label, sp in [("慢 0.9", 0.9), ("正常 1.0", 1.0), ("偏快 1.15", 1.15), ("快 1.3", 1.3)]:
                a = sp_menu.addAction(label)
                a.setCheckable(True)
                a.setChecked(abs(float(self.cfg.get("tts_speed", 1.15)) - sp) < 0.01)
                a.triggered.connect(lambda checked=False, v=sp: self.set_tts_speed(v))
            voice_menu.addAction("查询用量", lambda checked=False: self.tts_usage())
            voice_menu.addAction("今日积分 / 去领 400 分",
                                 lambda checked=False: self.credits_report(open_page=True))
            voice_menu.addAction("设置 Breeze Key", self._set_tts_key_dialog)

        # ---- 记忆
        mem = m.addMenu("记忆")
        mem.addAction("它还记着啥", lambda checked=False: self.memory_report())
        if self.ambient:
            mem.addAction("记忆与成长", self.open_memory_panel)
        # 推测是「它觉得」，跟记忆不是一回事；手动试一句，绕过节流
        mem.addAction("现在推一句（试试推测）",
                      lambda checked=False: self._hunch_tick(manual=True))

        # ---- 说话前想一下（人设 + 记忆库 + 内容）
        # 开关和预算在 ai_talk.json，这里放个能点的入口，不用去翻文件。
        think = m.addMenu("说话前想一下")
        try:
            import say_ai
            c = say_ai.cfg()
            a = think.addAction("总闸（关了就全用模板）")
            a.setCheckable(True)
            a.setChecked(bool(c.get("enabled", True)))
            a.triggered.connect(lambda on: self.set_ai_talk(on))
            for key, label in (("notify", "手机通知"), ("day", "日报收尾"),
                               ("file", "吃文件后补话")):
                sc = think.addAction(label)
                sc.setCheckable(True)
                sc.setChecked(bool((c.get("scopes") or {}).get(key, True)))
                sc.triggered.connect(lambda on, k=key: self.set_ai_scope(k, on))
            think.addSeparator()
            st = say_ai.stats()
            think.addAction(f"今天说了 {st['calls']} 条，约 {st['yuan']:.3f} 元").setEnabled(False)
            think.addAction("打开设置文件（模型 / 预算）",
                            lambda checked=False: webbrowser.open(say_ai.CFG_PATH))
        except Exception as exc:      # noqa: BLE001  模块缺了不该打不开菜单
            print("[ai] 菜单项跳过:", exc)

        m.addSeparator()
        m.addAction("设置…（人设 / 语音 / 移机）",
                    lambda checked=False: self.open_settings())
        m.addAction("退出", self.quit_app)
        return m

    # ---------- 收工 ----------
    def do_cleanup(self):
        """一键清掉挂着没在用的进程。

        判定、白名单、硬保护全在 cleaner.py。这里只管「点完立刻有反应」：
        先吭一声，后台干活，干完播报。遍历进程 + 枚举窗口要几百毫秒，
        不值得冻住界面陪它等。
        """
        if self._cleaning:
            return
        self._cleaning = True
        self.say("收工，我看一眼谁在偷懒。", force=True, speak=False, layer="system")

        def _work():
            try:
                import cleaner
                # 桌宠一开几个小时，sys.modules 里那份 cleaner 可能是旧版。
                # fresh() 只在磁盘文件真改过时才重载，改完代码不用重启。
                cleaner = cleaner.fresh()
                line = cleaner.say_line(cleaner.run())
            except Exception as exc:      # noqa: BLE001  收工失败不该让桌宠出事
                print("[clean] 失败:", exc)
                line = "收工没干成，等会儿再试。"
            self._cleaning = False
            self.cleanup_done.emit(line)

        threading.Thread(target=_work, name="cleanup", daemon=True).start()

    def _on_cleanup_done(self, line):
        self.say(line, force=True, speak=False, layer="system")

    # ---------- 说话前想一下 ----------
    def _ai_cfg_write(self, patch):
        """改 ai_talk.json 里的一小块，别的键原样写回。

        写入即生效——say_ai 按 mtime 热加载，不用重启桌宠，也不用重启通知监听。
        """
        try:
            import say_ai
            path = say_ai.CFG_PATH
        except Exception as exc:      # noqa: BLE001
            print("[ai] 写配置失败:", exc)
            return False
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                d = {}
        except (OSError, ValueError):
            d = {}
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(d.get(k), dict):
                d[k].update(v)
            else:
                d[k] = v
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return True
        except OSError as exc:
            print("[ai] 写配置失败:", exc)
            return False

    def set_ai_talk(self, on):
        ok = self._ai_cfg_write({"enabled": bool(on)})
        if not ok:
            self.say("设置没写进去，等下再看。", force=True, layer="system")
            return
        self.say("以后先想一下再说" if on else "不琢磨了，直接用老话术",
                 force=True, speak=False, layer="system")

    def set_ai_scope(self, key, on):
        if self._ai_cfg_write({"scopes": {key: bool(on)}}):
            print(f"[ai] scope {key} -> {on}")

    # ---------- 语音（BreezeBlue TTS） ----------
    @property
    def muted(self):
        return bool(self.cfg.get("mute_all", False))

    def toggle_mute(self):
        self.set_mute_all(not self.muted)

    def set_mute_all(self, on):
        """一键静音：把「出声」这条路整个掐掉。

        只掐声音，不掐气泡——静音是别吵到旁边人，不是不想看它说话。
        也**不动 tts_enabled**：那是用户对语音的长期偏好，静音是临时闸门。
        两者混在一起会出岔子——静音时把偏好改成关，解除静音就永远不出声了。
        """
        on = bool(on)
        if on == self.muted:
            self.quick_panel.set_mute(on)   # 状态没变也得刷按钮，别让界面滞后
            return
        self.cfg["mute_all"] = on
        if on:
            if self.tts is not None:
                self.tts.stop()
            # 排着队的播报一起清掉，否则解除静音时又从半句话开始念
            self._speech_q = []
            if self._speech_timer is not None:
                self._speech_timer.stop()
        self.save_config()
        self.quick_panel.set_mute(on)
        self.tray.setToolTip(TRAY_TIP_MUTED if on else TRAY_TIP)
        self.say("行，我闭嘴。" if on else "能出声了。",
                 force=True, speak=False, seconds=4, layer="system")

    def set_tts_enabled(self, on):
        self.cfg["tts_enabled"] = bool(on)
        if on and self.muted:
            # 用户明确要声音了，静音闸门得跟着抬起来——
            # 否则菜单勾了「念出台词」却一句不响，看着就是坏了
            self.cfg["mute_all"] = False
            self.quick_panel.set_mute(False)
            self.tray.setToolTip(TRAY_TIP)
        self.save_config()
        if not on and self.tts is not None:
            self.tts.stop()
        self.say("语音开了，我出声了" if on else "语音关了，只动嘴不出声",
                 force=True, speak=False, layer="system")

    def set_tts_ambient(self, on):
        self.cfg["tts_ambient"] = bool(on)
        self.save_config()
        self.say("自己碎嘴也会念了" if on else "自己碎嘴不念了", force=True, speak=False)
    def set_tts_voice(self, vid, name=None):
        self.cfg["tts_voice"] = vid
        self.save_config()
        if self.tts is not None:
            self.tts.stop()
        self.say(f"换成了{name or vid}，新音色得重新攒几句", force=True, speak=True)

    def set_tts_speed(self, sp):
        old = float(self.cfg.get("tts_speed", 1.15))
        self.cfg["tts_speed"] = float(sp)
        self.save_config()
        if self.tts is not None:
            self.tts.stop()
        # 语速是缓存 key 的一部分，改了就等于换了批音频，得先说清楚
        if abs(old - float(sp)) > 0.001:
            self.say(f"语速 {old:g} → {float(sp):g}，头几句得重新合成", force=True, speak=True)
        else:
            self.say("语速调好了", force=True, speak=True)

    def tts_preview(self):
        if self.tts is None:
            return
        if not self.tts.key:
            self.say("还没填 Breeze Key：右键 → 语音 → 设置 Breeze Key",
                     force=True, speak=False, layer="system")
            return
        self.say(f"我是{self.tts.voice_name}，这么说话你听得惯吗", force=True, speak=True)

    def tts_usage(self):
        if self.tts is None:
            return
        # 本地这份账本先拿出来：命中缓存多少次、真花钱多少次
        st = self.tts.stats()
        local = (f"这次开机缓存顶了 {st['cache_hits']} 次，真花钱 {st['api_calls']} 次"
                 f"（命中率 {st['hit_rate']:g}%，缓存 {st['cache_files']} 句）")

        def work():
            d = self.tts.usage()
            if not d:
                self._queue_say(f"用量没查到，可能网络或 Key。{local}", 9.0, False)
                return
            s = d.get("summary") or {}
            cost = s.get("total_cost") or 0
            self._queue_say(
                f"近 30 天念了 {s.get('request_count', 0)} 句，花 {cost:g} 积分。{local}",
                10.0, False)
        threading.Thread(target=work, daemon=True).start()

    # ---------- 每日积分守门 ----------
    def _credits_tick(self):
        """主线程：该查就查，该催就催。查在后台线程，说话只在这条线程。"""
        if not CREDITS_AVAILABLE or self.tts is None or not self.tts.key:
            return

        st = self._credits_fresh
        if st is not None:
            self._credits_fresh = None
            self._credits_state = st
            if st.get("ok"):
                print(f"[credits] 余额 {st.get('balance')} | 今天领了={st.get('claimed_today')}"
                      f" | 该催={st.get('should_nag')}")
            else:
                print(f"[credits] 查询失败: {st.get('reason')}")
            if st.get("should_nag"):
                self._credits_nag(st)
            elif st.get("critical"):
                today = time.strftime("%Y-%m-%d")
                if self._credits_low_day != today:
                    self._credits_low_day = today
                    self.say(f"卡里只剩 {st.get('balance', 0):g} 分，今天的分领过了，"
                             f"接下来省着点用，不然明天就哑了",
                             force=True, seconds=10, speak=True)
            return

        now = time.time()
        if self._credits_busy or now < self._credits_next:
            return
        self._credits_next = now + CREDITS_CHECK_EVERY
        self._credits_busy = True
        key = self.tts.key

        def work():
            try:
                self._credits_fresh = daily_claim_status(key)
            except Exception as e:
                print("[credits] 查询异常:", e)
                self._credits_fresh = {"ok": False, "reason": str(e), "should_nag": False}
            finally:
                self._credits_busy = False

        threading.Thread(target=work, daemon=True).start()

    def _credits_nag(self, st):
        """未领才说话；冷却与每日上限都在这里管。"""
        today = time.strftime("%Y-%m-%d")
        if self._credits_nag_day != today:
            self._credits_nag_day = today
            self._credits_nag_n = 0

        gap = nag_interval_minutes(st) * 60
        if time.time() - self._credits_nag_at < gap:
            return
        if self._credits_nag_n >= CREDITS_NAG_MAX:
            return

        self._credits_nag_at = time.time()
        self._credits_nag_n += 1
        print(f"[credits] 第 {self._credits_nag_n} 次催签到")
        lead = ["喂，", "先说正事：", "哎——", "打扰一下，", "提醒你个事："]
        self.say(random.choice(lead) + credits_line(st), force=True, seconds=14, speak=True)

    def memory_report(self):
        """右键菜单：它到底记着哪些事、记到什么程度。"""
        if self.ambient is None:
            self.say("记忆模块没加载上", force=True)
            return
        try:
            s = self.ambient.summary()
            prefs = self.ambient.preferences()
        except Exception as e:
            self.say("记忆读不出来，文件可能坏了", force=True)
            print("[memory] 读取失败:", e)
            return
        tail = f"；另外记着你 {len(prefs)} 条偏好" if prefs else ""
        self.say(f"我记着：{s}{tail}", force=True, seconds=14, speak=False)
        print("[memory] report:", s, "| prefs:", [p["text"] for p in prefs])

    def open_memory_panel(self):
        from memory_panel import MemoryDialog
        try:
            panel = MemoryDialog(self, self.ambient.path)
            panel.exec()
            panel.deleteLater()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "记忆读取失败", str(exc))

    # ---------- 抖音收藏（归档流水线 → 桌宠） ----------
    def _announce_new_items(self, items):
        """新收藏到了：先吭一声，再后台逐条点评，点评接在后面念。"""
        self.speak_segments([f"新收藏 {len(items)} 条，我看完再说。"], replace=False)
        threading.Thread(target=self._takes_worker, args=(items,), daemon=True).start()

    def _takes_worker(self, items):
        """后台跑点评：每条 2~3 秒、还要打网络，绝不能占主线程。"""
        try:
            import douyin_take as dt
            key = dt.load_key(self.cfg.get("ds_api_key"))
            takes = dt.get_takes(items, key)
            segs = self._take_segments(items, takes)
        except Exception as exc:      # noqa: BLE001
            print("[take] 批处理失败:", exc)
            segs = [f"这批收藏我没看明白：{type(exc).__name__}"]
        if segs:
            self.takes_ready.emit(segs)

    def _on_takes_ready(self, segs):
        # 追加而不是替换：前面那句「我看完再说」可能正在念
        self.speak_segments(segs, replace=False)

    def _ai_remark(self, fact, kind="notice", extra=""):
        """事实已经说过了，后台补一句看法，回主线程接着念。

        为什么不在这里同步等：拖进来一个文件就是「立刻要反应」的动作。
        模型慢起来两三秒，主线程一等，动画和右键全卡住。所以先说话，看法随后补。
        补不上也不影响——事实已经听到了。
        """
        def work():
            try:
                import say_ai
                line = say_ai.opine(fact, kind, extra=extra, mode="remark",
                                    key=self.cfg.get("ds_api_key"), timeout=8.0)
            except Exception as exc:      # noqa: BLE001
                print("[ai] 补话失败:", type(exc).__name__, exc)
                return
            if line:
                self.remark_ready.emit(line)

        threading.Thread(target=work, name="ai-remark", daemon=True).start()

    def _on_remark_ready(self, line):
        """看法回来了，排到队尾念——正在念的那句不打断。"""
        if line:
            self.speak_segments([line], replace=False)

    # ---------------- 推测感知 ----------------
    def _hunch_has_outlet(self):
        """推测有没有出口：弹气泡，或者念出来，至少占一样。

        say() 是「先出声、再判气泡」——关气泡只是不看字，只听不看的人
        还得听得见。所以气泡关掉不等于推测白说，别一看到 bubble_on
        是假就当成没出口。
        """
        if self.bubble_on:
            return True
        return bool(self.cfg.get("tts_ambient", False)) and self.tts is not None

    def _hunch_tick(self, manual=False):
        """十分钟一次：该不该推一句话。

        问一次不花钱，真正调模型才花。所以先 due() 过一遍节流
        （今天说够几条、距上次多久），过了才起线程。

        manual=True 是右键「现在推一句」：绕过节流，但要说清为什么少说，
        否则用户点一下没反应，会以为功能坏了。
        """
        if self._hunch_busy or not MEM_AVAILABLE:
            if manual:
                self.say("上一条还在想，等两秒再点。", seconds=4, force=True,
                         speak=False, react=False)
            return
        if not self._hunch_has_outlet():
            # 气泡关着、念诵也关着：一句话都到不了用户眼前，别调模型白花钱。
            # 手动点时告诉他一声，不然像是点了没反应。
            print("[hunch] 气泡和念诵都关着，不推")
            if manual:
                try:
                    self.tray.showMessage("大肥鱼", "气泡关了，念诵也关着，推了你也收不到。\n"
                                                      "外观 → 显示气泡，或语音里打开念诵。",
                                          QSystemTrayIcon.MessageIcon.Information, 6000)
                except Exception as exc:      # noqa: BLE001
                    print("[hunch] 托盘提示失败:", type(exc).__name__, exc)
            return
        try:
            import pet_hunch
            ok, why = pet_hunch.due()
        except Exception as exc:          # noqa: BLE001
            print("[hunch] 跳过:", type(exc).__name__, exc)
            if manual:
                self.say(f"推测模块没加载：{type(exc).__name__}", seconds=6,
                         force=True, layer="system", speak=False, react=False)
            return
        if not ok and not manual:
            # 以前这里是静默 return，日志里什么都看不到，
            # 排查时只能靠猜。原因必须落一行。
            print(f"[hunch] 不说：{why}")
            return
        if manual:
            print(f"[hunch] 手动触发（{why or '节流放行'}）")
        self._hunch_busy = True
        self._hunch_manual = bool(manual)
        threading.Thread(target=self._hunch_worker, name="hunch", daemon=True).start()

    def _hunch_worker(self):
        try:
            import pet_hunch
            got = pet_hunch.sniff(force=bool(getattr(self, "_hunch_manual", False)))
            if got:
                self.hunch_ready.emit(got["text"])
            elif getattr(self, "_hunch_manual", False):
                self.say("这会儿没什么可推的，材料不够。", seconds=5, force=True,
                         speak=False, react=False)
        except Exception as exc:          # noqa: BLE001  推不出来就算了，别弄死桌宠
            print("[hunch] 线程挂了:", type(exc).__name__, exc)
        finally:
            self._hunch_busy = False
            self._hunch_manual = False

    def _on_hunch(self, text):
        """推测出来了：说一句，记下说过的时刻（节流靠它）。"""
        if not text:
            return
        if not self._hunch_has_outlet():
            # 线程跑完这几秒里用户可能刚把两个开关都关了：这时别记账，
            # 免得白占掉「今天说够了」的一个名额。
            print("[hunch] 出口被关了，这句不记账")
            return
        try:
            import pet_hunch
            pet_hunch.mark_said(text)
        except Exception as exc:          # noqa: BLE001
            print("[hunch] 记账失败:", type(exc).__name__, exc)
        self.say(text, seconds=9, force=True, layer="character", react=True)

    def review_today(self):
        """右键「逐条点评今天的」：不念日报，只听点评。手动测试用。"""
        try:
            import douyin_archive as da
            items = da.today()
        except Exception as exc:      # noqa: BLE001
            self.say(f"读不到收藏：{type(exc).__name__}", force=True, layer="system")
            return
        if not items:
            self.say("今天还没收藏，没得点评。", force=True, layer="system")
            return
        self.say(f"今天 {len(items)} 条，我看看。", force=True, seconds=4,
                 speak=False, layer="system")
        threading.Thread(target=self._takes_worker, args=(items,), daemon=True).start()

    def _douyin_tick(self):
        """每分钟一次：有新收藏就播报；到点做每日汇报。

        直接读同机 records.jsonl，不依赖看板服务是否在跑。
        首次安装当天不立刻汇报，避免一启动就出声打扰；之后每天到点汇报一次。
        """
        try:
            import douyin_archive as da
        except Exception as exc:
            print("[douyin] 模块不可用:", exc)
            return
        try:
            cfg = da.load_config()
            if not cfg.get("enabled", True):
                return
            state = da.load_state()
            first_run = not state.get("last_check")
            today_str = time.strftime("%Y-%m-%d")
            dirty = False

            # 1) 每日固定汇报：跟手动日报走同一条流水线（基础日报 + 逐条点评）
            if first_run:
                if not state.get("last_report_date"):
                    state["last_report_date"] = today_str
                    dirty = True
            elif da.daily_report_due(state, cfg):
                state["last_report_date"] = today_str
                dirty = True
                if self._report_busy:
                    print("[douyin] 日报正在拼，这次定时汇报跳过")
                else:
                    self._report_busy = True
                    threading.Thread(target=self._daily_report_worker, daemon=True).start()
                    print("[douyin] 每日汇报（逐条点评）")

            # 2) 新收藏：按 poll_minutes 节流（首次只做种子，不播报存量）
            gap = max(1, int(cfg.get("poll_minutes", 5))) * 60
            if first_run or time.time() - float(state.get("last_check") or 0) >= gap:
                new, state = da.check_new(state, da.load_records(cfg))
                if new and cfg.get("announce_new", True) and da.can_announce(state, cfg):
                    da.bump_announce(state)
                    self._announce_new_items(new)
                    print("[douyin] 新收藏，逐条点评:", len(new))
                da.mark_seen(state, new)
                dirty = True

            if dirty:
                da.save_state(state)
        except Exception as exc:
            print("[douyin] tick 失败:", exc)

    def _douyin_menu(self):
        """右键「抖音收藏」：今天的、最近的、汇报、面板、开关。"""
        m = WMenu("抖音收藏")
        try:
            import douyin_archive as da
        except Exception as exc:
            m.addAction(f"模块没加载（{type(exc).__name__}）").setEnabled(False)
            return m
        try:
            items = da.load_records()
            today = da.today(items)
            cfg = da.load_config()
        except Exception as exc:
            m.addAction(f"读取失败：{type(exc).__name__}").setEnabled(False)
            return m

        head = m.addAction(f"今天 {len(today)} 条 · 累计 {len(items)} 条")
        head.setEnabled(False)
        m.addSeparator()

        t_menu = m.addMenu(f"今天的收藏（{len(today)}）")
        if not today:
            t_menu.addAction("今天还没收藏").setEnabled(False)
        else:
            for it in today[:10]:
                act = t_menu.addAction(da.short_title(it, 26))
                # Qt 的 triggered(checked) 必须用默认参数吃掉
                act.triggered.connect(lambda checked=False, x=it: self._douyin_item(x))

        r_menu = m.addMenu("最近 5 条")
        for it in da.recent(5, items):
            act = r_menu.addAction(da.short_title(it, 26))
            act.triggered.connect(lambda checked=False, x=it: self._douyin_item(x))

        m.addSeparator()
        m.addAction("让它汇报一下", lambda checked=False: self._douyin_report())
        m.addAction("逐条点评今天的（念出来）", lambda checked=False: self.review_today())
        m.addAction("停一下，别念了", lambda checked=False: self.stop_speech())
        m.addAction("打开收藏面板", lambda checked=False: self.open_douyin_panel())
        m.addAction("打开看板", lambda checked=False: self._douyin_open_board())
        m.addSeparator()

        an = m.addAction("主动播报新收藏")
        an.setCheckable(True)
        an.setChecked(bool(cfg.get("announce_new", True)))
        an.triggered.connect(lambda on: self._douyin_toggle("announce_new", on))

        dr = m.addAction(f"每日汇报（{cfg.get('daily_report_at', '12:00')}）")
        dr.setCheckable(True)
        dr.setChecked(bool(cfg.get("daily_report", True)))
        dr.triggered.connect(lambda on: self._douyin_toggle("daily_report", on))

        sp = m.addAction("念出声（TTS）")
        sp.setCheckable(True)
        sp.setChecked(bool(cfg.get("speak", True)))
        sp.triggered.connect(lambda on: self._douyin_toggle("speak", on))

        m.addAction("设置（打开配置）", lambda checked=False: self._douyin_open_config())
        return m

    def _douyin_item(self, it):
        """点某条：打开视频，并让鱼念一句标题。"""
        url = str(it.get("url") or "")
        if url:
            try:
                webbrowser.open(url)
            except Exception as exc:
                print("[douyin] 打开链接失败:", exc)
        try:
            import douyin_archive as da
            line = f"打开这条：{da.short_title(it, 22)}"
        except Exception:
            line = "打开这条了"
        self.say(line, force=True, seconds=6, layer="system")

    def _douyin_report(self):
        """右键：马上汇报今天收藏概况（手点一定念出声）。"""
        try:
            import douyin_archive as da
            text = da.summary_text("today")
        except Exception as exc:
            self.say("抖音归档读不出来，文件可能坏了", force=True, layer="system")
            print("[douyin] report 失败:", exc)
            return
        self.say(text, force=True, seconds=16, speak=True, layer="system")
        print("[douyin] report:", text)

    def open_douyin_panel(self):
        try:
            from douyin_panel import DouyinDialog
            panel = DouyinDialog(
                self,
                on_say=lambda t: self.say(t, force=True, seconds=16, speak=True,
                                          layer="system"),
            )
            panel.exec()
            panel.deleteLater()
        except Exception as exc:
            QMessageBox.warning(self, "抖音收藏面板打不开", f"{type(exc).__name__}: {exc}")

    def _douyin_toggle(self, key, on):
        try:
            import douyin_archive as da
            da.save_config({key: bool(on)})
            label = {"announce_new": "主动播报新收藏",
                     "daily_report": "每日汇报",
                     "speak": "念出声"}.get(key, key)
            self.say(f"{label}{'开了' if on else '关了'}", force=True, seconds=5,
                     layer="system")
        except Exception as exc:
            print("[douyin] 保存配置失败:", exc)

    def _douyin_open_board(self):
        """打开看板：服务没起就先起，服务跟着页面生死走。

        以前这里只有一句 webbrowser.open，服务没在跑的时候用户看到的是
        「无法访问此网站」，只能自己跑去双击 .cmd 才能用。

        整个流程（探活 + 起 node + 等就绪）全部丢后台：这台机器连一个没人听的
        本地端口要等 2 秒才回拒绝，探活本身就能把主线程拖住。
        """
        try:
            import board_link as bl
        except Exception as exc:
            self.say(f"看板模块没加载（{type(exc).__name__}）", force=True, layer="system")
            print("[board] import 失败:", exc)
            return
        self.say("正在开看板……", force=True, seconds=4, layer="system")
        bl.open_board(on_done=lambda ok, msg: self.board_op_done.emit(ok, msg))

    def _on_board_op_done(self, ok, msg):
        """后台起完看板：成了就不吵（浏览器已经弹出来了），失败才说话。"""
        print("[board]", "ok" if ok else "fail", msg)
        if not ok:
            self.say(f"看板没起来：{msg}", force=True, seconds=10, layer="system")


    def _douyin_open_config(self):
        try:
            import douyin_archive as da
            if not os.path.exists(da.CONFIG_PATH):
                da.save_config({})
            os.startfile(da.CONFIG_PATH)  # Windows 打开配置文件
        except Exception as exc:
            QMessageBox.warning(self, "打不开配置", str(exc))

    def set_ambient_memory(self, on):
        """碎碎念跟不跟对话走。关掉就只用原来那批固定台词。"""
        self.cfg["ambient_memory"] = bool(on)
        self.save_config()
        if on and self.ambient is not None:
            self.say("行，我记着你最近在忙什么了", force=True, seconds=5, speak=False)
        elif on:
            self.say("话题记忆没加载上，先用老台词", force=True, seconds=5, speak=False)
        else:
            self.say("好，我闭嘴，只说老台词", force=True, seconds=5, speak=False)

    def credits_report(self, open_page=False):
        """右键菜单入口：马上查一次，把结果念出来。"""
        if self.tts is None:
            self.say("语音模块没加载，积分也查不了", force=True, layer="system")
            return
        if not self.tts.key:
            self.say("还没填 Breeze Key，右键 → 语音 → 设置 Breeze Key",
                     force=True, layer="system")
            return
        if open_page:
            webbrowser.open(CREDITS_URL)
        self.say("我看一眼卡里还剩多少", force=True, seconds=4, speak=False)
        key = self.tts.key

        def work():
            st = daily_claim_status(key)
            self._credits_state = st
            self._queue_say(credits_line(st), 12.0, True)

        threading.Thread(target=work, daemon=True).start()

    def _set_tts_key_dialog(self):
        key, ok = QInputDialog.getText(
            self,
            "Breeze Key",
            "粘贴 BreezeBlue 的 API Key（brz_ 开头）：\n多个 Key 用逗号分隔，可轮流消耗额度：",
            QLineEdit.EchoMode.Password,
            self.cfg.get("tts_key", ""),
        )
        if not ok:
            return
        self.cfg["tts_key"] = key.strip()
        self.save_config()
        if key.strip():
            self.say("Key 存好了，试听一句", force=True, speak=True, layer="system")
        else:
            self.say("Key 清空了，我不会出声了", force=True, speak=False,
                     layer="system")

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Context:
            # 托盘右键：位置取光标（托盘那一角的右下方向会自适应夹进屏幕）
            self._pop_menu(self._build_menu(), QCursor.pos())
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visible()

    def _pop_menu(self, m, pos):
        """弹出右键菜单。

        位置由 wb_menu 自己算：主菜单贴着光标出现（贴右边时也只是把右缘对齐
        屏幕，不从光标处跳走），每级子菜单单独挑放得下的一侧展开，所以
        「贴右边 → 子菜单折叠 → 压住父菜单 → 选不中」这条链子断了。
        菜单必须留引用，否则 Python 一回收，菜单自己就没了。
        """
        old = getattr(self, "_live_menu", None)
        if old is not None and old is not m:
            # 每次右键都是新建的一棵树，上一棵用完就该放掉，不然越点越多
            try:
                old.deleteLater()
            except RuntimeError:
                pass
        self._live_menu = m
        m.exec(pos)

    def contextMenuEvent(self, e):
        self._pop_menu(self._build_menu(), e.globalPos())

    # ---------- 功能 ----------
    def apply_settings(self, cfg):
        """设置面板保存后走这里：内存和磁盘一起改，能立刻生效的立刻生效。

        关键：必须就地 update，不能 self.cfg = cfg。
        TTS 拿的是这个 dict 的引用，换成新对象它还在读旧配置——
        双 key 那次数据被覆写就是这么丢的，这里不再踩第二次。
        """
        before_voice = self.cfg.get("tts_voice")
        before_on = bool(self.cfg.get("tts_enabled", True))
        # 一键静音不归面板管：面板开着的时候用户可能用双击面板静音过，
        # 面板手里是打开那一刻的旧快照，一保存就把静音状态冲回去了
        cfg.pop("mute_all", None)
        self.cfg.update(cfg)
        self.save_config()

        now_on = bool(self.cfg.get("tts_enabled", True))
        if self.tts is not None:
            if before_on and not now_on:
                self.tts.stop()          # 关语音得主动停，不然当前这句会念完
            if before_voice != self.cfg.get("tts_voice"):
                self.tts.last_error = ""  # 换过音色，旧报错不该还挂着

        self._weather_cache = None        # 城市可能改了
        self.update()
        try:
            self.reload_anim_cfg(silent=True)
        except Exception as e:
            print("[settings] 动作节奏重排失败:", e)
        return True

    def open_settings(self):
        """右键菜单入口：设置面板。

        每次都 reload 一次：桌宠一开就是几个小时，settings_panel 一旦被 import
        就进了 sys.modules，之后改面板代码（加页签、改字段）全部看不到，
        表现成「我改了但界面还是老的」。reload 只重跑模块，不影响已有窗口实例。

        `ding` 也得跟着 reload：面板要读它的 TEMPLATES / DEFAULT_CFG，
        但那是屏幕之外的小模块，一直躺在内存里。漏了它会报
        `module 'ding' has no attribute 'TEMPLATES'`，整个面板打不开（踩过）。
        """
        try:
            import importlib
            for name in ("ding",):
                mod = sys.modules.get(name)
                if mod is not None:
                    try:
                        importlib.reload(mod)
                    except Exception as e:
                        print(f"[settings] 重载 {name} 失败（继续开面板）:", e)
            import settings_panel
            settings_panel = importlib.reload(settings_panel)
            settings_panel.open_panel(pet=self, parent=None)
        except Exception as e:
            print("[settings] 面板打不开:", e)
            QMessageBox.warning(self, "设置面板",
                                f"打不开设置面板：{e}\n\n"
                                "如果缺 PySide6 组件，用系统 Python 跑一次 "
                                "pip install -r requirements.txt")

    def save_config(self):
        """立即落盘，别等到「退出」才存（直接关进程也不会丢设置）"""
        try:
            self.cfg["x"], self.cfg["y"] = self.x(), self.y()
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG_PATH)
        except Exception as e:
            print("配置保存失败:", e)

    def set_mode(self, mode):
        self.mode = mode
        self.target = None
        self.cfg["mode"] = mode
        self.save_config()

    def set_size(self, mult):
        """换挡。窗口、静态图、素材帧三者必须一起换，少一个就「点了没反应」。

        素材动画是按渲染高度烤的帧（见 anim_lib 的「读进来就是渲染尺寸」），
        所以 animlib 的高度也得跟着改；窗口宽度还要留出动画画布横向伸出去的
        那部分，跟 __init__ 里那套算法保持一致。
        """
        self.cur_h = round(340 * mult)
        self.cfg["size"] = mult
        self.cross_t = 0.0
        self.prev_key = None
        self.win_mx = int(self.cur_h * 0.062) + 6
        self.win_w = max(p.width() for k, p in self.sprites.items() if k[1] == self.cur_h) + self.win_mx * 2
        if self.animlib is not None:
            self.animlib.reset_height(self.cur_h)
            aw, ah = self.animlib.canvas
            if aw and ah:
                need = round(aw * self.cur_h / ah) + self.win_mx * 2
                if need > self.win_w:
                    self.win_w = need
        self.setFixedSize(self.win_w, self.cur_h + BUBBLE_H + MARGIN * 2 + 10)
        self._bright_cache.clear()
        self._bright_used = 0
        self.snap_into_screen()
        self.update()
        self.save_config()

    def set_brightness(self, val):
        """亮度增益（0~0.4）。改变时清提亮缓存，立即重绘。"""
        val = max(0.0, min(0.4, float(val)))
        self.cfg["brightness"] = val
        self._bright_key = val
        self._bright_cache.clear()
        self._bright_used = 0
        self.save_config()
        self.update()

    def _random_point_in(self, geo):
        """在可用区域里取一个「整个窗口都落在屏内」的中心点"""
        hw, hh = self.width() / 2, self.height() / 2
        lo_x, hi_x = geo.left() + hw + 4, geo.right() - hw - 4
        lo_y, hi_y = geo.top() + hh + 4, geo.bottom() - hh - 4
        if hi_x <= lo_x:
            lo_x = hi_x = (geo.left() + geo.right()) / 2
        if hi_y <= lo_y:
            lo_y = hi_y = (geo.top() + geo.bottom()) / 2
        return (random.uniform(lo_x, hi_x), random.uniform(lo_y, hi_y))

    def _clamp_on_screen(self, geo=None):
        """把窗口整体夹回屏幕内（多屏时按当前所在屏算）"""
        if geo is None:
            geo = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        x = max(geo.left(), min(geo.right() - self.width(), self.x()))
        y = max(geo.top(), min(geo.bottom() - self.height(), self.y()))
        if x != self.x() or y != self.y():
            self.move(int(x), int(y))

    def snap_into_screen(self):
        self._clamp_on_screen()

    def _apply_passthrough(self, on):
        hwnd = int(self.winId())
        GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
        style = ctypes.windll.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        style = style | WS_EX_LAYERED
        if on:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)

    def set_passthrough(self, on):
        self.cfg["passthrough"] = bool(on)
        self._apply_passthrough(bool(on))
        self.save_config()
        if on:
            self.say("我隐身了！右键托盘图标解除～")

    def set_topmost(self, on):
        self.cfg["topmost"] = bool(on)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        self.show()
        self.save_config()

    def set_autostart(self, on):
        self.cfg["autostart"] = bool(on)
        lnk = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                           "Start Menu", "Programs", "Startup", "大肥鱼桌宠.lnk")
        try:
            if on:
                ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{}');"
                      "$s.TargetPath='{}';$s.Arguments='\"{}\"';$s.WorkingDirectory='{}';$s.Save()"
                      .format(lnk, PYTHONW,
                              "" if getattr(sys, "frozen", False) else ENTRY_SCRIPT,
                              APP_DIR))
                subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
                self.save_config()
                self.say("已开机自启，明天见～")
            else:
                if os.path.exists(lnk):
                    os.remove(lnk)
                self.save_config()
                self.say("已取消开机自启")
        except Exception as ex:
            QMessageBox.warning(self, "开机自启", f"设置失败：{ex}")

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def quit_app(self):
        self.save_config()
        # 自己拉起来的看板服务要跟着退：它的窗口是隐藏的，用户看不见也关不掉，
        # 留着就是一个没主的 node 进程占着 4174。用户自己双击 .cmd 起的实例不动。
        try:
            import board_link as bl
            if bl.stop_server(timeout=2.0):
                print("[board] 随桌宠一起停了")
        except Exception:
            pass
        self.tray.hide()
        QApplication.quit()


try:
    from ctypes import wintypes as _wintypes

    class _SEHRecord(ctypes.Structure):
        """Windows EXCEPTION_RECORD，只取我们真正要读的那个字段。"""
        _fields_ = [("ExceptionCode", _wintypes.DWORD),
                    ("ExceptionFlags", _wintypes.DWORD),
                    ("ExceptionRecord", ctypes.c_void_p),
                    ("ExceptionAddress", ctypes.c_void_p),
                    ("NumberParameters", _wintypes.DWORD),
                    ("ExceptionInformation", ctypes.c_void_p * 15)]

    class _SEHPointers(ctypes.Structure):
        _fields_ = [("ExceptionRecord", ctypes.POINTER(_SEHRecord)),
                    ("ContextRecord", ctypes.c_void_p)]
except Exception:                    # 非 Windows：整个兜底机制直接不启用
    _wintypes = None
    _SEHRecord = None
    _SEHPointers = None

_SEH_HITS = []                       # 每次真接住记一个时间戳，给自检和排查看


def _install_seh_guard(limit=20, window=60.0, verbose=True):
    """接住 0x8001010d，别让整个进程死在一个可以重试的 COM 错误上。

    这个码是 RPC_E_CANTCALLOUT_ININPUTSYNCCALL：Windows 在处理输入同步调用
    （SendMessage 一类）时，不允许再发起跨 apartment 的 COM 调用。
    Qt Multimedia 在 Windows 上走 WMF 后端，播放和音频设备变化都会往 COM
    里回调，Qt 内部能重试，但异常一旦冒到顶层就把 Python 进程直接带走——
    表现就是「鱼突然不见了」，而且连 Python 栈都看不到，只有一行 fatal
    exception。

    这里返回「继续执行」，让那条指令重试。同一个窗口期内触发过多就放手：
    那种情况不是偶发，继续掩盖只会让程序状态越走越乱，不如让它死得明白。

    返回 1 表示装上了，0 表示跳过（非 Windows）。
    """
    if sys.platform != "win32" or _SEHPointers is None:
        return 0

    RESUME = 0xFFFFFFFF        # EXCEPTION_CONTINUE_EXECUTION = -1
    SEARCH = 0
    CODE = 0x8001010D
    hits = []

    def _handler(info):
        try:
            if not info or not info.contents.ExceptionRecord:
                return SEARCH
            if info.contents.ExceptionRecord.contents.ExceptionCode != CODE:
                return SEARCH
            now = time.time()
            hits[:] = [t for t in hits if now - t < window]
            hits.append(now)
            if len(hits) > limit:
                if verbose:
                    print("[seh] 一分钟内第 %d 次 0x8001010d，不再兜底" % len(hits))
                return SEARCH
            # 只记真正兜住的那次。熔断放行的也记上的话，事后看数字会虚高。
            _SEH_HITS.append(now)
            if verbose:
                print("[seh] 接住 0x8001010d，继续跑（本分钟第 %d 次）" % len(hits))
            return RESUME
        except Exception:          # noqa: BLE001  handler 自己绝不能抛
            return SEARCH

    proto = ctypes.WINFUNCTYPE(_wintypes.LONG, ctypes.POINTER(_SEHPointers))
    cb = proto(_handler)
    # 回调对象必须一直被引用着。交出去再被回收，下次异常就跳到已释放地址。
    globals()["_SEH_CB"] = cb
    ctypes.windll.kernel32.SetUnhandledExceptionFilter(cb)
    return 1


def main():
    # 后台启动（pythonw）时看不到报错，把未捕获异常落到文件，方便事后查
    import time as _time
    import traceback as _tb

    def _log_exc(exc_type, exc, tb):
        try:
            with open(os.path.join(APP_DIR, "pet_crash.log"), "a", encoding="utf-8") as f:
                f.write("%s\n" % _time.strftime("%Y-%m-%d %H:%M:%S"))
                _tb.print_exception(exc_type, exc, tb, file=f)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _log_exc
    try:
        import faulthandler
        faulthandler.enable(open(os.path.join(APP_DIR, "pet_fault.log"), "a"))
    except Exception:
        pass

    # 顺序要紧：先让 faulthandler 装上它的记录器，再挂兜底。
    # faulthandler 走的是 vectored handler，先打印我们才能留下痕迹；
    # 它打完继续往上传，正好落到这里的 unhandled filter 上被接住。
    _install_seh_guard()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    w = PetWindow()
    try:
        # bridge_server.py 放在上一层 C:\DaFeiYu\，这里把它加入模块搜索路径
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from bridge_server import start_bridge
        start_bridge(w, int(os.environ.get("DAFEIYU_PORT", "8790")))
    except OSError as e:
        # 端口被占 = 已经有一条鱼在跑了。这里必须退出，不能继续空转——
        # 否则会多出一条「哑」的鱼（有窗口、没桥，不响应任何事件）。
        print("[bridge] 端口被占，已有实例在跑，本实例退出:", e)
        sys.exit(0)
    except Exception as e:
        print("[bridge] start failed:", e)

    # 启动即演一次指定动作（pending_act.json），用于「重启后验一眼新动作」
    try:
        w.play_pending_act()
    except Exception as e:
        print("[pending_act] failed:", e)

    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        try:
            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(None, "大肥鱼桌宠出错", str(ex))
        except Exception:
            pass
        raise
