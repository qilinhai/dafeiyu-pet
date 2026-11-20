"""抖音收藏归档 · 给大肥鱼桌宠用的数据/汇报/新收藏检测模块。

不依赖看板服务：直接读同机的 data/records.jsonl，服务没开也能用。
配置与状态跟 pet_memory.json 一样放在程序根目录，方便备份。
"""
import json
import os
import time
from collections import Counter
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))          # C:\DaFeiYu
CONFIG_PATH = os.path.join(BASE, "douyin_config.json")
STATE_PATH = os.path.join(BASE, "douyin_seen.json")

DEFAULT_ARCHIVE_DIR = r"C:/Users/ASUS/WorkBuddy/2026-11-01-12-41-47"

# 所有可调项都在这里，改 douyin_config.json 即生效（右键「设置」可打开）
DEFAULT_CONFIG = {
    "enabled": True,                    # 总开关
    "archive_dir": DEFAULT_ARCHIVE_DIR,  # 归档项目目录
    "poll_minutes": 5,                  # 多少分钟查一次新收藏
    "announce_new": True,               # 有新收藏就冒泡播报
    "max_announces_per_day": 6,         # 一天最多播报几次（防刷屏）
    "daily_report": True,               # 每天固定时间汇报一次
    "daily_report_at": "12:00",         # 汇报时间 HH:MM（本地时间）
    "speak": True,                      # 自动播报是否念出声；右键「念出声」可随时关
    "announce_seconds": 9,              # 新收藏气泡停留秒数
    "report_seconds": 16,               # 汇报气泡停留秒数
}

DEFAULT_STATE = {
    "seen_ids": [],          # 已经「见过」的作品 id，用于判定新增
    "last_check": 0,         # 上次检查时间（epoch 秒）
    "announce_date": "",     # 播报计数所属日期
    "announce_count": 0,     # 当天已播报次数
    "last_report_date": "",   # 上次每日汇报的日期
}


# ---------- 读写 JSON（原子写，避免半截文件） ----------

def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            return obj
    except (OSError, ValueError):
        pass
    return dict(default or {})


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------- 配置 ----------

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(_read_json(CONFIG_PATH, {}))
    return cfg


def save_config(patch):
    cfg = load_config()
    cfg.update(patch or {})
    _write_json(CONFIG_PATH, cfg)
    return cfg


# ---------- 状态 ----------

def load_state():
    st = dict(DEFAULT_STATE)
    st.update(_read_json(STATE_PATH, {}))
    if not isinstance(st.get("seen_ids"), list):
        st["seen_ids"] = []
    return st


def save_state(state):
    _write_json(STATE_PATH, state)


# ---------- 读归档 ----------

def records_path(cfg=None):
    cfg = cfg or load_config()
    base = str(cfg.get("archive_dir") or DEFAULT_ARCHIVE_DIR)
    return os.path.join(base, "data", "records.jsonl")


def _ts(at):
    """把 at（ISO 字符串）解析成 epoch 秒；解析不了返回 0。"""
    if not at:
        return 0.0
    s = str(at).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _local_date(at):
    ts = _ts(at)
    if not ts:
        return None
    return datetime.fromtimestamp(ts).date()


def load_records(cfg=None):
    """读 records.jsonl，跳过坏行，按归档时间倒序。"""
    path = records_path(cfg)
    items = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    it = json.loads(line)
                except ValueError:
                    continue
                if isinstance(it, dict) and it.get("aweme_id"):
                    items.append(it)
    except OSError:
        return []
    items.sort(key=lambda x: _ts(x.get("at")), reverse=True)
    return items


def today(items=None):
    items = items if items is not None else load_records()
    d = date.today()
    return [it for it in items if _local_date(it.get("at")) == d]


def since(days=7, items=None):
    items = items if items is not None else load_records()
    cutoff = date.today() - timedelta(days=max(1, int(days)) - 1)
    out = []
    for it in items:
        ld = _local_date(it.get("at"))
        if ld and ld >= cutoff:
            out.append(it)
    return out


def recent(n=5, items=None):
    items = items if items is not None else load_records()
    return items[: max(0, int(n))]


