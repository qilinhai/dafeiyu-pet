"""大肥鱼的事件回忆与关系成长。与话题记忆共用一个事务，不依赖 Qt。"""
import re
import time

import pet_memory as memory


QUIET_STOP = "收工时只冒泡"
FIELDS = {"items": "topic", "preferences": "text", "pins": "text"}
STAGES = ((0, "初识"), (3, "熟面孔"), (8, "老搭档"))


def edit_entry(path, group, old, new):
    """用原文定位；不同窗口改过同一条时提示刷新。None 表示删除。"""
    field = FIELDS[group]
    if new is not None:
        new = new.strip()
        if not new or len(new) > 80:
            raise ValueError("请填写 1 到 80 个字")
    with memory.edit_store(path) as d:
        rows = d.setdefault(group, [])
        entry = next((row for row in rows if row[field] == old), None)
        if old is not None and entry is None:
            raise ValueError("这条记忆已变动，请重新打开")
        if group == "items" and old and old != new:
            for task in d.get("growth", {}).get("tasks", {}).values():
                if task.get("topic") == old:
                    task["topic"] = new or ""
        if new is None:
            rows.remove(entry)
        elif any(row is not entry and row[field] == new for row in rows):
            raise ValueError("已经记过这条了")
        elif entry is not None:
            entry[field] = new
            entry["source"] = "manual"
        elif group in ("pins", "preferences"):
            rows.append({field: new, "source": "manual", "ts": int(time.time())})
        else:
            raise ValueError("新记忆请使用手记")


def quiet_stop(path):
    return any(row["text"] == QUIET_STOP for row in memory.read_store(path)["preferences"])


def set_quiet(path, enabled):
    with memory.edit_store(path) as d:
        if enabled and not any(p["text"] == QUIET_STOP for p in d["preferences"]):
            d["preferences"].append({"text": QUIET_STOP, "source": "manual", "ts": int(time.time())})
        elif not enabled:
            d["preferences"] = [p for p in d["preferences"] if p["text"] != QUIET_STOP]


def _tokens(text):
    tokens = set()
    for word in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", text.casefold()):
        if word.isascii():
            tokens.add(word)
        else:
            tokens.update(word[i:i+2] for i in range(len(word)-1))
    return tokens


def recall(d, query, *, long_topic=False, now=None):
    """单条相关回忆。任务庆祝只接受跨日巩固话题。"""
    keys = _tokens(query)
    best, score = "", 0
    candidates = [it["topic"] for it in d["items"]
                  if memory.phase_of(it, now) != "dormant"
                  and (not long_topic or memory.is_consolidated(it))]
    if not long_topic:
        candidates = [row["text"] for row in d.get("pins", [])] + candidates
    for name in candidates:
        terms = _tokens(name)
        hits = len(keys & terms)
        if terms and len(name) >= 2 and name.casefold() in query.casefold():
            value = 2 + len(name) / 100
        elif hits >= 3 and any(name[i:i+4].casefold() in query.casefold()
                               for i in range(len(name) - 3)):
            value = 1 + hits / max(len(terms), len(keys))
        elif hits >= 2 and hits >= min(len(terms), len(keys)) * 0.6:
            value = hits / max(len(terms), len(keys))
        else:
            continue
        if value > score:
            best, score = name, value
    return best


def stage(g):
    points = g.get("days", 0) + len(g.get("tasks", {}))
    return next(name for limit, name in reversed(STAGES) if points >= limit)


def interact(path, kind, now=None):
    """喂食和点击共享当天的一点进度。返回当天首次互动的阶段和台词。"""
    today = memory._day(now or time.time())
    with memory.edit_store(path) as d:
        g = d.setdefault("growth", {})
        if today <= g.get("last_day", ""):
            return None
        g["last_day"] = today
        g["days"] = g.get("days", 0) + 1
        name = stage(g)
        if name != "初识":
            g.setdefault("milestones", {}).setdefault(name, f"{today} · {kind}")
        return name


def complete_tasks(path, items):
    """消费已落盘的完成事件。重复同步与重新勾选都沿用原待办 ID。"""
    with memory.edit_store(path) as d:
        celebrations = []
        for it in items:
            if it.get("status") != "done" or not it.get("growth_completion"):
                continue
            g = d.setdefault("growth", {})
            tasks = g.setdefault("tasks", {})
            if it["id"] in tasks:
                continue
            topic = recall(d, it["title"], long_topic=True)
            tasks[it["id"]] = {"title": it["title"], "at": it["growth_completion"], "topic": topic}
            name = stage(g)
            if name != "初识":
                g.setdefault("milestones", {}).setdefault(name, it["growth_completion"][:10] + " · " + it["title"])
            if topic:
                celebrations.append(topic)
        return celebrations


def growth_report(d):
    g = d.get("growth", {})
    tasks = g.get("tasks", {})
    total = g.get("days", 0) + len(tasks)
    next_stage = next(((n, title) for n, title in STAGES if n > total), None)
    lines = [f"{stage(g)}　·　{total} 点", f"互动 {g.get('days', 0)} 天，共同完成 {len(tasks)} 件待办"]
    if next_stage:
        lines.append(f"再积累 {next_stage[0]-total} 点，成为「{next_stage[1]}」")
    for title, cause in g.get("milestones", {}).items():
        greeting = "挥鳍迎接" if title == "熟面孔" else "小跑迎接"
        lines.append(f"学会{greeting} · {cause}")
    for task in list(tasks.values())[-5:][::-1]:
        line = f"{task['at'][:10]}　{task['title']}"
        if task["topic"]:
            line += f"（一起忙过「{task['topic']}」）"
        lines.append(line)
    return "\n".join(lines)
