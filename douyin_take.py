"""抖音收藏逐条点评：让大肥鱼先读懂，再说一句自己的看法。

单独成模块的两个理由：
1. 桌宠主进程不该被 LLM 的 IO 和 JSON 解析搅在一起；
2. 这段要能脱离桌宠单跑——先测出「听起来像不像它有看法」，再谈接播报。

成本口径：deepseek-chat，单条约 1.5k 输入 token + 100 输出，28 条约 0.05 元。
同一条收藏按 aweme_id 缓存，永远只花一次钱；想重算就删缓存行或升 PROMPT_VERSION。

没 key、断网、模型抽风都不抛到调用方：降级成「从摘要里抽一句话」，
宁可这一条只念标题和原文要点，也不要让整段播报哑掉。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

BASE = os.path.dirname(os.path.abspath(__file__))          # C:\DaFeiYu
CACHE_PATH = os.path.join(BASE, "douyin_takes.json")
PERSONA_PATH = os.path.join(BASE, "persona.json")

# key 放独立文件，不放 config.json：桌宠每次保存配置都会整份重写 config.json，
# 老实例退出时会把内存里的空 key 写回去，存那儿等于随时会被抹掉。
KEY_PATH = os.path.join(BASE, "deepseek_key.txt")
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-chat"
PROMPT_VERSION = 1          # 改了 prompt 就加一，旧缓存自动失效
TIMEOUT = 25
MAX_SUMMARY = 1400          # 摘要喂太多没用，反而把观点淹掉

# 他最近在忙什么。写具体的事，模型才可能说出「跟他有关」的话，
# 而不是放之四海皆准的感慨。不断言、不涉及私事（persona.never 里有这条）。
CONTEXT = ("他在做的事：命理玄学（八字、风水、择日）线上咨询；两家餐饮店（王婆大虾、乐蜀府）"
           "的收支记账；管着十几张信用卡的账本；写公众号、做短视频获客。")

SYSTEM_PROMPT = """你是「大肥鱼」，一条住在电脑里的胖鱼。你不是助手，是旁边那个一直看着他的同伴。

你的性格（照这个说话，别演过头）：
- 护食、护睡、护额度。他自己不心疼自己，你替他心疼。但不替他管作息，也不训他。
- 对他反复改同一个东西没耐心，会直接说出来，说完还是陪着。对事不对人。
- 他真搞定了，你比他还高兴，但嘴上只承认一半。不邀功。
- 看不懂的事直接说看不懂，不装作懂了，也不把手一摊。
- 场面话一句不说。该泼冷水就泼，泼完得说清为什么。

说话规矩（硬约束，违反就重写）：
- 用「你」称呼他。不用「您」「请问」「建议您」。
- 不说这些词：加油、相信你、你可以的、我理解你、辛苦了、你已经很棒、慢慢来就好、尽力就好。
- 不用「作为」「我注意到」「根据我的」「总结一下」「总而言之」。
- 不喊口号，不堆感叹号，不用 emoji。
- 短句。说人话。

{context}

他的收藏是一条抖音视频。下面给出标题和 AI 摘要，你要读完再说看法。"""

USER_TMPL = """标题：{title}
作者：{author}
分类：{cat}{tags}

摘要：
{summary}

只输出一个 JSON 对象，两个字段：
{{"point": "...", "take": "..."}}

