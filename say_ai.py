# -*- coding: utf-8 -*-
"""大肥鱼 · 说话前先想一下。

事实句 → 人设（persona.json）+ 记忆库（pet_memory.json）+ 具体内容 → 一句带看法的话。

跟 douyin_take 的分工：
    douyin_take  专门对付「一条抖音收藏」，要读长摘要、要缓存一辈子；
    say_ai       对付所有其他播报：手机通知、日报收尾、待办、吃文件。
                 短、快、每条都得当场说，所以自带限流和预算。

三条硬底线，比「说得好听」优先：
  1. **数字不许错**。模型改一个金额，账就错了。输出里的数字必须能在原文里找到，
     否则整句作废、退回模板。宁可没看法，不能有错话。
  2. **紧急只讲事实**。地震预警这种，人设规定「那种时候不该贫」，也不该花 2 秒等模型。
     直接不调。
  3. **不花没必要的钱**。同类 45 秒内只 AI 一次、同一条内容 1 小时内复用、
     每小时/每天/每天花费都有上限。超了就退回模板，播报不哑。

没 key、断网、模型抽风一律返回 ""，由调用方退回原模板——这一层永远不许抛异常。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "ai_talk.json")
STATE_PATH = os.path.join(BASE, "ai_talk_state.json")
KEY_PATH = os.path.join(BASE, "deepseek_key.txt")
PERSONA_PATH = os.path.join(BASE, "persona.json")
MEM_PATH = os.path.join(BASE, "pet_memory.json")

API_URL = "https://api.deepseek.com/v1/chat/completions"
# 改了 prompt 或者校验规则就 +1：缓存 key 带这个号，旧缓存会自动失效，
# 否则新规矩上线了，用户听到的还是按旧规矩生成的那批句子。
PROMPT_VERSION = 3

DEFAULTS = {
    "enabled": True,
    "model": "deepseek-chat",
    "per_hour": 24,
    "per_day": 200,
    "daily_yuan": 1.2,
    "same_kind_gap": 45,
    "cache_ttl": 3600,
    "timeout": 5.5,
    "max_chars": 60,
    # 分场景开关：不想让某类播报花钱，单独关这一项就行，不用停下整个模块。
    "scopes": {"notify": True, "day": True, "file": True},
}

NOTIFY_KINDS = {"money", "work", "chat", "tech", "notice", "flood", "urgent"}


def _scope_of(kind):
    return "notify" if kind in NOTIFY_KINDS else str(kind or "notify")

# deepseek-chat 现价：输入 ¥2/百万（未命中缓存）、输出 ¥8/百万。
# 只用来记个大概，不是账单——所以故意算高一点，花超了能早发现。
PRICE_IN = 2.0 / 1_000_000
PRICE_OUT = 8.0 / 1_000_000

KIND_LABEL = {
    "money": "钱的事（到账、扣款、账单、额度）",
    "work": "店里和客户的事（订单、催单、预约、对账）",
    "chat": "有人找他（群里点名、私聊）",
    "tech": "电脑或程序的事（报错、部署、构建）",
    "notice": "普通通知",
    "flood": "同一个 App 连发好几条",
    "file": "拖进来的文件",
    "weather": "天气",
    "day": "一天的收尾",
    "phone": "手机没连上",
    "phone_back": "手机重新连上",
}

_cfg_lock = threading.Lock()
_cfg_cache = {"mtime": None, "v": dict(DEFAULTS)}
_state_lock = threading.Lock()


# ---------------------------------------------------------------- 配置

def cfg():
    """ai_talk.json 按 mtime 热加载：改完保存就生效，不用重启监听。"""
    try:
        mtime = os.stat(CFG_PATH).st_mtime
    except OSError:
        return dict(DEFAULTS)
    with _cfg_lock:
        if _cfg_cache["mtime"] == mtime:
            return dict(_cfg_cache["v"])
        v = dict(DEFAULTS)
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for k in DEFAULTS:
                    if k not in raw:
                        continue
                    # scopes 这种字典项要合并，不能整块替换：
                    # 用户只写 {"notify": false} 时，别的场景不该跟着消失。
                    if isinstance(DEFAULTS[k], dict) and isinstance(raw[k], dict):
                        merged = dict(DEFAULTS[k])
                        merged.update(raw[k])
                        v[k] = merged
                    else:
                        v[k] = raw[k]
        except (OSError, ValueError) as exc:
            print("[ai] 配置读不动，用默认值:", exc)
        _cfg_cache.update(mtime=mtime, v=v)
        return dict(v)


def load_key(cfg_key=""):
    k = str(cfg_key or "").strip()
    if k:
        return k
    try:
        with open(KEY_PATH, encoding="utf-8") as f:
            return "".join(f.read().split())
    except OSError:
        return ""


# ---------------------------------------------------------------- 用量台账

def _state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("day", "")
            d.setdefault("day_calls", 0)
            d.setdefault("day_yuan", 0.0)
            d.setdefault("hour", "")
            d.setdefault("hour_calls", 0)
            d.setdefault("kinds", {})
            d.setdefault("cache", {})
            return d
    except (OSError, ValueError):
        pass
    return {"day": "", "day_calls": 0, "day_yuan": 0.0,
            "hour": "", "hour_calls": 0, "kinds": {}, "cache": {}}


def _save_state(d):
    tmp = STATE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
    except OSError as exc:
        print("[ai] 台账写不进去:", exc)


def _roll(d, now=None):
    """跨天/跨小时清零。缓存另算，它有自己的 ttl。"""
    now = now or time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    hour = time.strftime("%Y-%m-%d-%H", time.localtime(now))
    if d.get("day") != day:
        d["day"] = day
        d["day_calls"] = 0
        d["day_yuan"] = 0.0
    if d.get("hour") != hour:
        d["hour"] = hour
        d["hour_calls"] = 0
    return d


def budget_left(now=None):
    """还能不能再调一次。返回 (能吗, 原因)。"""
    c = cfg()
    if not c.get("enabled", True):
        return False, "关闭"
    d = _roll(_state(), now)
    if d["day_calls"] >= int(c["per_day"]):
        return False, "今天条数用完了"
    if d["hour_calls"] >= int(c["per_hour"]):
        return False, "这一小时够了"
    if float(d["day_yuan"]) >= float(c["daily_yuan"]):
        return False, "今天的花费用完了"
    return True, ""


def stats():
    """给面板和自检看：今天用了几条、花了多少、缓存多少条。"""
    d = _roll(_state())
    return {"day": d["day"], "calls": d["day_calls"],
            "yuan": round(float(d["day_yuan"]), 4),
            "hour_calls": d["hour_calls"], "cached": len(d.get("cache") or {})}


# ---------------------------------------------------------------- 人设

_persona_lock = threading.Lock()
_persona_cache = {"mtime": None, "text": ""}


def persona_block():
    """把 persona.json 压成一段能进 prompt 的话。

    只取「怎么说」的部分：trait（是什么）、guard（不许怎么演）、禁词、never。
    stances 不取——那是给模板用的短句，塞进 prompt 只会让模型抄。
    """
    try:
        mtime = os.stat(PERSONA_PATH).st_mtime
    except OSError:
        return ""
    with _persona_lock:
        if _persona_cache["mtime"] == mtime and _persona_cache["text"]:
            return _persona_cache["text"]
        try:
            with open(PERSONA_PATH, encoding="utf-8") as f:
                p = json.load(f)
        except (OSError, ValueError):
            return ""
        lines = []
        ident = str(p.get("identity") or "").strip()
        if ident:
            lines.append(ident)
        att = [a for a in (p.get("attitude") or []) if isinstance(a, dict)]
        if att:
            lines.append("性格：")
            for a in att:
                t = str(a.get("trait") or "").strip()
                g = str(a.get("guard") or "").strip()
                if t:
                    lines.append("- " + t + ("（" + g + "）" if g else ""))
        sp = p.get("speech") or {}
        ban = []
        for k in ("no_honorific", "no_cheer", "no_ai_tell"):
            ban += [str(x) for x in (sp.get(k) or [])]
        for a in att:
            ban += [str(x) for x in (a.get("bound") or [])]
        dflt = p.get("defaults") or {}
        for grp in dflt.values():
            if isinstance(grp, dict):
                ban += [str(x) for x in (grp.get("ban") or [])]
        ban = [b for b in dict.fromkeys(ban) if b]
        if ban:
            lines.append("这些词一个都不许出现：" + "、".join(ban[:40]))
        never = [str(x).strip() for x in (p.get("never") or []) if str(x).strip()]
        if never:
            lines.append("不做的：" + "、".join(never))
        text = "\n".join(lines)
        _persona_cache.update(mtime=mtime, text=text)
        return text


def forbid_words():
    """禁词表，用来机械校验模型输出（跟 persona 里那份同源）。"""
    try:
        with open(PERSONA_PATH, encoding="utf-8") as f:
            p = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    sp = p.get("speech") or {}
    for k in ("no_honorific", "no_cheer", "no_ai_tell"):
        out += [str(x) for x in (sp.get(k) or [])]
    for a in (p.get("attitude") or []):
        if isinstance(a, dict):
            out += [str(x) for x in (a.get("bound") or [])]
    for grp in (p.get("defaults") or {}).values():
        if isinstance(grp, dict):
            out += [str(x) for x in (grp.get("ban") or [])]
    return [w for w in dict.fromkeys(out) if w]


# ---------------------------------------------------------------- 记忆库

_mem_lock = threading.Lock()
_mem_cache = {"mtime": None, "ts": 0.0, "text": ""}
MEM_TTL = 300        # 记忆库是别的进程在写，5 分钟看一次够了

# 太泛的话题喂进去只会招来硬扯（「电费该记进方案里」），不如不给
MEM_TOO_GENERIC = {"方案", "思路", "问题", "东西", "事情", "计划", "规划", "整理记忆"}


def memory_context(days=7, limit=5, now=None):
    """他最近在忙什么——从 pet_memory 的活跃话题里捞。

    只取 7 天内出现过的（detail 阶段），不取淡出的话题：
    说「你上周弄的那个」可以，说「你两个月前弄的那个」就像在瞎猜。
    """
    try:
        mtime = os.stat(MEM_PATH).st_mtime
    except OSError:
        return ""
    now = now or time.time()
    with _mem_lock:
        fresh = (time.monotonic() - _mem_cache["ts"] < MEM_TTL)
        if _mem_cache["mtime"] == mtime and fresh:
            return _mem_cache["text"]
        try:
            with open(MEM_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return ""
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return ""
        live = []
        for it in items:
            if not isinstance(it, dict):
                continue
            # 只算「他在忙什么」。推送层（微信·某群）是别人找他，不是他在忙，
            # 混进来会让模型说出「你最近在忙王婆大虾配送群」这种怪话。
            if str(it.get("layer") or "work") != "work":
                continue
            topic = str(it.get("topic") or "").strip()
            last = float(it.get("last_ts") or 0)
            hits = int(it.get("hits") or 0)
            if not topic or len(topic) < 2 or now - last > days * 86400:
                continue
            # 只出现一次、又没分类的，是 n-gram 抠出来的碎片（「移交文」「上显示的文」）。
            # 这种进 prompt 只会让模型以为他真在忙这玩意，宁可少一条。
            if str(it.get("tag") or "") == "misc" and hits < 2:
                continue
            if topic in MEM_TOO_GENERIC:
                continue
            live.append((last, hits, topic, it))
        live.sort(key=lambda x: x[:2], reverse=True)
        picked = [t for _, _, t, _ in live[:limit]]
        if not picked:
            _mem_cache.update(mtime=mtime, ts=time.monotonic(), text="")
            return ""
        text = "他最近在忙：" + "、".join(picked) + "。"
        # 再挂一两行「具体那次干了啥」。只给最近的两个话题，多了就变成流水账，
        # 模型反而抓不住重点，还白烧 token。
        rows = []
        for _, _, topic, it in live[:3]:
            ev = it.get("events") or []
            if not ev or not isinstance(ev[0], dict):
                continue
            line = _card_line(ev[0])
            if line:
                rows.append(f"{topic}：{line}")
            if len(rows) >= 2:
                break
        if rows:
            text += "最近的事：" + "；".join(rows) + "。"
        _mem_cache.update(mtime=mtime, ts=time.monotonic(), text=text)
        return text


def _card_line(card, cap=46):
    """一张事件卡压成半句话：他让「…」，结果「…」。"""
    ask = str(card.get("ask") or "").strip()
    did = str(card.get("did") or "").strip()
    if ask and did:
        s = f"他让「{ask}」，结果「{did}」"
    elif did:
        s = f"结果「{did}」"
    elif ask:
        s = f"他让「{ask}」"
    else:
        return ""
    return s if len(s) <= cap else s[:cap] + "…"


# ---------------------------------------------------------------- 校验

def _nums(text):
    """抽数字。把 3,200 这种千分位先抹平，不然整句都校验不过。"""
    s = str(text or "").replace(",", "").replace("，", "")
    return set(re.findall(r"\d+(?:\.\d+)?", s))


def _strip_marks(s):
    """剥掉模型顺手带出来的排版残留。

    实测出现过「李四说报价明天上午发你，别催了#5；」，那个 #5 是模型自己
    给条目打的编号。直接送进 TTS 会念成「井号五」。这里只剥有标记特征的，
    不碰正常数字（11月19日、3218.00 元原样留着）。
    """
    s = re.sub(r"[#＃]\s*\d+", "", s)               # #5 ／ ＃5
    s = re.sub(r"\*{2,3}([^*]*?)\*{2,3}", r"\1", s)  # **粗体**
    s = re.sub(r"(?<!\w)_{2,3}([^_]*?)_{2,3}(?!\w)", r"\1", s)   # __强调__
    s = re.sub(r"^\s*\d{1,2}\s*[.、)）]\s*", "", s)   # 行首 1. ／ 2、
    s = re.sub(r"^\s*[（(]\s*\d{1,2}\s*[)）]\s*", "", s)          # 行首 (1)
    s = re.sub(r"`([^`]*)`", r"\1", s)              # `代码`
    return s


def _clean(text, limit):
    s = " ".join(str(text or "").split())
    s = s.strip("「」\"'“”‘’ ")
    s = _strip_marks(s)
    for half, full in ((",", "，"), (";", "；"), (":", "：")):
        s = re.sub(r"(?<=[\u4e00-\u9fa5])" + re.escape(half), full, s)
    s = re.sub(r"[，、；：]+$", "", s)
    if not s:
        return ""
    if s[-1] not in "。！？…":
        s += "。"
    if len(s) <= limit:
        return s
    # 超长不截。试过「按小句收到 limit」，结果常留下「记得去记上；钱到了不记」这种
    # 半句话——念出来比不说难听，还容易把意思说反。宁可弃用退回模板：
    # 模板本身是通顺的，AI 只是加成，不是唯一出路。
    return ""


def _require_of(kind, fact):
    """哪些词必须原样出现在输出里。

    只对群 @ 下手：群名是「哪个店、哪个群」的唯一线索，模型特别爱缩成
    「配送群」。普通通知的 App 名不卡——「尾号6688那期账单」不提建行也听得懂，
    卡太死只会让退回模板的比例升高。
    """
    if kind != "chat":
        return ()
    m = re.match(r"^([^：:]{2,20})[：:]", str(fact or ""))
    return (m.group(1).strip(),) if m else ()


def check_line(line, fact, hint="", limit=None, require=()):
    """一句话能不能用。返回 (能不能, 原因)。

    这是这一层唯一的安全阀：模型可以说错观点，不可以说错数。
    """
    s = str(line or "").strip()
    if not s:
        return False, "空"
    c = cfg()
    cap = int(limit or c["max_chars"])
    if len(s) > cap + 6:
        return False, f"太长({len(s)})"
    # 群名这类「定位用的名字」丢了，整句就废了——用户有好几个群、好几个店，
    # 光说「配送群」他不知道是哪一个。这条只能硬卡，prompt 说了模型也不总听。
    for w in (require or ()):
        if w and w not in s:
            return False, f"丢了关键名({w})"
    for w in forbid_words():
        if w and w in s:
            return False, f"禁词({w})"
    # 清洗后还带着排版符号，说明模型这次输出不对劲（编号、加粗、代码块）。
    # 这种句子念出来会带出「井号」「星号」，不如退回模板。
    for ch in ("#", "＃", "*", "`"):
        if ch in s:
            return False, f"残留排版符号({ch})"
    if "…" in s or "..." in s:
        return False, "省略号"
    # 数字必须能在原文里找到。原文 = 事实句 + 正文。
    # 8842.6 和 8842.60 是同一个数，模型换了个写法不该算错——按数值比。
    src = _nums(fact) | _nums(hint)
    src_val = set()
    for x in src:
        try:
            src_val.add(round(float(x), 4))
        except ValueError:
            pass
    for n in _nums(s):
        if n in src:
            continue
        try:
            if round(float(n), 4) in src_val:
                continue
        except ValueError:
            pass
        return False, f"凭空出现数字({n})"
    return True, ""


# ---------------------------------------------------------------- 主入口

def _cache_key(kind, fact, hint, mode="full"):
    raw = f"{PROMPT_VERSION}|{mode}|{kind}|{fact}|{str(hint or '')[:80]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _cache_get(key):
    d = _state()
    row = (d.get("cache") or {}).get(key)
    if not isinstance(row, dict):
        return ""
    if time.time() - float(row.get("ts") or 0) > float(cfg()["cache_ttl"]):
        return ""
    return str(row.get("line") or "")


def _cache_put(key, line, yuan):
    d = _state()
    cache = d.setdefault("cache", {})
    cache[key] = {"line": line, "ts": int(time.time()), "yuan": round(yuan, 6)}
    # 台账别无限长：留最近 300 条
    if len(cache) > 300:
        keep = sorted(cache.items(), key=lambda kv: float((kv[1] or {}).get("ts") or 0),
                      reverse=True)[:300]
        d["cache"] = dict(keep)
    _save_state(d)


def opine(fact, kind="notice", hint="", extra="", key="", timeout=None,
          allow_urgent=False, mode="full"):
    """事实 → 一句带看法的话。返回 "" 表示「这层没话说，用你自己的模板」。

    mode="full"   事实 + 看法一起给，用于一条通知（它没说别的，得自己把事说清）
    mode="remark" 只给看法，事实前面已经念过了，不许再重复一遍
                  （日报收尾、待办记完、文件吃完都是这种）

    调用方一律写成 `opine(...) or dress(fact, kind) or fact`：
    这一层永远只是加法，不能成为播报的单点故障。
    """
    fact = str(fact or "").strip()
    if not fact:
        return ""
    c = cfg()
    if not c.get("enabled", True):
        return ""
    if not (c.get("scopes") or {}).get(_scope_of(kind), True):
        return ""
    # 紧急不调：人设规定那种时候不该贫，而且 2 秒的等待本身就是风险
    if kind == "urgent" and not allow_urgent:
        return ""

    ok, why = budget_left()
    if not ok:
        print(f"[ai] 跳过（{why}）")
        return ""

    # 缓存先于限流：同一条内容再来一次是免费的，没必要为了「同类间隔」把它掐掉。
    ck = _cache_key(kind, fact, hint, mode)
    hit = _cache_get(ck)
    if hit:
        return hit

    # 同类间隔：同一种通知一分钟内来三条，只说一次看法就够了
    now = time.time()
    d = _state()
    gap = float(c.get("same_kind_gap") or 0)
    if gap > 0:
        last = float((d.get("kinds") or {}).get(kind) or 0)
        if now - last < gap:
            return ""

    k = load_key(key)
    if not k:
        return ""

    limit = int(c["max_chars"]) if mode == "full" else max(26, int(c["max_chars"]) - 14)
    prompt = _build_prompt(fact, kind, hint, extra, mode)
    payload = {
        "model": str(c.get("model") or "deepseek-chat"),
        "messages": prompt,
        "temperature": 0.85,
        "max_tokens": 160,
    }
    try:
        import requests
        r = requests.post(API_URL, json=payload, timeout=float(timeout or c["timeout"]),
                          headers={"Authorization": "Bearer " + k,
                                   "Content-Type": "application/json"})
        if r.status_code in (401, 402):
            print(f"[ai] key 不可用({r.status_code})，退回模板")
            return ""
        r.raise_for_status()
        data = r.json()
        raw = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        yuan = (int(usage.get("prompt_tokens") or 0) * PRICE_IN
                + int(usage.get("completion_tokens") or 0) * PRICE_OUT)
    except Exception as exc:      # noqa: BLE001  这一层不许把播报弄死
        print("[ai] 调用失败，退回模板:", type(exc).__name__, exc)
        return ""

    line = _clean(raw, limit)
    good, why = check_line(line, fact, hint, limit, _require_of(kind, fact))
    if not good:
        print(f"[ai] 弃用（{why}）：{line}")
        _log_call(kind, yuan, "")
        return ""

    _log_call(kind, yuan, line)
    _cache_put(ck, line, yuan)
    return line


def _log_call(kind, yuan, line):
    with _state_lock:
        d = _roll(_state())
        d["day_calls"] = int(d.get("day_calls") or 0) + 1
        d["hour_calls"] = int(d.get("hour_calls") or 0) + 1
        d["day_yuan"] = float(d.get("day_yuan") or 0) + float(yuan)
        if line:
            d.setdefault("kinds", {})[kind] = time.time()
        # 按进程记账：桌宠、通知监听、自检脚本都会写这个文件，
        # 只有分得清是谁调的，才答得上「桌宠自己到底走没走 DeepSeek」。
        pids = d.setdefault("pids", {})
        me = str(os.getpid())
        pids[me] = int(pids.get(me) or 0) + 1
        _save_state(d)


def _build_prompt(fact, kind, hint, extra, mode="full"):
    label = KIND_LABEL.get(kind, "普通通知")
    limit = int(cfg()["max_chars"]) if mode == "full" else max(26, int(cfg()["max_chars"]) - 14)
    # 报给模型的目标要比硬上限紧一档。实测：按上限报「不超过 50 字」，模型普遍写到
    # 56~58 字，正好撞上「超长整句作废」，四成通知白花一次钱还退回模板。
    # 报 42 字它落在 45~52，全部落在 60 的硬线内——留余量比事后截断便宜。
    tell = max(22, limit - 16)
    if kind == "file":
        # 吃文件只给类型和文件名，没读过内容。规则也只留用得上的几条：
        # 「金额照抄」「群名照抄」这类在这是纯浪费 token。
        head = [
            "你是「大肥鱼」，一条住在电脑里的胖鱼，跟他一起上工。你俩是同伴，不是助手。",
            persona_block(),
            "说话规矩（硬约束）：",
            f"- 一句话说完，不超过 {tell} 字（写超了整句会被丢掉，宁可短）。",
            "- 用「你」称呼他。",
            "- 你只看到文件名和类型，从来没打开过这些文件：不许猜里面写了什么，"
            "不许猜是谁发的、办的什么事。",
            "- 文件名照抄，不许改写、缩写、翻译。",
            "- 不用 emoji，不堆感叹号，不写「作为」「我注意到」「总结一下」。",
        ]
        user = [f"情况：{fact}", ""]
        if mode == "remark":
            user.extend([
                "他刚听完「吃掉了什么」，你只补一句自己的判断。",
                "要求：",
                "1. 不复述数量和文件名，一个都别再说。",
                "2. 只说该不该清、清得对不对、下次注意什么，要有立场。",
                "3. 别点评文件内容——你没看过。",
                "4. 只输出这一句话，不要引号、不要解释、不要分点。",
            ])
        else:
            user.extend([
                "把这件事念给正在干活的他听。要求：",
                "1. 一句话说完，带一句你的看法。",
                "2. 别点评文件内容——你没看过。",
                "3. 只输出这一句话，不要引号、不要解释、不要分点。",
            ])
        return [{"role": "system", "content": "\n".join(x for x in head if x)},
                {"role": "user", "content": "\n".join(user)}]

    head = [
        "你是「大肥鱼」，一条住在电脑里的胖鱼，跟他一起上工。你俩是同伴，不是助手。",
        persona_block(),
        (memory_context() + "（只是背景，多数通知跟这些没关系）") if memory_context() else "",
        "说话规矩（硬约束）：",
        f"- 一句话说完，不超过 {tell} 字（写超了整句会被丢掉，宁可短）。不说两句。",
        "- 用「你」称呼他。",
        "- **数字、金额、人名、单号一律照抄原文，不许改、不许补、不许自己算。**",
        "- 事实开头那个名字（群名、App 名）照抄全称，不许简写。",
        "- 不确定的事不猜，看不懂就说看不懂。",
        "- 不用 emoji，不堆感叹号，不写「作为」「我注意到」「总结一下」。",
    ]
    sys_text = "\n".join(x for x in head if x)
    user = [f"一条{label}的通知：", f"事实：{fact}"]
    if hint:
        user.append("原始内容：" + " ".join(str(hint).split())[:200])
    if extra:
        user.append("补充：" + str(extra)[:300])
    user.append("")
    if mode == "remark":
        user.append("这些事他刚听你说完了。你只补一句自己的判断，别再复述一遍事实。")
        user.append("要求：")
        user.append("1. 不重复已有的数字、人名、时间；一个都别再说。")
        user.append("2. 说你的看法：最该先弄哪件、这事值不值、明天要防什么。要有立场。")
        user.append("3. 联系得上他手头的事就联系，八竿子打不着就一个字不提。")
        user.append("4. 只输出这一句话，不要引号、不要解释、不要分点。")
        return [{"role": "system", "content": sys_text},
                {"role": "user", "content": "\n".join(user)}]
    user.append("把这条念给正在干活的他听。要求：")
    user.append("1. 先把最关键的事实说清（谁、什么事、多少钱、要他做什么），再补一句你的看法。")
    user.append("2. 看法要有立场，别中立客套。可以嫌烦、可以提醒他别拖、可以说这事没用。")
    user.append("3. 只有真相关才联系他手头的事（银行到账 → 账本；公众号催稿 → 写稿）。"
                "八竿子打不着就一个字不提，硬扯比不说更糟。")
    user.append("4. 只输出这一句话，不要引号、不要解释、不要分点。")
    return [{"role": "system", "content": sys_text},
            {"role": "user", "content": "\n".join(user)}]


def explain(fact, kind="notice", hint="", mode="full"):
    """自检用：把要发出去的 prompt 拿出来看，不花钱。"""
    return _build_prompt(fact, kind, hint, "", mode)
