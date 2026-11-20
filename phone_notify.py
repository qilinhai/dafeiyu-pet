# -*- coding: utf-8 -*-
"""大肥鱼 · 手机通知播报：把手机收到的通知，念给电脑上的大肥鱼听。

走 Escrcpy 自带的 adb（无线模式），每 POLL_S 秒抓一次通知列表，
跟上一轮比对，只把「新出现」的通知标题念出来。念过的记 key，重复不念。

依赖：大肥鱼桌宠正在运行（bridge_server 8790）+ adb 已无线连上手机。
用法：
    python phone_notify.py              # 前台跑，Ctrl+C 停
    python phone_notify.py --once       # 只抓一轮，打印新通知（调试）
    python phone_notify.py --dry-run    # 同上，但不调用大肥鱼
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "phone_notify.log")
LOG_DIR = os.path.join(HERE, "logs")


def _safe_stdout():
    """pythonw 启动时 sys.stdout 是 None，任何 print 都会 AttributeError 直接把进程弄死。

    无窗口启动是常态（双击启动器、开机自启、计划任务、hook 拉起），
    所以这里必须自己接一个文件，不能指望外部重定向。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        f = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    except Exception:
        return
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


_safe_stdout()

try:
    from notify_voice import dress, kind_of
except Exception:            # 话术层缺失时退回只念事实，不阻塞监听
    def kind_of(pkg="", title="", body="", chat_pkgs=()):
        return "notice"

    def dress(fact, kind="notice", now=None):
        f = (fact or "").strip("，。、 　")
        return (f + "。") if f else ""

try:
    import phone_link
except Exception:
    phone_link = None

# 「说话前先想一下」那一层。缺了它也必须能播——所以是可选依赖。
# 没 key、断网、限流、模型抽风，opine 都返回 ""，这里自动退回模板话术。
try:
    import say_ai
except Exception:
    say_ai = None

HERE = os.path.dirname(os.path.abspath(__file__))
ADB = r"C:\Users\ASUS\AppData\Local\Programs\escrcpy\resources\extra\win\scrcpy\adb.exe"
POLL_S = 2.0            # 抓取间隔

# Windows 上每调一次外部 exe，默认都会先起一个 conhost.exe 当控制台宿主。
# 只加 CREATE_NO_WINDOW 是把窗口藏起来 —— conhost 照样创建，实测每秒一个，
# 任务栏/桌面上就是「黑框一直闪」。DETACHED_PROCESS 才是不创建控制台，
# 连 conhost 都不会有。stdout 走管道，不受影响。
NO_WINDOW = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
STATE_FILE = os.path.join(HERE, "phone_notify_seen.json")

# 大肥鱼桥接（从 endpoint.json 读，不写死）
ENDPOINT = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                        "DaFeiYuBridge", "endpoint.json")
MAX_TEXT = 200


