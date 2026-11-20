# -*- coding: utf-8 -*-
"""大肥鱼的长期记忆 v2：分阶段、可回溯、会巩固、不会瞎说。

参考 whyever77/dafeiyu-astrbot 的「经历记忆」策略，砍掉不适合桌宠的部分，
留下四条硬规则：

1. **分阶段**：7 天内是 detail（可以说具体的），7~60 天是 gist（只说还在弄），
   60 天后是 dormant（不主动提）。这不是生物遗忘曲线，是「什么时候配说多细」的检索规则。
2. **保护**：主人明确表达的偏好，永不淡化。
3. **证据与解释分离**：只存话题、情绪、来源轮次；不存评价、不存原文。
4. **召回不强化**：查看/查询不改权重，只有它真的又出现在对话里才 +1。

分工不变：hook 记，pet 说。只依赖标准库（hook 侧不能装依赖）。
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
import os
import random
import re
import time
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
MEM_PATH = os.path.join(BASE, "pet_memory.json")
PERSONA_PATH = os.path.join(BASE, "persona.json")

VERSION = 4
MIN_TOPIC = 3
MAX_TOPIC = 12
KEEP_ITEMS = 60
MAX_SOURCES = 5

# 事件卡：话题只说明「在忙什么」，卡才说明「那次到底干了啥」。
# 每个话题留最近 5 张，老的自动挤掉，文件不会膨胀。
MAX_EVENTS = 5
EVENT_ASK = 60           # 你的原话留多少字
EVENT_DID = 50           # 结论留多少字
# 三层记忆各自的条数上限。混在一起用一个数会被推送刷屏挤掉干活记录。
KEEP_EVENT = 80
KEEP_IMPRESSION = 40

DETAIL_DAYS = 7          # 7 天内：细节阶段
DORMANT_DAYS = 60        # 60 天后：淡出默认回忆
PROTECT_DAYS = 30        # 保护也有期限：30 天没再提就自动过期
FORGET_DAYS = 180        # 半年没再出现的，清掉（文件别无限长）
REPEAT_GAP = 70 * 60     # 同一话题 70 分钟内不重复说（模板翻了三倍，可以收紧）

CONSOLIDATE_HITS = 3     # 出现 3 次
CONSOLIDATE_DAYS = 2     # 且跨 2 个不同日子 → 算「长期主题」

# ---------------------------------------------------------------- 话题词典
# 越具体越靠前。输出的是能独立成立的名词短语，模板负责加动词。
TOPIC_RULES = [
    (r"信用卡|额度|还款日|账单日|卡账|卡管理|wallet", "信用卡账本", "dev"),
    (r"抖音|收藏夹|切片|短视频|直播", "抖音内容", "write"),
    (r"积分|签到|breeze|Breeze|TTS|语音|说话|桌宠|大肥鱼|桥接|hook", "大肥鱼桌宠", "dev"),
    (r"报错|崩了|白屏|超时|跑不起来|起不来", "查报错", "dev"),
    (r"奇门|命盘|姜太公|排盘|起局|jtimepower|遁甲", "奇门命盘", "work"),
    (r"风水|择日|取名|符箓|起卦|八字|客户咨询", "客户咨询", "work"),
    (r"王婆|大虾|套餐|菜品|门店|店里", "店里的事", "ops"),
    (r"飞书|收支|台账|对账|记账|流水", "记账对账", "ops"),
    (r"截图|凭证|刷卡|小票", "刷卡凭证", "ops"),
    (r"公众号|排版|小红书|封面|卡片|推文|发布", "公众号排版", "write"),
    (r"哲学|短视频|脚本|选题|文案|文章|发心|因果|空性", "写东西", "write"),
    (r"英语|作业|学习页|六年级|孩子", "孩子的英语作业", "life"),
    (r"部署|上线|workbuddy\.link|发布到", "部署上线", "dev"),
    (r"skill|技能|Skill", "折腾技能", "dev"),
    (r"记忆|memory|上下文", "整理记忆", "dev"),
    (r"插图|配图|shot list|出图|图片", "配图", "write"),
    (r"录音卡|录音笔记|语音转写|切分", "录音卡笔记", "write"),
    (r"配音|音频|字幕|剪辑", "配音剪辑", "write"),
    (r"调研|测评|竞品|市场", "看市场", "work"),
    (r"价格|定价|涨|成本|利润", "算价格", "work"),
    (r"装修|房子|租|买房|地产", "房子的事", "life"),
    (r"留学|签证|申请", "留学的事", "life"),
    (r"还款|额度提升|提额|分期", "信用卡账本", "dev"),
    (r"照片|相册|整理文件", "照片归档", "ops"),
    (r"接口|API|请求|响应|返回空", "接口", "dev"),
    (r"表格|Excel|xlsx|数据表", "表格数据", "ops"),
    # 「方案|思路|规划」这种太泛的以前单列过一条，结果 13 次点击全落在「方案」上，
    # 一条等于没有。泛词不进词典，宁可交给原话滑窗。
    (r"菜单设计|菜谱|上新|定价", "菜单", "ops"),
]

# 情绪词表：命中多者胜，平票算「平」
MOOD_WORDS = {
    "annoyed": ["报错", "崩", "失败", "不对", "又不行", "卡住", "修", "排查", "半天",
                "为什么", "搞不定", "白屏", "超时", "漏", "错"],
    "tired": ["累", "困", "熬夜", "太晚", "睡了", "眼睛", "歇", "撑不住", "先睡"],
    "smooth": ["好了", "成功", "通过", "搞定", "完成", "跑通", "上线", "修好", "对了",
               "可以了", "成了", "完了", "写完", "做好", "通了"],
}

# 明确偏好：写进 protected，永不淡化
# 前三条是「能直接执行」的行为准则；最后一条抓原话片段，别把具体内容抹掉。
PREF_RULES = [
    (r"不要废话|别废话|说重点|简洁点|直接说", "他不喜欢废话，要直接"),
    (r"别催|不要催|别烦我|安静点|别吵", "他忙的时候别催"),
    (r"叫我|称呼我|以后叫我", "注意他怎么被称呼"),
]
PREF_GENERIC = r"我喜欢|我习惯|我讨厌|受不了|我最烦|我受不了"

STOP = set("""
这个 那个 一下 什么 怎么 可以 现在 已经 还是 就是 因为 所以 我们 你们 他们
自己 东西 问题 时候 不是 没有 但是 如果 然后 一个 这样 那样 这些 那些 之后
之前 需要 应该 可能 知道 看看 帮我 我的 你的 它的 有点 一直 或者 而且 虽然
不过 其实 于是 起来 出来 下来 过来 哪些 怎样 如何
所有 全都 一些 几个 每个 整个 剩下 其他 别的
""".split())

NOUN_TAIL = "表单页图账稿文盘件据案程索接口页"

# 兜底 n-gram 最容易抽出来的垃圾：说的是「刚才那段话」本身，不是一个能念叨的事
BANNED_TOPIC = re.compile(
    r"原文|这段|那句|这句|上面|下面|之前说|刚才|内容|文本|字面|意思|情况|"
    r"部分|方式|方法|结果|原因|东西|时候|一下$|什么$|怎么$"
)

# 口头禅：长话题压成他平时真会说的短词。压完必须还能塞进句式里念通。
SHORT = {
    "信用卡账本": "账本",
    "大肥鱼桌宠": "这鱼",
    "奇门命盘": "命盘",
    "客户咨询": "客户",
    "记账对账": "对账",
    "刷卡凭证": "凭证",
    "公众号排版": "排版",
    "写东西": "稿子",
    "孩子的英语作业": "作业",
    "部署上线": "上线",
    "折腾技能": "技能",
    "整理记忆": "记忆",
    "配音剪辑": "配音",
    "看市场": "市场",
    "算价格": "价格",
    "房子的事": "房子",
    "留学的事": "留学",
    "照片归档": "照片",
    "表格数据": "表格",
}

# ---------------------------------------------------------------- 人设
# 台词由 persona.json 约束，不是随手写。改性格改那个文件。
_DEFAULT_SPEECH = {
    "max_chars": 34,
    "no_honorific": ["您", "请问", "建议您", "是否需要", "如需"],
    "no_cheer": ["加油", "相信你", "你可以的", "我理解你", "辛苦了", "我懂你"],
    "no_ai_tell": ["作为", "我注意到", "根据我的", "总结一下", "总而言之"],
    "no_emoji": True,
}


def _norm_attitude(raw):
    """性格条目规范化。v2 是纯字符串列表，v3 是 {trait, guard, bound} 对象。

    两种都收——旧人设卡不能把鱼搞哑，这是底限。
    不是列表就返回空（比如有人把 attitude 写成字符串，逐字拆开会拆出
    一串假性格，那比读不出来更糟）。
    """
    if not isinstance(raw, list):
        return []
    out = []
    for a in (raw or []):
        if isinstance(a, dict):
            trait = str(a.get("trait") or "").strip()
            if not trait:
                continue
            out.append({
                "trait": trait,
                "guard": str(a.get("guard") or "").strip(),
                "bound": [str(x).strip() for x in (a.get("bound") or [])
                          if str(x).strip()],
            })
        elif isinstance(a, str) and a.strip():
            out.append({"trait": a.strip(), "guard": "", "bound": []})
    return out


_DEFAULT_KEYS = ("address", "relation", "quirks", "honesty")


def load_persona(path=None):
    """读人设卡。缺字段就用默认值，不能让文件损坏把人搞哑。"""
    try:
        with open(path or PERSONA_PATH, encoding="utf-8") as f:
            p = json.load(f)
    except Exception:
        p = {}
    if not isinstance(p, dict):
        p = {}
    sp = p.get("speech")
    if not isinstance(sp, dict):
        sp = {}
    for k, v in _DEFAULT_SPEECH.items():
        sp.setdefault(k, v)
    p["speech"] = sp
    p["attitude"] = _norm_attitude(p.get("attitude"))
    p.setdefault("stances", [])
    p.setdefault("identity", "")
    p.setdefault("defaults", {})
    p.setdefault("layers", {})
    return p


# 派生结果先占位，由 _rebuild 填——人设改了能热加载，不用重启桌宠。
PERSONA = {}
_SPEECH = {}
MAX_CHARS = 34
_FORBID = []
_DEFAULT_BAN = []
BOUND_WORDS = []
_GUARD_WORDS = []


def _rebuild(p):
    """把 persona 字典摊平成运行时用的几张表。

    摊平是因为守卫要跑得够快（每条台词都要过一遍），
    不能每次现翻字典。改完人设调一次这个就生效。
    """
    global PERSONA, _SPEECH, MAX_CHARS, _FORBID, _DEFAULT_BAN
    global BOUND_WORDS, _GUARD_WORDS
    PERSONA = p
    _SPEECH = p["speech"]
    MAX_CHARS = int(_SPEECH.get("max_chars") or 34)

    # 禁词三张表：客套腔、打鸡血、AI 腔
    _FORBID = [w for k in ("no_honorific", "no_cheer", "no_ai_tell")
               for w in (_SPEECH.get(k) or []) if w]

    # 默认值钉死的禁用词（称呼/关系/口癖/诚实），与上面同等待遇
    ban = []
    for k in _DEFAULT_KEYS:
        seg = p.get("defaults", {}).get(k)
        if isinstance(seg, dict):
            ban += [str(x).strip() for x in (seg.get("ban") or [])
                    if str(x).strip()]
    _DEFAULT_BAN = ban

    # 性格越界词：这条性格演过头时的特征词，命中整句作废
    BOUND_WORDS = [w for a in p["attitude"] for w in a.get("bound") or []]

    seen, merged = set(), []
    for w in _FORBID + _DEFAULT_BAN + BOUND_WORDS:
        if w and w not in seen:
            seen.add(w)
            merged.append(w)
    _GUARD_WORDS = merged


def reload_persona(path=None):
    """改完 persona.json 热加载：重读、重算，不用重启。

    返回 (成功, 说明)。文件写坏了就保留原设置并回滚——
    宁可新规则没生效，也不能因为一个人设文件把鱼搞哑。
    """
    try:
        p = load_persona(path)
    except Exception as e:
        return False, f"读不了人设：{e}"
    # speech 有默认值兜底，空文件也能"看起来正常"，所以只认 attitude。
    # 一条性格都没有 = 这人设不成立，宁可不动，也不能把人设清空。
    if not p.get("attitude"):
        return False, "人设文件里没有性格条目，保留原设置"

    before = PERSONA
    try:
        _rebuild(p)
    except Exception as e:
        _rebuild(before)
        return False, f"格式不对（{e}），已回滚"

    return True, (f"已生效：{len(PERSONA['attitude'])} 条性格、"
                  f"{len(_GUARD_WORDS)} 个拦截词、{MAX_CHARS} 字上限")


# 气泡台词不带表情。之前 no_emoji 只是个没人读的字段，现在真拦。
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u2190-\u21FF]+")


def guard_line(text):
    """这句话能不能出口。返回 (ok, 原因)。

    宁可漏抓不可错杀——只拦清单里的词，不做风格判断。
    人被惹烦的从来不是语气，是那些一听就是套路的话。
    """
    s = str(text or "")
    if not s.strip():
        return False, "空"
    for w in _GUARD_WORDS:
        if w in s:
            return False, w
    if _SPEECH.get("no_emoji", True) and _EMOJI.search(s):
        return False, "表情"
    return True, ""


def layer_rule(name, key, default=None):
    """取表达分层的规则。system=说人话，character=角色腔。"""
    seg = PERSONA.get("layers", {}).get(name)
    if not isinstance(seg, dict):
        return default
    v = seg.get(key, default)
    return default if v is None else v


_rebuild(load_persona())

CN = re.compile(r"[\u4e00-\u9fa5]{2,}")
BAD_HEAD = "这那什么怎哪几每些个条只件次种位份的了在是有和就都也还又很太更最把被让给跟对向从由将拿使"
BAD_MID = "把被让给跟对向从由将拿使了着过吗呢吧啊嘛哦呀的"   # 片段中间出现介词/助词=跨词切了
BAD_TAIL = "的了在是有和就都也还又很太更最着过吗呢吧啊嘛哦呀"


# ---------------------------------------------------------------- 提炼
def _clean(text):
    t = str(text or "")
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"`[^`]*`", " ", t)
    t = re.sub(r"[A-Za-z]:\\[^\s，。；、]+", " ", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _trim(topic):
    topic = topic.strip("，。！？、；：,.!?;: 　")
    if len(topic) > MAX_TOPIC:
        topic = topic[:MAX_TOPIC]
    return topic


def detect_mood(text):
    t = _clean(text)
    score = {k: 0 for k in MOOD_WORDS}
    for k, words in MOOD_WORDS.items():
        for w in words:
            if w in t:
                score[k] += 1
    best = max(score, key=lambda k: score[k])
    return best if score[best] > 0 else "flat"


def _ngram_topic(text):
    """词典没命中时的兜底：找最像名词的 3~5 字片段。

    先把双字停顿词换成逗号，否则滑窗会跨词切出「这个报错又」这种没法念的碎片。
    宁可这一轮不记，也不冒出「把上周那批」。
    """
    t = _clean(text)
    for w in STOP:
        if len(w) >= 2:
            t = t.replace(w, "，")
    counts = {}
    for seg in CN.findall(t):
        for n in (5, 4, 3):
            for i in range(len(seg) - n + 1):
                w = seg[i:i + n]
                if w in STOP or w[0] in BAD_HEAD or w[-1] in BAD_TAIL:
                    continue
                # 片段里夹着「把/被/给/了」这类字，说明滑窗跨过了一个词边界，
                # 切出来就是「左边把源文」这种半截话。整条作废。
                if any(c in BAD_MID for c in w[1:-1]):
                    continue
                counts[w] = counts.get(w, 0) + 1
    if not counts:
        return ""
    def score(w):
        return counts[w] * 2.0 + (0.8 if w[-1] in NOUN_TAIL else 0) + 0.15 * len(w)
    best = max(counts, key=score)
    if len(best) < MIN_TOPIC or len(best) > 6 or best[-1] not in NOUN_TAIL:
        return ""
    return best


def extract_topic(text, focus=None):
    """返回 (topic, mood, tag)；提炼不出来返回 (None, mood, "")。

    focus 是用户这一轮的原话。主题优先从原话里认——助手写的摘要会把
    「帮我看下额度」改写成「已完成额度核对」，抽出来的词跟人想的不一样。
    """
    raw = _clean(text)
    fou = _clean(focus or "")
    if len(raw) < 4 and len(fou) < 4:
        return None, "flat", ""
    mood = detect_mood(raw + " " + fou)
    for src in (fou, raw):
        if not src:
            continue
        for pat, topic, tag in TOPIC_RULES:
            if re.search(pat, src):
                return _trim(topic), mood, tag
    # 词典没命中才滑窗，而且只在原话开头找（一句里最先说的那件事）
    n = _ngram_topic((fou or raw)[:26])
    if n and not BANNED_TOPIC.search(n):
        return _trim(n), mood, "misc"
    return None, mood, ""


def extract_preference(text):
    """抓到「明确偏好」就返回一句人能读的描述，否则 None。

    抓不到具体内容的，就截取原话片段（≤20 字），别用「他有偏好」这种废话代替。
    """
    raw = _clean(text)
    for pat, desc in PREF_RULES:
        if re.search(pat, raw):
            return desc
    m = re.search(PREF_GENERIC, raw)
    if m:
        seg = raw[max(0, m.start() - 2):m.end() + 14].strip("，。！？、；： ")
        seg = re.split(r"[，。！？；\n]", seg)[0]
        if 3 <= len(seg) <= 20:
            return seg
    return None


# ---------------------------------------------------------------- 存储
def _empty():
    return {"version": VERSION, "items": [], "preferences": [], "impressions": []}


def _migrate(d):
    """老档补齐到当前版本。

    v1 -> v2：只有 topic/mood/ts/hits/said_ts，补分层字段。
    v2 -> v3：items 补 layer（老的全是干活得来的 work）；新增 impressions，
              放「它对你的推测」——推测和事实分开存，混在一起就成了编造。
    """
    if not isinstance(d, dict):
        return _empty()
    ver = int(d.get("version") or 1)
    if ver >= VERSION:
        d.setdefault("preferences", [])
        d.setdefault("impressions", [])
        return d
    if ver == 2:
        for it in d.get("items") or []:
            it.setdefault("layer", "work")
        d["impressions"] = []
        d.setdefault("preferences", [])
        d["version"] = 3
        ver = 3
    if ver == 3:
        # v3 -> v4：话题下挂事件卡。老条目没有卡是正常的——那时候真的没存，
        # 不补假的，等下次真干活再长出来（历史那批已回原文重建过）。
        for it in d.get("items") or []:
            if isinstance(it, dict):
                it.setdefault("events", [])
        d.setdefault("preferences", [])
        d.setdefault("impressions", [])
        d["version"] = VERSION
        return d
    out = _empty()
    for it in d.get("items") or []:
        ts = int(it.get("ts") or 0)
        out["items"].append({
            "topic": it.get("topic"),
            "tag": it.get("tag", "misc"),
            "mood": it.get("mood", "flat"),
            "layer": "work",
            "first_ts": ts,
            "last_ts": ts,
            "hits": int(it.get("hits") or 1),
            "days": 1,
            "sources": [],
            "protected": False,
            "said_ts": int(it.get("said_ts") or 0),
            "said_count": 0,
            "events": [],
        })
    return out


def read_store(path):
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return _empty()
    if (not isinstance(raw, dict) or not isinstance(raw.get("items"), list)
            or not isinstance(raw.get("preferences", []), list)
            or not isinstance(raw.get("impressions", []), list)
            or raw.get("version", 1) not in (1, 2, 3, VERSION)):
        raise ValueError("记忆存档格式异常，已保留原文件")
    for group, key in (("items", "topic"), ("preferences", "text"), ("pins", "text"),
                       ("impressions", "text")):
        rows = raw.get(group, [])
        if not isinstance(rows, list) or any(not isinstance(r, dict) or
                not isinstance(r.get(key), str) for r in rows):
            raise ValueError("记忆条目格式异常，已保留原文件")
    growth = raw.get("growth", {})
    if (not isinstance(growth, dict) or not isinstance(growth.get("days", 0), int)
            or not isinstance(growth.get("tasks", {}), dict)
            or not isinstance(growth.get("milestones", {}), dict)):
        raise ValueError("成长记录格式异常，已保留原文件")
    return _migrate(raw)


def load_mem():
    return read_store(MEM_PATH)


def _open_lock(name, deadline=8.0):
    """打开锁文件，打不开就等一会儿再试。

    别小看这一步：并发写的时候「open 本身」就会偶发 PermissionError——
    另一个进程正开着这个文件做替换，Windows 上有瞬间的共享冲突。
    原来直接 `with open(...)`，撞上就整个写操作崩掉，几个进程一起写时
    三次里能崩两次。等 20 毫秒再开几乎总能过去。
    """
    end = time.monotonic() + deadline
    while True:
        try:
            return open(name, "a+b")
        except PermissionError:
            if time.monotonic() >= end:
                raise
            time.sleep(0.02)


@contextmanager
def edit_store(path):
    """所有写方在同一个旁路文件上锁住完整的读改写。进程退出时系统释放锁。"""
    lock = _open_lock(str(path) + ".lock")
    try:
        if os.name == "nt":
            import msvcrt
            # 8 秒不是「卡住」的线：进程退出时系统就会释放锁，不会留死锁，
            # 真抢不到只能是别的进程正在写（每次写都要 fsync，慢的时候确实要等）。
            deadline = time.monotonic() + 8.0
            while True:
                try:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.02)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        d = read_store(path)
        before = deepcopy(d)
        yield d
        if before != d:
            _write_store(path, d)
    finally:
        lock.close()


def _write_store(path, d):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


# 用户拖文件进对话时，正文里会带一串引用标记：「@"D:/我的文档/xx.zip"」「@image1:Shot」。
# 原样存进事件卡，卡片就变成「他让「@"D:/…/迁移包-r91-20261025.zip"」」，读的人
# （模型和用户本人）都看不出在干嘛。剥掉，剩下的才是真话；剥空了就别写 ask。
REF_PATH = re.compile(r'@"[^"]{0,300}"|@[\w:.\-]{3,}|[A-Za-z]:[\\/][^\s，。；、）】]*')


def _strip_refs(text):
    t = REF_PATH.sub(" ", str(text or ""))
    # 剥掉路径后会留下断口：「检查目录 中的项目」。中文里不该有这种空格。
    t = re.sub(r"\s+(?=[，。、；：）】的了吗呢吧在里中的])", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ，。、:：\"'")
    return t


def make_card(user_text="", did="", dur=0.0, tools=0, ref="", now=None):
    """一轮干活的「事件卡」：你让它干嘛、最后落成什么、花了多久。

    话题只回答「在忙什么」，卡才回答「那次到底干了啥」。都从真实记录里取，
    不经模型改写——所以原话永远是你的原话。抽不出来就给 None，不硬凑。
    """
    ask = _short(_strip_refs(user_text), EVENT_ASK)
    did = _short(_strip_refs(did), EVENT_DID)
    if not ask and not did:
        return None
    return {"ts": int(now or time.time()), "ask": ask, "did": did,
            "dur": int(dur or 0), "tools": int(tools or 0), "ref": ref or ""}


def _add_card(it, card):
    """挂一张卡。同一来源同一分钟内不重复记（Stop 偶发重复触发）。"""
    if not card:
        return
    cards = it.setdefault("events", [])
    for c in cards:
        if (c.get("ref") and c.get("ref") == card.get("ref")
                and abs(int(c.get("ts") or 0) - int(card["ts"])) < 60):
            return
    cards.insert(0, card)                 # 新的在前，取的时候不用排序
    del cards[MAX_EVENTS:]


def events_of(it, n=2):
    """取这个条目最近 n 张卡（新的在前）。"""
    cards = it.get("events") if isinstance(it, dict) else None
    return list(cards or [])[:max(0, n)]


def describe_card(card, brief=False):
    """把一张卡说成一行人话，给模型和面板共用。"""
    ask = str(card.get("ask") or "").strip()
    did = str(card.get("did") or "").strip()
    if brief:
        # 只要最要紧的那半句：有结论用结论，没有就退回原话
        return did or ask
    bits = []
    if ask:
        bits.append(f"他让「{ask}」")
    if did:
        bits.append(f"结果「{did}」")
    return "，".join(bits) or (did or ask)


def remember_turn(text, weight=1, ref="", now=None, user_text=None,
                  did=None, dur=0.0, tools=0):
    """记一轮。同话题合并；返回话题名或 None。ref 是来源（会话文件名之类）。

    did/dur/tools 有就顺手挂一张事件卡——没有也能记，只是只剩一个话题词，
    那种记忆说不出「后来怎么样了」。
    """
    now = int(now or time.time())
    topic, mood, tag = extract_topic(text, user_text)
    pref = extract_preference(text if user_text is None else user_text)
    card = make_card(user_text, did, dur, tools, ref, now) if topic else None
    with edit_store(MEM_PATH) as d:
        return _remember(d, topic, mood, tag, pref, now, ref, weight, card=card)


def _remember(d, topic, mood, tag, pref, now, ref, weight, layer="work", preview="",
              card=None):
    items = d["items"]

    if pref and not any(p.get("text") == pref for p in d["preferences"]):
        d["preferences"].append({"text": pref, "ts": now, "source": ref})
        inferred = [p for p in d["preferences"] if p.get("source") != "manual"]
        if len(inferred) > 12:
            d["preferences"].remove(inferred[0])

    if not topic:
        return None

    for it in items:
        if it.get("topic") == topic and it.get("layer", "work") == layer:
            it["hits"] = int(it.get("hits", 1)) + weight
            if _day(now) > _day(it.get("last_ts") or now):
                it["days"] = int(it.get("days", 1)) + 1
            it["last_ts"] = max(now, it.get("last_ts", 0))
            it["mood"] = mood
            if preview:
                it["preview"] = preview      # 推送类只留最后一句，不留全文
            if card and layer == "work":
                _add_card(it, card)
            src = it.setdefault("sources", [])
            if ref and not any(s.get("ref") == ref for s in src):
                src.append({"ts": now, "day": _day(now), "ref": ref})
                del src[:-MAX_SOURCES]
            # 反复卡住的事 = 没干完的事，别让它淡出；哪天说「好了」就自动解锁
            if mood == "smooth":
                it["protected"] = False
            elif (int(it["hits"]) >= CONSOLIDATE_HITS and int(it["days"]) >= CONSOLIDATE_DAYS
                  and mood in ("annoyed", "tired")):
                it["protected"] = True
            break
    else:
        row = {
            "topic": topic, "tag": tag, "mood": mood, "layer": layer,
            "first_ts": now, "last_ts": now, "hits": weight, "days": 1,
            "sources": [{"ts": now, "day": _day(now), "ref": ref}] if ref else [],
            "protected": False, "said_ts": 0, "said_count": 0,
        }
        if preview:
            row["preview"] = preview
        if card and layer == "work":
            row["events"] = [card]
        items.append(row)

    # 分层截断：混在一个额度里，推送一多就把干活记录挤出去
    alive = [i for i in items
             if i.get("protected") or now - int(i.get("last_ts") or 0) < FORGET_DAYS * 86400]
    alive.sort(key=lambda x: x.get("last_ts", 0), reverse=True)
    kept = []
    for lay, cap in (("work", KEEP_ITEMS), ("event", KEEP_EVENT)):
        kept.extend([i for i in alive if i.get("layer", "work") == lay][:cap])
    kept.sort(key=lambda x: x.get("last_ts", 0), reverse=True)
    d["items"] = kept
    return topic


# ---------------------------------------------------------------- 推送与推测
def remember_event(app, who, text, now=None, ref=""):
    """手机推送记一条（event 层）。

    主题就是来源本身——「微信·王婆大虾配送群」。这一层不需要切词，
    来源是现成的、干净的，反而是最可靠的索引。
    内容只留最后一条摘要（preview），不留全文，别把聊天记录搬进记忆。
    """
    now = int(now or time.time())
    app = (app or "手机").strip()
    who = (who or "").strip()
    topic = _trim(f"{app}·{who}" if who else app)
    if not topic:
        return None
    text = _short(text, 40)
    mood = detect_mood(text)
    with edit_store(MEM_PATH) as d:
        return _remember(d, topic, mood, "event", None, now, ref, 1,
                         layer="event", preview=text)


def _short(text, n):
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= n else t[:n] + "…"


def remember_impression(kind, text, basis=None, conf=0.5, now=None):
    """记一条推测。kind = about_you（对这个人）/ about_work（对事）。

    推测和事实分开放，就是为了让它说话时能分得清：
    「我记得你说过……」是事实，「我觉得你这周有点赶」是推测。
    """
    now = int(now or time.time())
    text = _short(text, 40)
    if not text:
        return None
    kind = kind if kind in ("about_you", "about_work") else "about_you"
    with edit_store(MEM_PATH) as d:
        rows = d.setdefault("impressions", [])
        for it in rows:
            if it.get("text") == text and it.get("kind") == kind:
                it["ts"] = now
                it["basis"] = list(basis or it.get("basis") or [])[:5]
                it["conf"] = round(float(conf), 2)
                it["wrong"] = False        # 又说了一次，之前判错的作废
                return text
        rows.append({
            "kind": kind, "text": text, "basis": list(basis or [])[:5],
            "conf": round(float(conf), 2), "ts": now, "said_ts": 0, "wrong": False,
        })
        rows.sort(key=lambda x: x.get("ts", 0), reverse=True)
        d["impressions"] = rows[:KEEP_IMPRESSION]
        return text


def impressions(kind=None, limit=10):
    """取还没被判错的推测，新的在前。"""
    rows = load_mem().get("impressions", [])
    out = [r for r in rows if not r.get("wrong") and (kind is None or r.get("kind") == kind)]
    out.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return out[:limit]


def mark_impression(text, wrong=False, said=False, now=None):
    """它说出口、或者被纠正时回写。判错的以后不再复述。

    wrong 默认 False：只是"说过了"的时候不该把这条判成错的，
    两个开关混用一个默认值会把刚说出口的推测直接弄没（踩过）。
    """
    now = int(now or time.time())
    with edit_store(MEM_PATH) as d:
        for it in d.get("impressions", []):
            if it.get("text") == text:
                if said:
                    it["said_ts"] = now
                if wrong:
                    it["wrong"] = True
                return True
    return False


# ---------------------------------------------------------------- 分层
def phase_of(it, now=None):
    """detail / gist / dormant / protected —— 参考项目的分阶段检索。

    protected 不是永久标签：30 天没再出现就自动过期，否则一件早就结束的事
    会被永远当成「未完成」念叨下去。
    """
    now = int(now or time.time())
    age_days = (now - int(it.get("last_ts") or 0)) / 86400.0
    if it.get("protected") and age_days < PROTECT_DAYS:
        return "protected"
    if age_days < DETAIL_DAYS:
        return "detail"
    if age_days < DORMANT_DAYS:
        return "gist"
    return "dormant"


def is_consolidated(it):
    """反复出现、且跨了不同日子，才算「长期主题」，不是偶然提了一嘴。"""
    return (int(it.get("hits") or 1) >= CONSOLIDATE_HITS
            and int(it.get("days") or 1) >= CONSOLIDATE_DAYS)


# ---------------------------------------------------------------- 说
# 三层：观察(obs) / 态度(view) / 动作(act)。
#
# 原来的模板只有一层，全是描述「你在干嘛」——那是镜子，不是人。
# 性格活在 view 层：它对这件事怎么看。obs 只负责把话题带出来。
# 拼一句时话题名只出现一次：obs 用真名，view 里换成「它」，否则两句都带名字，念着像复读。
LAYERS = {
    "flat": {
        "obs": [
            "{t}又来了",
            "又摊上{t}了",
            "{t}，一整天",
            "{t}还没完",
        ],
        "view": [
            "这一天就交代给{t}了",
            "{t}你倒是挺有耐心",
            "我看{t}比你还耐磨",
            "换了别人早扔了",
            "{t}这活儿，无聊但得干",
            "你俩倒是处得来",
        ],
        "act": [
            "抬头看看窗外",
            "别一口气干到底，中间歇十分钟",
            "喝口水，你从坐下就没动过",
        ],
    },
    "smooth": {
        "obs": [
            "{t}收了",
            "{t}这回通了",
            "{t}，总算弄完了",
            "{t}完了，我看见了",
        ],
        "view": [
            "这次倒没返工，稀奇",
            "行，比上次利索",
            "我记着了，{t}你能自己搞定",
            "没白折腾",
            "嘴上说不弄了，手还挺诚实",
            "{t}也没那么难吧",
        ],
        "act": [
            "歇了吧，别接着开下一个",
            "趁热把这摊子收拾干净",
            "行，那就不说了",
        ],
    },
    "annoyed": {
        "obs": [
            "{t}又卡了",
            "又栽在{t}上",
            "{t}，第三回了",
            "{t}还没通",
        ],
        "view": [
            "这不是{t}的问题，是你想一把改完",
            "{t}这玩意儿就是磨人",
            "方向不对，再试十次也一样",
            "你越急它越不通",
            "{t}跟你杠上了",
            "昨天那个法子不是能跑吗？",
        ],
        "act": [
            "停手，先去洗把脸",
            "别硬刚，换个法子",
            "这段先跳过，回头看反而清楚",
        ],
    },
    "tired": {
        "obs": [
            "{t}弄到现在",
            "都这点了，{t}",
            "{t}，眼睛都睁不开了",
            "{t}，这会儿了",
        ],
        "view": [
            "{t}明天还在，人不会",
            "这时间干{t}，明天准要返工",
            "你上次这么熬，第二天白干半天",
            "{t}不差这一小时",
            "再熬下去{t}也不会变简单",
        ],
        "act": [
            "去躺十分钟",
            "今天收工吧",
            "睡够了再想，反正也想不明白",
        ],
    },
}

# 淡出细节后（7~60 天）：只提个大概，不装作还记得细节
GIST_TEMPLATES = [
    "{t}那摊子还拖着？",
    "你好像还在惦记{t}",
    "{t}……这一阵子了吧",
    "上回说{t}，后来没下文了",
]

# 反复出现且跨天（巩固过）：语气更熟，带点「我早知道」的味道
CONSOLIDATED_TEMPLATES = [
    "又是{t}，第 {n} 回了",
    "{t}你是真放不下",
    "兜一圈还是回到{t}",
    "{t}，我就知道",
]

# 心声：最不设防的一层，性格在这里最明显
INNER_TEMPLATES = [
    "{t}又来了……",
    "他这是要跟{t}耗到天亮",
    "我要是他早不干了",
    "看他弄{t}，我尾巴都僵了",
    "算了，他乐意，我操什么心",
    "我都看会了，他还没",
    "这人是不是不知道累",
    "第三回了，他还觉得能一次搞定",
]


def _stances():
    """预编译 persona.json 里的固定立场。写错正则就跳过，不让它把人搞哑。"""
    out = []
    for s in PERSONA.get("stances") or []:
        pat = s.get("when") or ""
        says = [x for x in (s.get("say") or []) if x]
        if not pat or not says:
            continue
        try:
            out.append((re.compile(pat), says))
        except re.error:
            continue
    return out


STANCES = _stances()


def stance_for(topic):
    for pat, says in STANCES:
        if pat.search(topic or ""):
            return says
    return None


def _fill(tpl, topic, pron=False, n=1):
    s = tpl.replace("{t}", "它" if pron else topic)
    return s.replace("{n}", str(n))


def _join(a, b, sep="，"):
    return a.rstrip("，。！？ ") + sep + b.lstrip("，。！？ ")


def _trim_line(s):
    """收尾：过人格守卫、控长度、补句末标点。不合格返回 None，让调用方重试。

    长度超了只在句中标点处截——硬切会切出半句话，就是之前「话说不全」的坑。
    """
    s = (s or "").strip("，。 　")
    if not s:
        return None
    ok, _why = guard_line(s)
    if not ok:
        return None
    if len(s) > MAX_CHARS:
        cut = max(s.rfind("，", 0, MAX_CHARS), s.rfind("。", 0, MAX_CHARS))
        s = s[:cut] if cut >= 8 else s[:MAX_CHARS - 1]
        s = s.rstrip("，。 ")
    if not s:
        return None
    if s[-1] in "，、；：,;:":
        s = s[:-1]
    # 省略号本身就是收尾，别再补句号——「……。」是之前话说不全的元凶之一
    if s and s[-1] not in "。！？!?" and not s.endswith("…"):
        s += "。"
    return s


def compose_line(it, now=None, inner=False, rng=None):
    """按人设把三层拼成一句话。这里不写盘。"""
    rng = rng or random
    now = int(now or time.time())
    raw_topic = it.get("topic") or ""
    topic = SHORT.get(raw_topic, raw_topic)
    n = int(it.get("hits") or 1)

    if inner:
        return _trim_line(_fill(rng.choice(INNER_TEMPLATES), topic, n=n))

    ph = phase_of(it, now)
    if ph == "gist":
        return _trim_line(_fill(rng.choice(GIST_TEMPLATES), topic, n=n))

    # 老问题才拿立场出来讲。刚提一嘴的事就上价值，那是说教，不是性格。
    if (is_consolidated(it) or ph == "protected") and rng.random() < 0.25:
        says = stance_for(raw_topic)
        if says:
            return _trim_line(_fill(rng.choice(says), topic, n=n))

    if is_consolidated(it) and it.get("mood") != "smooth" and rng.random() < 0.45:
        return _trim_line(_fill(rng.choice(CONSOLIDATED_TEMPLATES), topic, n=n))

    lay = LAYERS.get(it.get("mood") or "flat") or LAYERS["flat"]
    views = lay["view"]
    # 能接在 obs 后面的只有不含话题名的句子，否则会拼出「它你倒是挺有耐心」这种
    followers = [v for v in views if "{t}" not in v] or views
    with_t = [v for v in views if "{t}" in v]
    # obs 自身带逗号的只单独说，否则「都这点了，稿子，你上次这么熬」三个逗号连成一串
    plain_obs = [o for o in lay["obs"] if "，" not in o] or lay["obs"]

    mode = rng.choices(["obs_view", "view", "view_act", "obs"],
                       [0.45, 0.28, 0.15, 0.12])[0]
    if mode == "obs":
        return _trim_line(_fill(rng.choice(lay["obs"]), topic, n=n))
    if mode == "view_act":
        # 单独起头的态度句必须带话题名，否则「你俩倒是处得来。喝口水」没头没尾
        return _trim_line(_join(_fill(rng.choice(with_t or views), topic, n=n),
                                rng.choice(lay["act"]), sep="。"))
    if mode == "obs_view":
        obs = _fill(rng.choice(plain_obs), topic, n=n)
        return _trim_line(_join(obs, _fill(rng.choice(followers), topic, n=n)))
    # 单说态度句：优先带话题名的，否则「换了别人早扔了」听着没头没尾
    pool = with_t if (with_t and rng.random() < 0.8) else views
    return _trim_line(_fill(rng.choice(pool), topic, n=n))


class AmbientMemory:
    """给桌宠用：挑一个话题，套一句台词。拿不到就返回 None，由调用方兜底。"""

    def __init__(self, path=None):
        self.path = path or MEM_PATH
        self._last_topic = ""

    def _data(self):
        return read_store(self.path)

    def items(self):
        return self._data()["items"]

    def preferences(self):
        return [p for p in self._data().get("preferences", []) if p.get("text")]

    def pick(self, now=None):
        """加权抽样：新鲜的高，淡出的不提。这里不写盘——查询不等于强化。

        先按 2 小时的重复间隔挑；一条能说的都没有时，把间隔放宽到 30 分钟
        再挑一次。否则只有一条记忆的日子，说完一次就两小时没词了。
        """
        now = int(now or time.time())

        def build(gap, skip_last=True):
            pool, weights = [], []
            for it in self.items():
                topic = it.get("topic")
                if not topic:
                    continue
                if skip_last and topic == self._last_topic:
                    continue
                if now - int(it.get("said_ts") or 0) < gap:
                    continue
                ph = phase_of(it, now)
                if ph == "dormant":
                    continue
                age = now - int(it.get("last_ts") or 0)
                base = 5.0 if age < 3600 else (3.0 if age < 86400 else 1.0)
                factor = {"protected": 1.0, "detail": 1.0, "gist": 0.6}[ph]
                if is_consolidated(it):
                    factor *= 1.5
                pool.append(it)
                weights.append(base * factor * (1.0 + 0.2 * min(int(it.get("hits", 1)), 5)))
            return pool, weights

        for gap, skip_last in ((REPEAT_GAP, True), (1800, False)):
            pool, weights = build(gap, skip_last)
            if pool:
                return random.choices(pool, weights=weights, k=1)[0]
        return None

    def line_for(self, it, now=None, inner=False):
        """拼一句。碰到禁词/超长会重试几次，全不合格返回 None 交给上层回落。"""
        for _ in range(6):
            s = compose_line(it, now=now, inner=inner)
            if s:
                return s
        return None

    def next_line(self, now=None, inner=False):
        it = self.pick(now=now)
        if not it:
            return None
        line = self.line_for(it, now=now, inner=inner)
        if not line:
            return None
        self._last_topic = it["topic"]
        self.mark_said(it["topic"], now=now)
        return line

    def mark_said(self, topic, now=None):
        """说出口才记一笔。这不是强化——强化只由「又出现在对话里」触发。"""
        now = int(now or time.time())
        with edit_store(self.path) as d:
            for it in d["items"]:
                if it.get("topic") == topic:
                    it["said_ts"] = now
                    it["said_count"] = int(it.get("said_count") or 0) + 1
                    break

    def forget(self, topic):
        with edit_store(self.path) as d:
            before = len(d["items"])
            d["items"] = [i for i in d["items"] if i.get("topic") != topic]
            return before - len(d["items"])

    def summary(self, now=None):
        now = int(now or time.time())
        items = self.items()
        if not items:
            return "还没有记忆"
        mark_of = {"protected": "护", "detail": "近", "gist": "远", "dormant": "睡"}
        out = []
        for it in items[:8]:
            ph = phase_of(it, now)
            star = "★" if is_consolidated(it) else ""
            out.append(f"[{mark_of[ph]}]{it['topic']}{star}×{it.get('hits', 1)}")
        return " ".join(out)


if __name__ == "__main__":
    import sys
    argv = sys.argv[1:]
    if argv and argv[0] == "--dump":
        print(json.dumps(load_mem(), ensure_ascii=False, indent=2))
    elif argv and argv[0] == "--add":
        for s in argv[1:]:
            print("记下:", remember_turn(s, ref="manual"))
    elif argv and argv[0] == "--forget":
        for s in argv[1:]:
            print(f"清掉 {s}：{AmbientMemory().forget(s)} 条")
    elif argv and argv[0] == "--test":
        for s in argv[1:]:
            topic, mood, tag = extract_topic(s)
            print(f"{mood:8} {tag:6} -> {topic}   <= {s[:40]}")
    else:
        m = AmbientMemory()
        print(m.summary())
        for p in m.preferences():
            print("偏好:", p["text"])