# ---------- 展示与汇报 ----------

def short_title(it, limit=22):
    """标题常形如「标题 · 作者」，汇报时去掉尾部作者，过长截断。"""
    t = " ".join(str(it.get("title") or "").split())
    if not t:
        return "（无标题）"
    if " · " in t:
        t = t.rsplit(" · ", 1)[0]
    return t if len(t) <= limit else t[:limit] + "…"


def item_line(it, limit=26):
    cat = it.get("category") or "未分类"
    author = " ".join(str(it.get("author") or "").split())
    line = f"[{cat}] {short_title(it, limit)}"
    return line + (f" · {author}" if author else "")


def report(period="today", items=None):
    items = items if items is not None else load_records()
    if period == "week":
        scope = since(7, items)
    elif period == "all":
        scope = list(items)
    else:
        period = "today"
        scope = today(items)
    cats = Counter((it.get("category") or "未分类") for it in scope)
    tags = Counter(t for it in scope for t in (it.get("tags") or []))
    return {
        "period": period,
        "count": len(scope),
        "total": len(items),
        "categories": cats.most_common(),
        "tags": tags.most_common(),
        "items": scope,
    }


def summary_text(period="today", items=None):
    r = report(period, items)
    if r["count"] == 0:
        return "今天还没收藏新东西。" if r["period"] == "today" else "这段时间没收藏新东西。"
    label = {"today": "今天", "week": "这七天", "all": "总共"}[r["period"]]
    cats = "、".join(f"{c}{n}条" for c, n in r["categories"][:3])
    line = f"{label}收藏了{r['count']}条"
    if cats:
        line += f"，主要是{cats}"
    line += "。"
    if r["items"]:
        line += "最近一条：" + short_title(r["items"][0], 24) + "。"
    return line


def new_announce_text(new_items):
    n = len(new_items)
    if n == 0:
        return ""
    if n == 1:
        return f"刚收藏了一条：{short_title(new_items[0], 20)}。"
    return f"刚收藏了{n}条，最近一条：{short_title(new_items[0], 20)}。"


# ---------- 新收藏检测 / 播报限流 ----------

def check_new(state, items=None):
    """返回 (新增列表, state)。首次运行只做种子，不把存量当新收藏播报。"""
    items = items if items is not None else load_records()
    seen = set(str(x) for x in (state.get("seen_ids") or []))
    if not seen and not state.get("last_check"):
        state["seen_ids"] = [str(it.get("aweme_id")) for it in items]
        state["last_check"] = int(time.time())
        return [], state
    new = [it for it in items if str(it.get("aweme_id")) not in seen]
    return new, state


def mark_seen(state, new_items):
    seen = set(str(x) for x in (state.get("seen_ids") or []))
    for it in new_items:
        seen.add(str(it.get("aweme_id")))
    state["seen_ids"] = sorted(seen)
    state["last_check"] = int(time.time())
    return state


def _today_str():
    return date.today().isoformat()


def can_announce(state, cfg):
    try:
        cap = int(cfg.get("max_announces_per_day", 6))
    except (TypeError, ValueError):
        cap = 6
    if cap <= 0:
        return True  # 0 = 不限
    if state.get("announce_date") != _today_str():
        return True
    return int(state.get("announce_count") or 0) < cap


def bump_announce(state):
    d = _today_str()
    if state.get("announce_date") != d:
        state["announce_date"] = d
        state["announce_count"] = 0
    state["announce_count"] = int(state.get("announce_count") or 0) + 1
    return state


# ---------- 每日汇报时间 ----------

def _parse_hhmm(s, default=(12, 0)):
    try:
        h, m = str(s).split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except (ValueError, AttributeError):
        pass
    return default


def daily_report_due(state, cfg, now=None):
    if not cfg.get("daily_report", True):
        return False
    now = now or datetime.now()
    if state.get("last_report_date") == now.date().isoformat():
        return False
    hh, mm = _parse_hhmm(cfg.get("daily_report_at"), (12, 0))
    return (now.hour, now.minute) >= (hh, mm)
