# -*- coding: utf-8 -*-
"""大肥鱼 · 语料成长层。

它管三件事，都围绕「同一类通知别念重样」这一件事展开：

  1. 挑（pick）—— 从池子里挑一句最近没说过的话
  2. 长（replenish）—— 池子快见底时补货，补进来的句子落盘，下次还在
  3. 记（learn）—— 哪句被念了、有没有起效，攒着给后面的权重用

设计约束：
- 生成器一律**注入**（gen 参数），测试塞假的，生产塞真的。绝不在测试里联网。
- 落盘文件坏了、丢了、手改成乱七八糟 → 一律当空池，绝不抛异常把播报搞哑。
- 给外部只留四个口：Lexicon / pick / learn / replenish。
"""
from __future__ import annotations

import json
import os
import random
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "voice_lexicon.json")
# 补货信箱：need = 大肥鱼下的订单，inbox = WorkBuddy 写好待取的句子
NEED_PATH = os.path.join(HERE, "voice_lexicon_need.json")
INBOX_PATH = os.path.join(HERE, "voice_lexicon_inbox.json")

RECENT = 12         # 整池说过一遍后，只躲最近这么多句
HIST_MAX = 200      # 每个 kind 记多少条历史，防无限涨
LOW_WATER = 3       # 新鲜句少于这个数就补货
BAD_LIMIT = 3       # 被嫌弃这么多次（且没被夸过）就沉底
REPLENISH_GAP = 1800  # 同一类补货最短间隔（秒），别一刷屏就狂调模型
BATCH = 12          # 一次补几条
MAX_LEN = 20        # 单句上限，太长念出来喘不上气

# 补货句的体检：这三样不过，宁可少几句也不能进池
_BAD_CHARS = re.compile(r"[A-Za-z0-9]{6,}|https?://|[\r\n]")


def _load_forbid():
    """persona.json 里写死不许说的词（敬语、鸡汤、AI 味）。

    自己读，不去 import notify_voice——那边会反过来 import 本模块，
    循环引用会在播报主链路上炸，代价太大。
    """
    try:
        with open(os.path.join(HERE, "persona.json"), encoding="utf-8") as f:
            sp = (json.load(f) or {}).get("speech") or {}
    except Exception:
        return []
    out = []
    for k in ("no_honorific", "no_cheer", "no_ai_tell"):
        out += list(sp.get(k) or [])
    return [w for w in out if w]


FORBID = _load_forbid()


def _clean(text):
    """补货句洗一遍：去空白、去引号、掐长度、过禁词。不合格返回空串。"""
    t = (text or "").strip().strip("「」“”\"'· ")
    t = re.sub(r"\s+", "", t)
    if not t or len(t) > MAX_LEN:
        return ""
    if _BAD_CHARS.search(t):
        return ""
    for w in FORBID:
        if w in t:
            return ""
    return t


# ---------------- 补货：让模型写新句子 ----------------
# 人设写在这里而不是散在代码里：一句话决定生成的梗像不像大肥鱼。
SYS = ("你在给桌面宠物「大肥鱼」写口播短句。它贱兮兮、务实、有点丧、爱玩梗，"
       "说话像熟人嘴上不饶人但心里有数。只输出句子本身，不要解释、不要编号、"
       "不要敬语、不要鸡汤、不要说教、不要提自己是AI。每句不超过12个字。")

KIND_HINT = {
    "money": "银行到账/扣款/账单类通知的吐槽尾巴",
    "work": "订单、客户、店里生意类通知的尾巴",
    "chat": "微信QQ有人找你时的尾巴",
    "tech": "代码、部署、任务跑完的尾巴",
    "notice": "无关紧要的普通通知，随口一提",
    "urgent": "地震停电台风这类紧急提醒，只能说半句，不许贫嘴",
    "flood": "同一个App刷屏时的吐槽",
    "phone": "手机断连了，催人去重连",
    "phone_back": "手机重新连上了，一句话带过",
}

_LEAD = re.compile(r"^\s*(?:[-*·—]|\d+\s*[.、)）]|[（(]\s*\d+\s*[）)])\s*")