def read_endpoint():
    try:
        with open(ENDPOINT, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("port"), d.get("token")
    except Exception:
        return None, None


# 桌宠的 config.json（和 phone_notify.py 不在同一目录，得显式找）。
# 这里只读「没监听时定时提醒」这一个开关，决定本进程要不要催连接。
PET_CONFIG = os.path.join(HERE, "dafeiyu-pet-main", "config.json")

# 播报规则（面板「通知」页签写这里）。跟本文件同目录，改了热加载。
NOTIFY_CFG = os.path.join(HERE, "notify_config.json")


def remind_enabled():
    """读桌宠的「没监听时定时提醒我」开关（phone_remind）。

    本进程是独立跑的，桌宠里的开关它本来不知道；现在催连接之前先问一句，
    关了这个开关，就别再每 NAG_GAP 秒催「手机还是没连上」烦人。
    读不到文件时保守返回 True（保持旧行为，别因为路径问题把提醒误关）。
    """
    try:
        with open(PET_CONFIG, encoding="utf-8") as f:
            d = json.load(f)
        return bool(d.get("phone_remind", True))
    except Exception:
        return True


# 在手机端先 grep 掉无关行：完整 dumpsys 有 800+ KB，只留需要的约 7 KB。
# 无线上这差别是每天几十 G 的流量，速度也快一倍。
FILTER_SH = ("dumpsys notification --noredact | "
             "grep -E 'NotificationRecord|android[.]title|android[.]text'")


def adb_dump():
    """抓 dumpsys notification（手机端已过滤），返回文本。失败返回 None。"""
    try:
        r = subprocess.run(
            [ADB, "shell", FILTER_SH],
            capture_output=True, timeout=15,
            creationflags=NO_WINDOW,          # 别闪黑框
            stdin=subprocess.DEVNULL,         # 也别让它抢键盘
        )
        err = r.stderr.decode("utf-8", errors="replace")
        # 退出码 1 有两种意思：grep 没匹配到（正常），或者根本没设备（断线）。
        # 只看退出码会把断线当成「手机上没有通知」，然后就永远不重连了。
        low = err.lower()
        if any(w in low for w in ("no devices", "device offline", "unauthorized",
                                  "not found", "error:", "failed to connect")):
            return None
        if r.returncode not in (0, 1):
            return None
        return r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return None


# 应用包名 → 中文名。先只给常见几类，抓不到名字就只念包名尾巴。
# 应用包名 → 中文名。按这台手机实际装的 App 补的，认不出的不念名字（只念标题）。
APP_NAMES = {
    # 通讯
    "com.tencent.mm": "微信",
    "com.tencent.mobileqq": "QQ",
    "com.ss.android.lark": "飞书",
    "com.alibaba.android.rimet": "钉钉",
    "com.tencent.workbuddy.app": "WorkBuddy",
    # 系统
    "android": "手机",
    "com.android.shell": "测试",
    "com.android.phone": "电话",
    "com.android.incallui": "电话",
    "com.hihonor.mms": "短信",
    "com.android.mms": "短信",
    "com.hihonor.calendar": "日历",
    "com.youloft.calendar": "万年历",
    "com.android.deskclock": "闹钟",
    "com.hihonor.systemmanager": "手机管家",
    # 店里用的（餐饮经营）
    "com.sjst.xgfe.android.kmall": "快驴进货",
    "com.lakala.cashier.pda": "拉卡拉收银",
    "com.lakala.xlsm": "拉卡拉",
    "com.sankuai.meituan.meituanwaimaibusiness": "美团商家",
    "com.sankuai.meituan.takeoutnew": "美团外卖",
    "com.sankuai.meituan": "美团",
    "me.ele.napos": "饿了么商家",
    "com.dianping.v1": "大众点评",
    "com.smile.gifmaker": "快手",
    "com.ss.android.ugc.aweme": "抖音",
    "com.lemon.lv": "剪映",
    "com.xingin.xhs": "小红书",
    # 银行 / 金融
    "com.ecitic.bank.mobile": "中信银行",
    "com.citiccard.mobilebank": "中信信用卡",
    "cn.com.spdb.mobilebank.per": "浦发银行",
    "com.spdbccc.app": "浦发信用卡",
    "com.icbc": "工商银行",
    "com.icbc.elife": "工银e生活",
    "com.chinamworld.main": "建设银行",
    "com.ccb.creditcard": "建行信用卡",
    "com.android.bankabc": "农业银行",
    "com.bankcomm.Bankcomm": "交通银行",
    "com.bankcomm.maidanba": "交行买单吧",
    "cn.com.cmbc.newmbank": "民生银行",
    "com.cmbc.cc.mbank": "民生信用卡",
    "com.cmbchina.ccd.pluto.cmbActivity": "招商银行",
    "com.cgbchina.xpt": "广发银行",
    "com.cebbank.mobile.cemb": "光大银行",
    "com.cib.cibmb": "兴业银行",
    "com.cib.xyk": "兴业信用卡",
    "com.csii.zybk.ui": "中原银行",
    "com.hfbank.mobile": "恒丰银行",
    "com.cs_credit_bank": "长沙银行",
    "com.ebank.creditcard": "信用卡管家",
    "com.pingan.paces.ccms": "平安银行",
    "com.zhongan.iyunbao": "众安保险",
    "com.unionpay": "云闪付",
    "com.eg.android.AlipayGphone": "支付宝",
    # 购物 / 生活
    "com.taobao.taobao": "淘宝",
    "com.taobao.idlefish": "闲鱼",
    "com.xunmeng.pinduoduo": "拼多多",
    "com.jd.mrd.jingming": "京东",
    "com.baidu.BaiduMap": "百度地图",
    "com.autonavi.minimap": "高德地图",
    "com.baidu.netdisk": "百度网盘",
    "com.cainiao.wireless": "菜鸟",
    "ctrip.android.view": "携程",
    "com.MobileTicket": "铁路12306",
    "com.biji.getnotes": "得到笔记",
    "com.huawei.health": "华为运动健康",
    "com.videogo": "萤石",
    "com.xiaomi.smarthome": "米家",
}


def app_name(pkg):
    """认得的 App 给中文名；不认得的返回空串，说话时不念名字（念包名尾巴更难听）。"""
    return APP_NAMES.get(pkg, "")


# ---- 念不念：规则外置，面板可改，改完热加载 ----
# 白名单的毛病是「猜不全」：你装了什么 App、哪条重要，我没法预先知道。
# 所以反过来——默认都念，只把明确没用的挡掉。
#
# 这些规则原来硬编码在本文件里，2026-11-06 起抽到 notify_config.json：
# 监听是独立进程，面板在桌宠进程里，两边只能靠文件通信。改完不重启也生效，
# 靠 load_rules() 比对文件 mtime —— 每 2 秒真读一次盘太浪费。

DEFAULT_RULES = {
    "_note": "手机通知播报规则。面板「设置 → 通知」可改，改完监听自动重新读，不用重启。",
    "enabled": True,          # 总开关。关掉后照样记账，但不出声（防恢复时补念一堆）
    "chat_only_at": True,     # 聊天类只念「群里有人@我」
    "chat_skip_media": True,  # 群里@我但只有图片/视频/通话，也不念
    "chat_pkgs": ["com.tencent.mm", "com.tencent.mobileqq", "com.tencent.tim"],
    "mute_pkgs": [
        "android", "com.android.systemui", "com.android.providers.downloads",
        "com.hihonor.parentcontrol",          # 已使用 X 小时
        "com.hihonor.systemmanager",          # 内存/省电/清理
        "com.hihonor.android.launcher",
        "com.hihonor.android.totemweather",   # 天气营销推文
    ],
    "urgent_words": ["地震", "预警", "紧急", "警报", "台风", "暴雨",
                     "火灾", "停水", "停电"],
    "skip_title": [
        "广告", "立减金", "优惠", "热销", "推荐", "领券", "活动", "限时",
        "营销", "签到", "任务", "排行榜", "精选", "上新", "秒杀", "满减",
        "已连接", "无线调试", "已使用", "正在运行", "屏幕录制", "截屏",
        "电池", "省电", "内存", "清理", "更新提示",
        # 进度类：内容一秒一变，念了就是噪音
        "下载中", "上传中", "传输中", "安装中", "解压中", "同步中",
        "正在下载", "正在上传", "正在传输", "正在安装",
    ],
    "poll_seconds": 2.0,      # 抓取间隔
    "flood_gap": 90,          # 刷屏保护窗口（秒）
    "flood_max": 2,           # 窗口内同一个 App 最多念几条
    "core_max": 30,           # 一句事实的字数上限
    "urgent_max": 46,         # 紧急通知放宽到多少字
}

# 当前生效的规则，由 load_rules() 填。空表示还没读过。
RULES = {}

# 下面四个是模块级「可变容器」：load_rules 原地 clear/update，
# 其它函数里 `pkg in CHAT_PKGS`、`any(w in t for w in URGENT_WORDS)` 不用跟着改。
MUTE_PKGS: set = set()
CHAT_PKGS: set = set()
URGENT_WORDS: tuple = ()
SKIP_TITLE: tuple = ()

# 聊天类：默认全静音。唯一例外是「群里有人 @ 我」——那是真有人等你回话。
# 用户 2026-11-06 明确要求：微信不是群里艾特的，一律不念；
# 发视频通话这些也不念。私聊、文件、红包、语音一样闭嘴。
# （包名列表在 DEFAULT_RULES["chat_pkgs"]，这里只留判断用的正则）
# 群 @ 的几种写法：微信标准是「[有人@我]昵称: 内容」，也有不带方括号、
# 或者直接写成「张三: @我 内容」的。@所有人 不算（那不是点你）。
CHAT_AT_PAT = re.compile(r"\[?有人\s*@\s*我\]?|@\s*我(?!们)")
# 这些内容本身没有字，念出来等于没说：图片/视频/语音/表情，以及各种通话
CHAT_EMPTY_PAT = re.compile(
    r"\[(图片|视频|语音|动画表情|表情|链接|音乐|位置|名片)\]"
    r"|视频通话|语音通话|通话中|邀请你(视频|语音)?通话|来电|视频聊天")

# 验证码 / 一次性口令：这类内容不送模型（见 line_for）。
# 念出来你自己听是有用的，但发给云端模型既没用又多一条泄露面。
_OTP_PAT = re.compile(
    r"验证码|校验码|动态码|一次性(密码|口令)|登录码|激活码"
    r"|verification\s*code|\bOTP\b|\bcode\b\s*[:：]?\s*\d{4,8}", re.I)

# 参数是全局变量，面板改完由 load_rules 就地更新
POLL_S = DEFAULT_RULES["poll_seconds"]
FLOOD_GAP = DEFAULT_RULES["flood_gap"]
FLOOD_MAX = DEFAULT_RULES["flood_max"]
CORE_MAX = DEFAULT_RULES["core_max"]
URGENT_MAX = DEFAULT_RULES["urgent_max"]

_CFG_MTIME = -1.0       # 上次读到的文件时间戳，没变就不重复解析


def _words(v):
    """面板写回来的是列表，手改文件时可能是逗号分隔的字符串，两种都认。"""
    if isinstance(v, str):
        v = re.split(r"[,，、\s]+", v)
    return [s for s in (str(x).strip() for x in (v or [])) if s]


def _num(v, lo, hi, default):
    try:
        return min(hi, max(lo, float(v)))
    except (TypeError, ValueError):
        return default


def load_rules(force=False):
    """读 notify_config.json 并就地更新生效规则。

    文件不存在 / 读不动 → 用 DEFAULT_RULES（也就是改造前那套硬编码，
    行为一致），绝不让规则文件的问题把监听搞哑。
    文件没变就直接返回缓存：这个函数每轮（2 秒）都会调。
    """
    global RULES, _CFG_MTIME, POLL_S, FLOOD_GAP, FLOOD_MAX, CORE_MAX, URGENT_MAX
    # URGENT_WORDS / SKIP_TITLE 必须一起声明：漏了它们会变成局部变量，
    # 模块级的空元组永远不被填上，结果是「营销词全都不挡、紧急词全都不放行」。
    global URGENT_WORDS, SKIP_TITLE

    try:
        mtime = os.path.getmtime(NOTIFY_CFG)
    except OSError:
        mtime = -1.0
    if not force and mtime == _CFG_MTIME and RULES:
        return RULES

    raw = dict(DEFAULT_RULES)
    if mtime >= 0:
        try:
            with open(NOTIFY_CFG, encoding="utf-8") as f:
                disk = json.load(f)
            if isinstance(disk, dict):
                raw.update({k: v for k, v in disk.items() if k in DEFAULT_RULES})
        except Exception as e:
            print(f"[!] notify_config.json 读不动，先用默认规则：{e}")

    RULES = raw
    _CFG_MTIME = mtime

    CHAT_PKGS.clear()
    CHAT_PKGS.update(_words(raw.get("chat_pkgs")))
    MUTE_PKGS.clear()
    MUTE_PKGS.update(_words(raw.get("mute_pkgs")))
    URGENT_WORDS = tuple(_words(raw.get("urgent_words")))
    SKIP_TITLE = tuple(_words(raw.get("skip_title")))

    POLL_S = _num(raw.get("poll_seconds"), 0.5, 10.0, 2.0)
    FLOOD_GAP = int(_num(raw.get("flood_gap"), 10, 3600, 90))
    FLOOD_MAX = int(_num(raw.get("flood_max"), 1, 20, 2))
    CORE_MAX = int(_num(raw.get("core_max"), 10, 80, 30))
    URGENT_MAX = int(_num(raw.get("urgent_max"), 20, 160, 46))
    return RULES


def rules():
    """给外部（面板、调试脚本）拿当前规则。"""
    return RULES or load_rules()


def is_urgent(title, body=""):
    t = f"{title or ''} {body or ''}"
    return any(w in t for w in URGENT_WORDS)


def _fp(title, text):
    """内容指纹。同一条通知被更新（银行短信又来一笔）时，key 要变，否则会被吞。"""
    raw = f"{title}\x01{text}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()[:6]


def is_chat_at(title, body=""):
    """这条聊天通知是不是「群里有人 @ 我」。"""
    return bool(CHAT_AT_PAT.search(f"{title or ''} {body or ''}"))


def chat_core(title, body=""):
    """群 @ 通知去壳，只留真正的消息内容。

    真实样本长这样（from phone_notify.log）：
        [有人@我]Blue航航: 明天的货到了吗
        [3条][表情]露宝[表情]羌红他妹: [图片]
    去掉计数前缀、@ 标记、[表情]、发送人，剩下有字才算有内容——
    只剩 [图片]/[视频]/通话 的话，念出来全是废话，直接判空。
    """
    b = body or ""
    b = CHAT_EMPTY_PAT.sub("", b)                 # 图/视频/通话占位先抹掉
    b = re.sub(r"\[?\d+\s*条\]?", "", b)           # [3条]
    b = CHAT_AT_PAT.sub("", b)                     # [有人@我]
    b = re.sub(r"\[[^\]]{1,6}\]", "", b)           # [表情] 这类占位
    if ":" in b or "：" in b:                      # 「张三: 内容」只留内容
        b = re.split(r"[:：]", b, maxsplit=1)[1]
    return b.strip(" ，。、:：\t")


def is_important(pkg, title, body=""):
    """判断这条通知要不要念。"""
    r = rules()
    # 1. 聊天类先判：只放行「群里 @ 我」，且 @ 的那条得有字。
    #    放在最前面是因为「紧急词」也不该把微信私聊放进来——用户要的是彻底不念。
    #    面板把「只念群里@我」关掉时，聊天类当普通 App 往下走。
    if pkg in CHAT_PKGS and r.get("chat_only_at", True):
        if not is_chat_at(title, body):
            return False
        if not r.get("chat_skip_media", True):
            return True          # 图片/视频/通话也算「被点名了」，念一声
        return bool(chat_core(title, body))
    # 2. 紧急的永远放行，不管哪个 App
    if is_urgent(title, body):
        return True
    # 3. 纯噪音包直接挡
    if pkg in MUTE_PKGS:
        return False
    # 4. 营销词挡掉
    if any(w in (title or "") for w in SKIP_TITLE):
        return False
    return True


_LOG_SEEN = {}          # 同一条通知别每 2 秒写一遍盘


def _log_decision(keep, pkg, title, body):
    """把微信/QQ 的判断结果落一行盘。

    群 @ 的文案格式没法离线造，只能等真通知来了才知道对不对。
    这一行就是为了第一次真 @ 到的时候能立刻核对，不用猜。
    """
    try:
        sig = (pkg, title or "", body or "")
        now = time.time()
        if now - _LOG_SEEN.get(sig, 0) < 600:   # 10 分钟内同一条只记一次
            return
        _LOG_SEEN[sig] = now
        if len(_LOG_SEEN) > 300:
            for k, v in list(_LOG_SEEN.items()):
                if now - v > 600:
                    del _LOG_SEEN[k]
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = ("%s %s %s | title=%s | text=%s\n"
                % (stamp, "念  " if keep else "静音", pkg,
                   (title or "")[:40], (body or "")[:80]))
        with open(os.path.join(LOG_DIR, "chat_filter.log"), "a",
                  encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass



def parse(dump):
    """从 dumpsys 输出解析出 [(key, app, title, text)]，只留有标题的。"""
    out = []
    rec = None
    for line in dump.splitlines():
        m = re.match(r"\s*NotificationRecord\(0x[0-9a-f]+:\s*pkg=(\S+)", line)
        if m:
            if rec and rec["title"]:
                # key 带内容指纹：同一条通知被更新（银行短信又来一笔）才算新的
                base = rec["key"] or rec["pkg"]
                out.append((f"{base}#{_fp(rec['title'], rec['text'])}", rec["pkg"],
                            rec["title"], rec["text"]))
            # 真正的 id/tag 在这同一行：NotificationRecord(... pkg=... id=... tag=...)
            idm = re.search(r"\bid=(-?\d+)", line)
            tagm = re.search(r"\btag=(\S+)", line)
            key = f"{m.group(1)}|{idm.group(1) if idm else '?'}"
            if tagm and tagm.group(1) not in ("null", "?"):
                key += f"|{tagm.group(1)}"
            rec = {"pkg": m.group(1), "key": key, "title": "", "text": ""}
            continue
        if not rec:
            continue
        tm = re.search(r"android\.title=String \(([^)]*)\)", line)
        if tm and not rec["title"]:
            rec["title"] = tm.group(1)
        xm = re.search(r"android\.text=String \(([^)]*)\)", line)
        if xm and not rec["text"]:
            rec["text"] = xm.group(1)
    if rec and rec["title"]:
        base = rec["key"] or rec["pkg"]
        out.append((f"{base}#{_fp(rec['title'], rec['text'])}", rec["pkg"],
                    rec["title"], rec["text"]))
    return out


def load_seen():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seen": [], "ts": 0}


def save_seen(d):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


FLOOD_GAP = 90      # 秒（真值由 load_rules 从 notify_config.json 读）
FLOOD_MAX = 2       # 同一个 App 这么多秒内念过这么多次，后面只记账不出声


def _flooded(pkg, state, now):
    """这个 App 是不是正在刷屏。手机连来 5 条银行提醒，念 5 遍没人受得了。"""
    ts = [t for t in (state.get("recent_app") or {}).get(pkg, []) if now - t < FLOOD_GAP]
    return len(ts) >= FLOOD_MAX


def _mark_app(pkg, state, now):
    book = state.setdefault("recent_app", {})
    book[pkg] = ([t for t in book.get(pkg, []) if now - t < FLOOD_GAP] + [now])[-6:]
    # 顺手清掉其他 App 的过期记录，防 state 无限长
    for k in list(book):
        if not any(now - t < FLOOD_GAP for t in book[k]):
            del book[k]


def clean(s):
    s = re.sub(r"\s+", " ", (s or "")).strip()
    # 只留中文、字母数字、常用标点。表情/装饰符（ʚ ྀི ɞ 这类）TTS 念出来是乱码。
    # @ 必须留：微信群 @我 的「@我」洗掉会变成「李四:我明天的货到了」，意思全拧。
    # 方括号也留：[文件] 去掉后读成「文件10月对账单」，听着像病句。
    s = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9，。！？、：；（）()《》【】\[\]@\-—.,!?:;%￥$/+·]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:MAX_TEXT]


