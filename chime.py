# -*- coding: utf-8 -*-
"""整点报时 —— 到整点说一句：报时间 + 按时间段给一句提醒。

为什么单独一个模块：时间窗判断和台词都在这儿，pet.py 只负责「到点了叫我一声」。
配置在 config.json（chime_* 那一组），设置面板「报时」页是它的唯一入口 ——
用户要改时段、改台词、关掉，都不该来改代码。

台词分两种来源：
  · 内置：`HINTS` 里按时间段分好组，随机挑一句，不会天天一模一样
  · 自定义：config 里 `chime_lines` 一行一句，填了就只用用户那几句
    支持 {time} 占位符，会换成「九点」「下午三点」这类说法
"""
import random
from datetime import datetime

CN = "零一二三四五六七八九"

# 整点说法：14 → 十四点。报时不说「下午两点」是因为用户可能只想知道几点，
# 而中文口语里「十四点」比「下午两点」更不会出错（跨时区/跨天时也不歧义）。
def cn_hour(h):
    h = int(h) % 24
    if h == 0:
        return "零点"
    if h < 10:
        return CN[h] + "点"
    if h == 10:
        return "十点"
    if h < 20:
        return "十" + CN[h - 10] + "点"
    if h == 20:
        return "二十点"
    return "二十" + CN[h - 20] + "点"


# 时间段 → 台词。写的是「它自己会说的话」：务实、不煽情、偶尔损一句。
HINTS = {
    "dawn": (          # 5-7
        "这么早？我还没醒透。",
        "早上的脑子最干净，先干正事。",
        "天亮了，别赖了。",
    ),
    "morning": (       # 8-11
        "上午的活儿，挑最要紧的先动。",
        "这个点脑子最清醒，别浪费在刷手机上。",
        "坐一上午腰先废，起来走两步。",
        "上午别开会开到饭点，浪费时间。",
    ),
    "noon": (          # 12-13
        "中午了，饭别拖。",
        "吃口热的，别又拿面包对付。",
        "歇二十分钟，下午能多干一小时。",
    ),
    "afternoon": (     # 14-17
        "下午最容易走神，先把手上这件收个尾。",
        "两点到四点最难熬，撑过去就顺了。",
        "喝口水。你已经坐很久了。",
        "下午别排太满，留点空给突发的事。",
    ),
    "evening": (       # 18-21
        "晚上了，该收的收一收。",
        "今天没做完的，明天还有一天。",
        "这个点还在忙，晚饭吃了没。",
    ),
    "night": (         # 22-4
        "这么晚还醒着？",
        "熬夜的账，明天早上还。",
        "该睡了，明天早上你会感谢现在的你。",
    ),
}

GENERIC = (
    "整点了。",
    "时间跑得比我快。",
    "又一个小时过去了。",
)


def slot_of(hour):
    """小时 → 时间段名。"""
    h = int(hour) % 24
    if 5 <= h <= 7:
        return "dawn"
    if 8 <= h <= 11:
        return "morning"
    if 12 <= h <= 13:
        return "noon"
    if 14 <= h <= 17:
        return "afternoon"
    if 18 <= h <= 21:
        return "evening"
    return "night"


def in_window(cfg, hour):
    """现在这个整点在不在报时时段里。起止都算（8 到 22 = 8:00~22:00 都报）。

    起止相同 = 全天 24 小时都报；起 > 止 表示跨午夜（22 → 6）。
    """
    def _hour(key, fallback):
        v = cfg.get(key, fallback)
        return (fallback if v is None else int(v)) % 24

    a = _hour("chime_from", 8)
    b = _hour("chime_to", 22)
    h = int(hour) % 24
    if a == b:
        return True
    if a < b:
        return a <= h <= b
    return h >= a or h <= b


def should_chime(cfg, last_key="", now=None):
    """这一刻该不该报。返回 (要不要报, 这一小时的标记)。

    只在整点那一分钟报，同一个小时只报一次 —— 靠 last_key 去重，
    这样改系统时间、机器休眠醒来都不会重复轰炸。
    """
    if not cfg.get("chime_on", True):
        return False, last_key
    now = now or datetime.now()
    if now.minute != 0:
        return False, last_key
    if not in_window(cfg, now.hour):
        return False, last_key
    key = now.strftime("%Y-%m-%d %H")
    if key == last_key:
        return False, last_key
    return True, key


def custom_lines(cfg):
    """用户自定义台词，一行一句，空行丢掉。"""
    raw = cfg.get("chime_lines") or ""
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        items = str(raw).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [s.strip() for s in items if s and s.strip()]


def line_for(cfg, now=None):
    """拼一句报时。

    有自定义台词就只从自定义里挑（{time} 换成「九点」）；没有就用内置那套。
    内置是「报时间 + 一句提醒」，自定义只换提醒那一半不够自由，所以整句交给用户，
    但 {time} 仍然能填 —— 想只报时间就写「{time} 整。」
    """
    now = now or datetime.now()
    hh = int(now.hour)
    stamp = cn_hour(hh)

    mine = custom_lines(cfg)
    if mine:
        return random.choice(mine).replace("{time}", stamp).replace("{hour}", str(hh))

    hint = random.choice(HINTS.get(slot_of(hh), GENERIC))
    # 整点报时先说时间，再说那句。两句都短，一口气念完不烦。
    return f"{stamp}整。{hint}"


def preview(cfg=None, hour=None):
    """面板「试一句」用：不碰系统时间，按给定小时生成一句。"""
    cfg = cfg or {}
    hh = int(hour) if hour is not None else datetime.now().hour
    fake = datetime(2000, 1, 1, hh, 0)
    return line_for(cfg, now=fake)