def _ds_key():
    """DeepSeek key，跟桌宠共用一份，不另存。"""
    for p in (os.path.join(HERE, "dafeiyu-pet-main", "config.json"),
              os.path.join(HERE, "config.json")):
        try:
            with open(p, encoding="utf-8") as f:
                k = ((json.load(f) or {}).get("ds_api_key") or "").strip()
            if k:
                return k
        except Exception:
            continue
    return ""


def _chat(prompt, timeout=12):
    """问模型一次。没 key / 没网 / 报错一律返回空串，绝不抛。"""
    key = _ds_key()
    if not key:
        return ""
    try:
        import requests
        r = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"model": "deepseek-chat",
                  "messages": [{"role": "system", "content": SYS},
                               {"role": "user", "content": prompt}],
                  "max_tokens": 500,
                  "temperature": 1.15},
            timeout=timeout)
        if r.status_code != 200:
            return ""
        ch = (r.json() or {}).get("choices") or [{}]
        return (ch[0].get("message") or {}).get("content") or ""
    except Exception:
        return ""


def _prompt(kind, want, avoid):
    hint = KIND_HINT.get(kind, "普通通知的尾巴")
    banned = "、".join(list(avoid)[:40]) if avoid else "（无）"
    return (f"场景：{hint}。\n"
            f"写 {want} 句，一行一句，句子之间不要相似，不要用同一个开头。\n"
            f"下面这些已经用过了，别重复：{banned}\n"
            f"可以玩梗：网络热梗、影视武侠、打工人自嘲、谐音都行，比例随意，"
            f"但别超过一半，剩下的要像人随口一说。")


def default_gen(kind, want, avoid):
    """默认补货生成器：不依赖任何 API key 也能长。

    三段接力，谁先给货先吃谁：
      1. 信箱 —— WorkBuddy 的模型（就是用户日常在用的那个）巡检时写好的句子
      2. 直连 —— 配上 DeepSeek key 才走，本地现编，最快
      3. 订单 —— 两边都没货就下单，写进 need 文件，等 WorkBuddy 下一轮巡检

    没 key 不再是死链：以前这里 `if not key: return ""`，等于整条成长链
    吊在一把要用户掏钱买的 key 上。现在最差也只是「晚一点长」。
    """
    got = _drain_inbox(kind, avoid, want)
    if len(got) >= want:
        return got[:want]
    if _ds_key():
        rest = want - len(got)
        got += _ask_model(kind, rest, set(avoid) | set(got))
    if len(got) < want and not got:
        # 只有「一句都没拿到」才下单：直连成功但给少了，说明模型活着，
        # 下一轮还会再要，不必麻烦 WorkBuddy。
        _queue_need(kind, want - len(got))
    return got


def _ask_model(kind, want, avoid):
    """直连模型那一支（有 key 才走）。"""
    raw = _chat(_prompt(kind, want, avoid))
    if not raw:
        return []
    out = []
    for ln in raw.splitlines():
        t = _clean(_LEAD.sub("", ln))
        if t and t not in avoid and t not in out:
            out.append(t)
        if len(out) >= want:
            break
    return out


# ---------------- 补货信箱：WorkBuddy 当生成器 ----------------
# 用户不想为这件事单独买 key —— 那就让已经在用的 WorkBuddy 模型来写。
# 它是定时巡检的，拿不到同步返回值，所以走「下单 → 写货 → 收货」三段。
def _read_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, type(fallback)) else fallback
    except Exception:
        return fallback


def _write_json(path, obj):
    """原子写。WorkBuddy 和大肥鱼是两个进程，写到一半被读会读出半截。"""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def _drain_inbox(kind, avoid, want):
    """把信箱里这个 kind 的句子取走。取走就删，免得同一句反复倒回池子。"""
    data = _read_json(INBOX_PATH, {})
    if not data:
        return []
    raw = data.get(kind)
    if not raw:
        return []
    out = []
    for x in raw if isinstance(raw, list) else []:
        t = _clean(x)
        if t and t not in avoid and t not in out:
            out.append(t)
    data.pop(kind, None)
    _write_json(INBOX_PATH, data)
    return out[:want] if want else out