CORE_MAX = 30       # 事实句上限（真值由 load_rules 读配置）
URGENT_MAX = 46     # 紧急通知放宽：标题+正文都得念全，「地震预警」不能省

# 正文是这种废话时，宁可念标题
JUNK_BODY = ("点击查看", "点击详情", "点击进入", "查看详情", "查看更多",
             "打开app", "打开APP", "立即查看", "去查看", "详情请")

# 银行长句压缩用：你关心的就三样——哪张卡、多少钱、还剩多少额度
RE_TAIL = re.compile(r"尾号\s*(\d{4})")
RE_AMT = re.compile(r"(消费|支出|取现|扣款|还款|入账|到账|转入|转出|退款|付款|收款)"
                    r"[^\d，。]{0,10}?(\d[\d,]*(?:\.\d+)?)\s*元")
RE_QUOTA = re.compile(r"(?:可用|剩余)额度\s*(\d[\d,]*(?:\.\d+)?)\s*元")


def shrink_money(s, n):
    """银行长句压成「尾号9007 消费3860元，额度42000元」。

    真实银行通知动辄 40 多字，30 字硬截必丢金额——而金额恰恰是唯一有用的。
    宁可把「某某餐饮有限公司」这种商户名丢掉。
    没有可提的金额就返回 None，交给 _clip 正常截。
    """
    m = RE_AMT.search(s)
    if not m:
        return None
    parts = []
    tail = RE_TAIL.search(s)
    if tail:
        parts.append(f"尾号{tail.group(1)}")
    parts.append(f"{m.group(1)}{m.group(2)}元")
    q = RE_QUOTA.search(s)
    if q:
        parts.append(f"额度{q.group(1)}元")
    out = " ".join(parts)
    return out if len(out) <= n else None