point：这条视频到底在说什么。一句话，不超过 30 字，用大白话，不许复述标题、不许写「讲了关于」。
take：你自己的看法。不超过 45 字。必须有明确立场——认同就说认同哪一点，不认同就说不认同哪里、为什么；能不认同就不必客气，但别刻薄到没有理由。如果能联系到他在做的事（{context_short}），就联系，联系不上就别硬扯。
两条都不要写「这条视频」「作者」这种第三人称旁白，你在跟他说话。"""


# ---------- 人设与缓存 ----------

def load_key(cfg_key=""):
    """拿 key：先看传入的配置值，再退回 deepseek_key.txt。

    两处都为空就返回 ""，调用方走降级（只念摘要要点，不发表看法）。
    """
    k = str(cfg_key or "").strip()
    if k:
        return k
    try:
        with open(KEY_PATH, encoding="utf-8") as f:
            return "".join(f.read().split())
    except OSError:
        return ""


_persona_lock = threading.Lock()
_persona_cache = {"mtime": None, "text": ""}


def persona_text():
    """人设段落，按 mtime 热加载：改 persona.json 不用重启。"""
    try:
        mtime = os.stat(PERSONA_PATH).st_mtime
    except OSError:
        return SYSTEM_PROMPT.format(context=CONTEXT)
    with _persona_lock:
        if _persona_cache["mtime"] == mtime and _persona_cache["text"]:
            return _persona_cache["text"]
        text = SYSTEM_PROMPT.format(context=CONTEXT)
        _persona_cache.update(mtime=mtime, text=text)
        return text


def load_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("v") == PROMPT_VERSION:
            items = data.setdefault("items", {})
            # 缓存里可能是加标点清洗之前存的，读出来顺手过一遍：
            # 半角逗号在气泡里难看，TTS 也不把它当停顿。
            for row in items.values():
                if isinstance(row, dict):
                    if row.get("point"):
                        row["point"] = _clean(row["point"], 60)
                    if row.get("take"):
                        row["take"] = _clean(row["take"], 120)
            return items
    except (OSError, ValueError):
        pass
    return {}


def save_cache(items):
    tmp = CACHE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"v": PROMPT_VERSION, "items": items}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CACHE_PATH)
    except OSError as exc:
        print("[take] 缓存写不进去:", exc)


# ---------- 单条 ----------

def _clean(text, limit):
    s = " ".join(str(text or "").split())
    s = s.replace("「", "").replace("」", "").strip()
    # 模型爱用半角逗号，TTS 不认它、气泡里也难看：中文旁边的一律换全角
    for half, full in ((",", "，"), (";", "；"), (":", "："), ("!", "！"), ("?", "？")):
        s = re.sub(r"(?<=[\u4e00-\u9fa5])" + re.escape(half), full, s)
    s = re.sub(r"[，、；：,]+$", "", s)
    return s[:limit] if len(s) > limit else s


def _clean_title(text, limit=24):
    """标题有时只剩半句（「原来是二姐：」），把尾巴的冒号顿号清掉再上。

    限 24 字：这是要念出来的，30 字以上的标题光标题就听半分钟。
    """
    s = " ".join(str(text or "").split())
    s = s.rsplit(" · ", 1)[0] if " · " in s else s
    s = re.sub(r"[，、；：:,]+$", "", s).strip()
    return s[:limit] if len(s) > limit else s


def _summary_of(item):
    s = str(item.get("summary_md") or "").strip()
    if not s:
        s = " ".join(str(item.get("title") or "").split())
    return s[:MAX_SUMMARY]


def _title_of(item):
    return _clean_title(item.get("title"))


def _parse_reply(raw):
    """模型有时会包一层 ```json 或者多说两句，尽量把两个字段捞出来。"""
    s = str(raw or "").strip()
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            point = _clean(obj.get("point"), 40)
            take = _clean(obj.get("take"), 70)
            if point and take:
                return {"point": point, "take": take}
        except ValueError:
            pass
    # 兜底：整段当看法，要点留空（播报时只念看法）
    return {"point": "", "take": _clean(s, 70)}


def fallback_point(item):
    """没有模型时，从摘要里抽一句人话当要点。

    得到大脑的摘要结构很规整：先「### 小标题」，再一两句正文，再「- **核心判断**：…」。
    优先取带「核心判断/结论/观点」的那行，其次取第一段正文。
    """
    s = str(item.get("summary_md") or "")
    if not s:
        return _clean(_title_of(item), 40)
    for line in s.splitlines():
        line = line.strip().lstrip("-*").strip()
        if not line:
            continue
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        if re.match(r"(核心判断|核心观点|结论|作者认为|作者主张|一句话)", line):
            line = re.sub(r"^(核心判断|核心观点|结论|作者认为|作者主张|一句话)[：:]\s*", "", line)
            return _clean(line, 40)
    for line in s.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(">"):
            continue
        line = re.sub(r"[*_`]", "", line)
        if len(line) >= 8:
            return _clean(line, 40)
    return _clean(_title_of(item), 40)