def _queue_need(kind, want):
    """下单。同一类只挂一条：池子见底时每次 pick 都会调进来，不合并会堆成垃圾。"""
    data = _read_json(NEED_PATH, {})
    ent = data.get(kind) if isinstance(data.get(kind), dict) else {}
    data[kind] = {"want": int(want), "ts": int(time.time()),
                  "n": int(ent.get("n", 0)) + 1}
    return _write_json(NEED_PATH, data)


def inbox_waiting():
    """信箱里还有多少句没被取走（面板显示用）。"""
    data = _read_json(INBOX_PATH, {})
    return sum(len(v) for v in data.values() if isinstance(v, list))


def need_summary():
    """还没被满足的订单，{kind: 第几轮}（面板显示用）。"""
    data = _read_json(NEED_PATH, {})
    return {k: int((v or {}).get("n", 0))
            for k, v in data.items() if isinstance(v, dict)}


_DEFAULT = None


def default_lexicon():
    """全进程共用的那一本账。notify_voice 惰性拿它。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Lexicon(path=DEFAULT_PATH, gen=default_gen)
    return _DEFAULT


class Lexicon:
    """一个 kind 一本账。path 可注入（测试不碰真文件）。"""

    def __init__(self, path=None, gen=None):
        self.path = path or DEFAULT_PATH
        self.gen = gen
        self.data = self._load()
        self._hist = {}          # kind -> 最近说过的句子
        self._last_fill = {}     # kind -> 上次补货的时间（节流用）
        self._stamp = self._file_stamp()

    # ---------------- 落盘 ----------------
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict) or not isinstance(d.get("pools"), dict):
                raise ValueError("shape")
            return d
        except Exception:
            return {"version": 1, "pools": {}, "meta": {"grown": 0}}

    def _file_stamp(self):
        """文件的 (mtime_ns, size)。两个进程靠它互相发现对方改过。"""
        try:
            st = os.stat(self.path)
            return (int(st.st_mtime_ns), int(st.st_size))
        except Exception:
            return None

    def _reload_if_changed(self):
        """别的进程（面板）改过账本，我这边得跟着重读。

        监听进程是常驻的，面板是另一个进程——不重读的话，面板里点「嫌烦」
        只写进了文件，监听内存里还是旧账，学了等于没学。
        """
        cur = self._file_stamp()
        if cur is not None and cur != self._stamp:
            self._stamp = cur
            self.data = self._load()

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            self._stamp = self._file_stamp()
        except Exception:
            pass

    # ---------------- 池子 ----------------
    def _builtin(self, kind):
        """内置语料：notify_voice.LAYERS 里的 view + act。"""
        try:
            import notify_voice as V
            lay = (V.LAYERS or {}).get(kind) or {}
            return list(lay.get("view") or []) + list(lay.get("act") or [])
        except Exception:
            return []

    def pool(self, kind):
        out = self._builtin(kind)
        for e in self.data["pools"].get(kind) or []:
            t = e.get("t") if isinstance(e, dict) else e
            if t and t not in out:
                out.append(t)
        return out

    def add(self, kind, lines):
        """补货句入池：洗、去重、掐批。返回真正加进去几条。"""
        bucket = self.data["pools"].setdefault(kind, [])
        have = set(self.pool(kind))
        n = 0
        for raw in lines or []:
            t = _clean(raw)
            if not t or t in have:
                continue
            bucket.append({"t": t, "n": 0, "last": 0, "ok": 0, "bad": 0, "src": "grown"})
            have.add(t)
            n += 1
        if n:
            self.data.setdefault("meta", {})["grown"] = \
                int(self.data.get("meta", {}).get("grown", 0)) + n
            self.save()
        return n

    def replenish(self, kind, want=BATCH, now=None, force=False):
        """补货。没有生成器就什么也不做（别假装能长）。

        节流是必须的：池子见底就补 = 刷屏时一分钟能补八回，积分和限流都受不了。
        失败也记账，免得网络一出问题就每句都重试一遍。
        """
        if not self.gen:
            return 0
        now = now or time.time()
        if not force and now - float(self._last_fill.get(kind, 0)) < REPLENISH_GAP:
            return 0
        self._last_fill[kind] = now
        avoid = set(self.pool(kind))
        try:
            fresh = self.gen(kind, want, avoid)
        except Exception:
            return 0
        return self.add(kind, fresh)

    # ---------------- 挑 ----------------
    def _maybe_absorb(self):
        """信箱来新货就并进池子。

        不能等池子见底才收：WorkBuddy 一批写十几句，池子还够吃的时候
        它躺在信箱里，等于白写一次。靠文件 mtime 发现，没变化不读内容。
        """
        try:
            st = os.stat(INBOX_PATH)
        except Exception:
            return 0
        cur = (int(st.st_mtime_ns), int(st.st_size))
        if cur == getattr(self, "_inbox_stamp", None):
            return 0
        self._inbox_stamp = cur
        data = _read_json(INBOX_PATH, {})
        if not data:
            return 0
        n = 0
        for kind, lines in list(data.items()):
            if isinstance(lines, list):
                n += self.add(kind, lines)
        _write_json(INBOX_PATH, {})
        return n

    def absorb_inbox(self):
        """立刻收一次信箱（面板按钮用，不等 mtime 判断）。"""
        self._inbox_stamp = None
        return self._maybe_absorb()

    def pick(self, kind, now=None):
        now = now or time.time()
        self._reload_if_changed()
        self._maybe_absorb()
        hist = self._hist.setdefault(kind, [])
        pool = self._usable(kind)

        # 先躲开「说过的所有句子」，不是只躲最近几句——只躲最近几句时，
        # 池子一长，早先的句子滑出窗口就会回头复读（实测 20 条只出 17 种）。
        fresh = [s for s in pool if s not in set(hist)]
        # 快见底了：先补货再挑。补完还不够就认了，宁可复读也别哑。
        if len(fresh) < LOW_WATER and self.replenish(kind, now=now):
            pool = self._usable(kind)
            fresh = [s for s in pool if s not in set(hist)]
        # 整池都说过一遍了，才退回「只躲最近 RECENT 句」
        if not fresh:
            fresh = [s for s in pool if s not in set(hist[-RECENT:])]
        if not fresh:
            fresh = pool or [""]

        s = random.choice(fresh)
        hist.append(s)
        del hist[:-HIST_MAX]
        self._touch(kind, s, now)
        return s

    def _entry(self, kind, text, create=False):
        """找这句话的账目条目。内置池的句子一开始没有条目，create 时补一条。"""
        for e in self.data["pools"].get(kind) or []:
            if isinstance(e, dict) and e.get("t") == text:
                return e
        if create:
            e = {"t": text, "n": 0, "last": 0, "ok": 0, "bad": 0, "src": "builtin"}
            self.data["pools"].setdefault(kind, []).append(e)
            return e
        return None

    def _touch(self, kind, text, now):
        """记一笔：用过几次、最后一次什么时候。"""
        e = self._entry(kind, text, create=True)
        if e is not None:
            e["n"] = int(e.get("n", 0)) + 1
            e["last"] = int(now)

    def learn(self, kind, text, signal):
        """反馈记一笔。signal: 'ok' / 'bad'。"""
        e = self._entry(kind, text, create=True)
        if e is None:
            return
        k = "ok" if signal == "ok" else "bad"
        e[k] = int(e.get(k, 0)) + 1
        self.save()

    def _usable(self, kind):
        """能说的句子：嫌弃够多又没被夸过的，先靠边站。

        全被嫌弃了还得说——宁可说句你不爱听的，也别在通知来的时候闭嘴。
        """
        pool = self.pool(kind)
        keep = [s for s in pool if not self._is_stale(kind, s)]
        return keep or pool

    def _is_stale(self, kind, text):
        e = self._entry(kind, text)
        if not e:
            return False
        return int(e.get("bad", 0)) >= BAD_LIMIT and int(e.get("ok", 0)) == 0