def _clip(s, n):
    """只在句中标点处截，硬切会切出半句话。"""
    if len(s) <= n:
        return s
    short = shrink_money(s, n)
    if short:
        return short
    cut = max(s.rfind("，", 0, n), s.rfind("。", 0, n),
              s.rfind("！", 0, n), s.rfind("、", 0, n))
    if cut >= 8:
        return s[:cut].rstrip("，。、 ")
    # 一点标点都没有：至少别把数字切成两半（「消费38」比丢了还糟）
    m = re.search(r"\d[\d,.]*$", s[:n])
    if m and m.start() >= 8:
        return s[:m.start()].rstrip("，。、 ")
    return s[:n]


def _core_of(pkg, title, body=""):
    """通知 → 一句核心事实（不带 App 名前缀）。合并多条时也用它。"""
    t = clean(title)
    b = clean(body)
    # 正文里混着「点击查看详情」这种废话：切掉那一小段，别整句丢掉
    if b and any(w in b for w in JUNK_BODY):
        keep = [x for x in re.split(r"[，。！]", b)
                if x and not any(w in x for w in JUNK_BODY)]
        if sum(len(x) for x in keep) >= 6:
            b = "，".join(keep)
        else:
            b = ""      # 整条正文都是「点击查看」，没信息可念
    if is_urgent(t, b):
        # 标题是「地震预警」，正文是「震中距您32公里」——只念正文等于没说是什么事
        core = f"{t}，{b}" if (t and b and t not in b) else (b or t)
        return _clip(core, URGENT_MAX).strip("，。、 　")
    if pkg in CHAT_PKGS:
        # 聊天类只剩「群里 @ 我」这一种。用群名当前缀，比「微信：」有信息量。
        inner = clean(chat_core(title, body))
        if inner:
            core = inner
        elif (RULES or {}).get("chat_skip_media", True):
            core = t                      # 只剩占位符，退回群名
        else:
            core = "有人提到了你"          # 面板允许念媒体消息时的兜底
    elif b and len(b) >= 6:
        core = b
    else:
        core = t or b
    return _clip(core, CORE_MAX).strip("，。、 　")