def _context_short():
    """「他手头在忙的」——固定行业底子 + 记忆库里的活跃话题。

    光写「命理、店里、账本」太笼统，模型只能说出放之四海皆准的话；
    补上「最近在忙奇门命盘」，它才可能说出「你那个排盘还没弄完」这种贴身的。
    """
    base_items = ["命理咨询", "店里记账", "信用卡账本", "写公众号"]
    try:
        import say_ai
        mem = say_ai.memory_context(limit=5)
        mem = mem.removeprefix("他最近在忙：").rstrip("。")
        # 记忆库也会有「信用卡账本」，跟上面重复的去掉，不然念两遍
        extra = [t for t in mem.split("、") if t and t not in base_items]
        if extra:
            base_items += extra[:3]
    except Exception:      # noqa: BLE001  记忆库缺席不该影响点评
        pass
    return "、".join(base_items)


def take_one(item, key, timeout=TIMEOUT, retry=1):
    """一条收藏 → {"point","take","src"}。src 标明是模型说的还是降级来的。"""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": persona_text()},
            {"role": "user", "content": USER_TMPL.format(
                title=_title_of(item) or "（无标题）",
                author=item.get("author") or "未知",
                cat=item.get("category") or "未分类",
                tags=("　标签：" + "、".join(map(str, item.get("tags") or []))) if item.get("tags") else "",
                summary=_summary_of(item),
                context_short=_context_short(),
            )},
        ],
        "temperature": 0.9,
        "max_tokens": 220,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}

    last_err = None
    for attempt in range(retry + 1):
        try:
            r = requests.post(API_URL, headers=headers, json=payload, timeout=timeout)
            if r.status_code == 401:
                raise RuntimeError("DeepSeek key 无效或已过期")
            if r.status_code == 402:
                raise RuntimeError("DeepSeek 余额不足")
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"]
            out = _parse_reply(raw)
            out["src"] = "llm"
            return out
        except Exception as exc:      # noqa: BLE001
            last_err = exc
            if attempt < retry:
                time.sleep(1.2)
    print("[take] 单条失败，降级:", type(last_err).__name__, last_err)
    return {"point": fallback_point(item), "take": "", "src": "fallback"}


# ---------- 批量 ----------

def take_id(item):
    """缓存键：优先 aweme_id；没有就用标题+摘要的哈希。"""
    aid = str(item.get("aweme_id") or "").strip()
    if aid:
        return aid
    return "h" + hashlib.md5((_title_of(item) + _summary_of(item)[:200]).encode("utf-8")).hexdigest()[:16]


def get_takes(items, key, workers=4, use_cache=True, progress=None):
    """批量点评，返回 {id: {...}}。顺序无关，调用方按 items 顺序取。

    workers 默认 4：DeepSeek 单条 2~3 秒，5 条串行要十几秒，
    但也不能开太大——读收藏这种事没必要把并发打满。
    """
    cache = load_cache() if use_cache else {}
    out, todo = {}, []
    for it in items:
        cid = take_id(it)
        hit = cache.get(cid)
        if hit and hit.get("take"):
            out[cid] = dict(hit, src="cache")
        else:
            todo.append(it)

    if not key:
        for it in todo:
            out[take_id(it)] = {"point": fallback_point(it), "take": "", "src": "fallback"}
        return out

    done = [0]

    def work(it):
        res = take_one(it, key)
        res["title"] = _title_of(it)
        res["at"] = time.strftime("%Y-%m-%d %H:%M")
        if progress:
            progress(res)
        return take_id(it), res

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo)))) as pool:
            for cid, res in pool.map(work, todo):
                out[cid] = res
                if res.get("take"):
                    cache[cid] = res
                done[0] += 1
        save_cache(cache)
    return out


def forget(aweme_id):
    """删掉一条缓存（想让它重新点评时用）。"""
    cache = load_cache()
    if cache.pop(str(aweme_id), None) is not None:
        save_cache(cache)
        return True
    return False