def compose_line(pkg, title, body=""):
    """把一条通知变成一句「事实」。

    正文优先：银行标题常常只有「账户变动通知」，金额在正文里；
    正文是「点击查看」这种废话时，才退回用标题。
    只念标题会丢掉金额和单号——那这条通知就白念了。
    """
    name = app_name(pkg)
    # 群 @：标题就是群名，比「微信」有用得多
    if pkg in CHAT_PKGS and clean(title) and clean(title) != name:
        name = clean(title)
    core = _core_of(pkg, title, body)
    if not core:
        return f"{name} 发了条通知" if name else ""
    # 已经带 App 名就别再来一遍（「中原银行：中原银行关于…」太蠢）
    if name and name not in core:
        return f"{name}：{core}"
    return core


# 通知类别 → 桌宠要演的动作。跟 KEYWORD_ACTS 一个思路，但按「类别」定调：
# 钱的事就掏钱包数钱、工作就忙活、报错就抓狂、紧急就吓一跳。
# 拿不到（动作不在库里）就返回 None，桌宠那边自己兜底。
KIND_ANIM = {
    "urgent": ["startle", "tail_slap"],
    "money": ["wallet_count", "wallet_coins", "wallet_full", "wallet_broke"],
    "work": ["work_busy", "note_taking", "work_think"],
    "chat": ["peek", "click_happy"],
    "tech": ["coding", "work_sigh", "game_rage"],
    "notice": ["peek", "stretch"],
    "flood": ["tap_desk"],
    "phone_back": ["click_happy"],
}


def anim_for(kind):
    """按通知类别挑一个动作名。挑不到返回 None。"""
    cand = KIND_ANIM.get(kind)
    if not cand:
        return None
    return random.choice(cand)


def line_for(fact, kind="notice", hint=""):
    """这条通知念什么。

    先问「想一下」那层：给它事实 + 原始正文，它结合人设和记忆库回一句带看法的话。
    拿不到（没 key / 限流 / 数字对不上 / 超时）就用原来的模板话术。
    这一层只是加法——它挂了，播报还得照念，绝不能因为模型不说话就哑掉。
    """
    # 验证码 / 一次性口令不进模型：念给你听是有用的，传给云端一点用没有，
    # 还白白多一条泄露面。这类照样念，只是走模板。
    if _OTP_PAT.search(fact) or _OTP_PAT.search(hint or ""):
        return dress(fact, kind) or fact
    if say_ai is not None:
        try:
            got = say_ai.opine(fact, kind, hint=hint)
            if got:
                return got
        except Exception as exc:      # noqa: BLE001
            print("[ai] 跳过:", type(exc).__name__, exc)
    return dress(fact, kind) or fact


def _who_of(pkg, title, body=""):
    """这条通知是谁发的：群名或联系人。

    微信的正文长这样：「[有人@我]Blue航航: 明天的货到了吗」，
    冒号前面那个人就是来源。短信/银行公告类反过来——正文是内容，
    标题才是来源（「河南电力」），从正文抠出来会变成「电费待缴 户号41」。
    """
    plain = re.sub(r"\[[^\]]{1,8}\]", "", str(title or "")).strip()
    if pkg not in CHAT_PKGS:
        return plain
    s = str(body or "")
    for _ in range(2):
        s = re.sub(r"\[?\d+\s*条\]?", "", s)
        s = CHAT_AT_PAT.sub("", s)
        s = re.sub(r"\[[^\]]{1,8}\]", "", s)
    if ":" in s or "：" in s:
        s = re.split(r"[:：]", s, maxsplit=1)[0]
    s = s.strip(" ，。、:：\t")
    return s or plain


def note_event(pkg, title, body=""):
    """把播过的那条通知留进记忆（event 层）。记不住也绝不能影响播报。"""
    try:
        if not CHAT_PKGS:
            load_rules()          # 单独调用时规则可能还没加载，聊天包名单是空的
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        from pet_memory import remember_event
        if pkg in CHAT_PKGS:
            text = chat_core(title, body)
            if not text:
                return           # 群里只发了图片/表情，没有内容，不占记忆
        else:
            text = _clip(str(body or title or "").strip(), 40)
        if not text:
            return
        remember_event(app_name(pkg) or "手机", _who_of(pkg, title, body), text, ref="notify")
    except Exception as exc:                       # noqa: BLE001
        print("[mem] 推送没记住:", type(exc).__name__, exc)


def speak(text, anim=None, speech=None, style=None):
    """把一句话送到桌宠。

    speech 是「念出来那份」，可以和显示那份不一样：
    通知播报会在事实和梗之间插 [停顿]，气泡里露出标签很难看。
    style 是类别名，桌宠按它决定用什么情绪念（见 voice_style.py）。
    """
    port, token = read_endpoint()
    if not port:
        print("  [跳过] 读不到大肥鱼端点（先启动桌宠）")
        return False
    try:
        # 气泡时长跟着字数走：加了「想一下」之后一句话能到 50 字，
        # 固定 8 秒会出现声音还在念、气泡已经没了。
        secs = max(8.0, len(str(text)) / 4.0 + 3.5)
        body = {"text": text, "speak": True, "seconds": round(secs, 1)}
        if anim:
            body["anim"] = anim
        if speech and speech != text:
            body["speech"] = speech
        if style:
            body["style"] = style
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/say", data=data,
            headers={"Content-Type": "application/json", "X-Pet-Token": token},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            resp = json.loads(r.read().decode())
        return bool(resp.get("ok"))
    except Exception as e:
        print(f"  [跳过] 调大肥鱼失败: {e}")
        return False


def should_read(pkg, title, body=""):
    """过滤 + 记账。微信/QQ 的每条判断都落一行，方便核对 @ 识别对不对。

    注意别叫 keep：once() 里有个同名局部变量（刷屏保护的保留列表），
    会把函数名遮住，运行期直接 UnboundLocalError。
    """
    ok = is_important(pkg, title, body)
    if pkg in CHAT_PKGS:
        _log_decision(ok, pkg, title, body)
    return ok


def once(dry_run=False, state=None):
    load_rules()             # 面板改完规则，下一轮就生效，不用重启监听
    dump = adb_dump()
    if dump is None:
        return None          # 抓不到 = 手机断了，由主循环去重连和催
    items = parse(dump)
    state = state or load_seen()
    seen = set(state.get("seen", []))
    # 只保留「重要」通知：白名单 + 关键词反过滤，微信/营销推送直接静音
    items = [(k, p, t, x) for k, p, t, x in items if should_read(p, t, x)]
    fresh = []
    for key, pkg, title, text in items:
        if key in seen:
            continue
        fresh.append((key, pkg, title, text))

    if not fresh:
        return 0

    # 总开关关掉：照样记账，但一声不出。
    # 只 skip 不记账的话，一开回来会把暂停期间积压的通知全念一遍。
    if not (RULES or {}).get("enabled", True):
        for key, *_ in fresh:
            seen.add(key)
        state["seen"] = list(seen)[-500:]
        state["ts"] = int(time.time())
        save_seen(state)
        print(f"[i] 播报已暂停，{len(fresh)} 条只记账不出声")
        return 0

    print(f"[+] 抓到 {len(fresh)} 条新通知")
    now = int(time.time())
    # 刷屏保护：同一个 App 这么短时间内念过两次，后面的不再一条条念。
    # 但紧急通知绝不压——地震预警被「刷屏保护」吞掉就完蛋了。
    keep = []
    flooded = {}
    for item in fresh:
        key, pkg, title, text = item
        if not is_urgent(title, text) and _flooded(pkg, state, now):
            seen.add(key)
            flooded[pkg] = flooded.get(pkg, 0) + 1
        else:
            keep.append(item)
    fresh = keep

    # 压下去了也要说一声，否则你以为它聋了。同一个 App 这个窗口内只说一次。
    for pkg, n in flooded.items():
        name = app_name(pkg) or "手机"
        said = (state.get("flood_said") or {}).get(pkg, 0)
        if now - said > FLOOD_GAP:
            line = line_for(f"{name} 连发了好几条", "flood")
            print(f"    [压] {line}")
            if not dry_run:
                speak(line, anim_for("flood"))
            state.setdefault("flood_said", {})[pkg] = now
        else:
            print(f"    [压] {name} 又来了 {n} 条，不出声")
    said_book = state.setdefault("flood_said", {})
    for k in [k for k, ts in said_book.items() if now - ts > FLOOD_GAP * 4]:
        del said_book[k]

    if not fresh:
        state["seen"] = list(seen)[-500:]
        state["ts"] = now
        save_seen(state)
        return 0

    # 桥接服务有 0.5 秒限流，多条一口气刷过去会撞 429。
    # 单条就单条念；多条合并成一句，避免连珠炮和丢播。
    if dry_run:
        for key, pkg, title, text in fresh:
            fact = compose_line(pkg, title, text)
            # 这里故意只给模板、不调模型：--dry-run 的用途是确认「抓没抓到、
            # 拼出来是什么」，每跑一次都掏钱不合适。要看模型怎么说，去掉
            # --dry-run 跑一次就行。
            line = dress(fact, kind_of(pkg, title, text, CHAT_PKGS)) if fact else ""
            print(f"    {line or '(没名字，跳过)'}")
            seen.add(key)
    else:
        if len(fresh) == 1:
            key, pkg, title, text = fresh[0]
            fact = compose_line(pkg, title, text)
            if fact:
                k = kind_of(pkg, title, text, CHAT_PKGS)
                line = line_for(fact, k, hint=text)
                print(f"    {line}")
                speak(line, anim_for(k))
                note_event(pkg, title, text)
                _mark_app(pkg, state, now)
            seen.add(key)
        else:
            # 多条一起到：同一个 App 合并成一句，不同 App 分开念。
            # 以前不分 App 一律「X 来了 N 条，第一条：…」——微信 @ 和银行短信
            # 撞进同一个 2 秒窗口时，银行那句金额就被吞掉了，那才是真信息。
            groups, order = {}, []
            for it in fresh:
                if it[1] not in groups:
                    groups[it[1]] = []
                    order.append(it[1])
                groups[it[1]].append(it)
            for pkg in order:
                g = groups[pkg]
                kinds = [kind_of(p, t, x, CHAT_PKGS) for _, p, t, x in g]
                if len(g) == 1:
                    key, p, t, x = g[0]
                    fact = compose_line(p, t, x)
                    if not fact:
                        seen.add(key)
                        continue
                    line = line_for(fact, kinds[0], hint=x)
                else:
                    name = app_name(pkg) or "手机"
                    # 第一条要说正文，别念标题——「招商银行 来了 5 条，
                    # 第一条：招商银行」这种等于什么都没说
                    core0 = _core_of(g[0][1], g[0][2], g[0][3])
                    fact = (f"{name} 来了 {len(g)} 条通知"
                            + (f"，第一条：{core0}" if core0 else ""))
                    # 合并句按「第一条」定调，但紧急优先——地震预警来 5 条时
                    # 不能因为前面压着一条银行短信就把口径定成「钱的事」。
                    k0 = "urgent" if "urgent" in kinds else kinds[0]
                    line = line_for(fact, k0, hint=g[0][3])
                print(f"    {line}")
                speak(line, anim_for("urgent" if "urgent" in kinds else kinds[0]))
                # 一批只记最新的那条：记忆按「谁发的」合并，记多条也只留最后一句
                note_event(pkg, g[-1][2], g[-1][3])
                _mark_app(pkg, state, now)
                for key, *_ in g:
                    seen.add(key)
                time.sleep(0.6)   # 让大肥鱼喘口气，也避开桥接限流
        time.sleep(0.6)   # 让大肥鱼喘口气，也避开桥接限流

    state["seen"] = list(seen)[-500:]   # 只留最近 500 条，防无限增长
    state["ts"] = int(time.time())
    save_seen(state)
    return len(fresh)


NAG_GAP = 600        # 一直连不上时，多久催一次（10 分钟，催太勤会烦）
RETRY_GAP = 60       # 两次重连尝试至少隔这么久，别把 adb 打爆
LINK_BUDGET = 12     # 单次重连最多花几秒
REBASE_GAP = 300     # 断了超过这么久才重建基线，短暂抖动不丢通知
LOCK_PORT = 8795     # 单实例锁：占住这个本地端口，占不到就是已经有监听在跑


def single_instance():
    """占一个本地端口当锁。进程死了端口自动释放，比 pid 文件可靠。

    hook 每次会话启动都会试着拉起监听，没有这个会起一堆互相抢 adb。
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def say(fact, kind="notice"):
    # 走和单条通知同一个出口：先问模型，问不到再退回模板。
    # 这里原来是裸的 dress()，手机重连那两句因此一直念的是硬编码话术。
    line = line_for(fact, kind)
    if not line:
        return False
    print(f"  [说] {line}")
    # 气泡用干净的那句；念出来那份在事实和梗之间多一口气，
    # 否则毒舌尾巴会黏成「念完账目顺口补一句」。规则在 voice_style.py。
    speech = line
    try:
        import voice_style
        speech = voice_style.tts_text(line, fact, kind)
    except Exception:
        pass
    if speech != line:
        print(f"  [念] {speech}")
    return speak(line, anim_for(kind), speech=speech, style=kind)


def main():
    once_only = "--once" in sys.argv
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        once_only = True

    if not os.path.exists(ADB):
        print(f"[错误] 找不到 adb：{ADB}")
        print("      确认 Escrcpy 装在默认位置，或改脚本里的 ADB 路径。")
        sys.exit(1)

    lock = single_instance()
    if lock is None:
        print("[i] 已经有一个监听在跑了，这个不重复起。")
        return

    print("[i] 大肥鱼 · 手机通知播报")
    load_rules(force=True)
    print(f"[i] 规则：{'播报中' if RULES.get('enabled') else '已暂停'}"
          f"，聊天类{'只念群里@我' if RULES.get('chat_only_at') else '照常判断'}"
          f"，每 {POLL_S:g} 秒抓一次")

    # 首轮：先确保手机在，再建基线（已经在通知栏的旧通知不念，别一开机就念一堆）
    dump = adb_dump()
    if dump is None and phone_link:
        print("[i] 手机没连上，先试着连一下……")
        ok, msg = phone_link.ensure(budget=LINK_BUDGET)
        print(f"[i] {msg}")
        if ok:
            dump = adb_dump()

    if dump is None:
        # 连不上也别退：进去等着，连上就自动开始，期间催他
        print("[i] 手机还没连上。进入等待模式，连上就自动开始播报。")
        if once_only:
            sys.exit(1)
        state = {"seen": [], "ts": 0}
        save_seen(state)
    else:
        print("[i] 首轮建立基线，已有通知不念，之后只念新的……")
        items = parse(dump)
        items = [(k, p, t, x) for k, p, t, x in items if should_read(p, t, x)]
        state = {"seen": [k for k, *_ in items], "ts": int(time.time())}
        save_seen(state)
        print(f"[i] 基线建立：已记录 {len(items)} 条重要通知，现在开始监听。")
        if once_only:
            print("[i] --once 模式：仅建立基线后退出。")
            return

    fails = 0
    last_try = 0.0
    last_nag = 0.0
    last_ok = time.time()
    try:
        while True:
            # 固定周期：抓取耗时从间隔里扣。否则「抓 0.5s + 等 2s」实际是 2.5s 一轮。
            t0 = time.time()
            n = once(state=state)
            cost = time.time() - t0
            now = time.time()

            if n is None:                       # adb 抓不到 = 手机断了
                fails += 1
                if fails >= 3 and now - last_try > RETRY_GAP:
                    last_try = now
                    ok, msg = (phone_link.ensure(budget=LINK_BUDGET)
                               if phone_link else (False, "缺 phone_link 模块"))
                    print(f"[i] 重连尝试: {ok} {msg}")
                    if ok:
                        print("[i] 手机回来了，接着播。")
                        say("手机连回来了", "phone_back")
                        fails = 0
                        # 断得久就重建基线，否则一口气把断线期间的通知全念一遍
                        if now - last_ok > REBASE_GAP:
                            state = {"seen": [], "ts": 0}
                            save_seen(state)
                            print("[i] 断得有点久，旧通知先跳过。")
                    elif now - last_nag > NAG_GAP:
                        last_nag = now
                        # 催之前先问桌宠的「没监听时定时提醒」开关，关了就别烦人
                        if remind_enabled():
                            say("手机还是没连上", "phone")
                        else:
                            print("[i] 提醒已关，跳过催连接。")
            else:
                if fails:
                    print("[i] 抓取恢复正常。")
                fails = 0
                last_ok = now

            time.sleep(max(0.8, POLL_S - cost))
    except KeyboardInterrupt:
        print("\n[i] 已停止。")


if __name__ == "__main__":
    main()
