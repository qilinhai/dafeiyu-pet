"""推测感知：从记忆里推一句话——关于你，或者关于你在忙的事。

和「记忆」的区别先说清楚：记忆只存发生过的事（谁说了什么、哪天真干了）；
这里存的是**推测**，可能错，所以要带依据、要能被纠正、说错了以后不再复述。

两层分开存（pet_memory.impressions），不许混进 items——混了就成了编造：
「我记得你说过」和「我猜你最近在赶」是两种话，说反了就是撒谎。

节流按「今天已经说了几条」来算，不另开状态文件：
impressions 里每条都记 said_ts，够数就不说了。
"""
import json
import os
import time

import say_ai

KIND_YOU = "about_you"
KIND_WORK = "about_work"

DEFAULT = {
    "enabled": True,
    "per_day": 3,          # 一天最多点几次
    "gap_minutes": 90,     # 两次之间至少隔这么久
    "max_chars": 26,
}


def _cfg():
    try:
        talk = say_ai.cfg()
    except Exception:                     # noqa: BLE001
        talk = {}
    out = dict(DEFAULT)
    out.update(talk.get("hunch") or {})
    return out


def _today_said(now):
    import pet_memory as pm
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    n = 0
    last = 0
    for it in pm.load_mem().get("impressions", []):
        ts = int(it.get("said_ts") or 0)
        if not ts:
            continue
        if time.strftime("%Y-%m-%d", time.localtime(ts)) == day:
            n += 1
        last = max(last, ts)
    return n, last


def due(now=None):
    """现在该不该说一句推测。返回 (bool, 原因)。"""
    now = int(now or time.time())
    c = _cfg()
    if not c.get("enabled", True):
        return False, "推测关着"
    n, last = _today_said(now)
    if n >= int(c.get("per_day") or 3):
        return False, f"今天已经点了 {n} 次"
    gap = int(c.get("gap_minutes") or 90) * 60
    if last and now - last < gap:
        return False, f"距上次还不到 {gap // 60} 分钟"
    return True, ""


def _facts(limit_work=6, limit_event=4):
    """给模型看的材料：最近在忙什么、具体干了啥、谁找过他。

    只有话题名的话，模型只能推「你这几天又在弄那个吧」——等于没说。
    挂上最近一次的原话和结论，它才推得出「术语砍完了，引导页还没动」。
    """
    import pet_memory as pm
    mem = pm.load_mem()
    work = [i for i in mem["items"] if i.get("layer", "work") == "work"][:limit_work]
    event = [i for i in mem["items"] if i.get("layer") == "event"][:limit_event]
    lines = []
    for it in work:
        head = (f"- 在忙：{it['topic']}（近 {it.get('days', 1)} 天提过 {it.get('hits', 1)} 次，"
                f"状态 {it.get('mood', 'flat')}）")
        lines.append(head)
        cards = pm.events_of(it, 1)
        if cards:
            line = pm.describe_card(cards[0])
            if line:
                lines.append(f"    最近一次：{line}")
    for it in event:
        lines.append(f"- 消息：{it['topic']} → {it.get('preview', '')}")
    return lines


PROMPT = """你是「大肥鱼」，一条住在这个人电脑里的胖鱼，跟着他上工。
下面是它记得的事，请根据这些说一句**推测**。

材料：
{facts}

已经说过的（不要重复）：
{said}

规则：
- 只推一件具体的事，别泛泛而谈（「你最近挺忙」这种等于没说）
- 一句话，不超过 {limit} 字，口语，像随口点的
- 允许带「估计/八成/我看着」这类口吻，但不许说「我确定」「我查过」
- **不许编造材料里没有的事**。材料不够就返回空
- kind 只能取 about_you（关于这个人）或 about_work（关于他在忙的事）

只输出 JSON，不要解释：
{{"kind":"about_you","text":"...","basis":"依据材料的哪一条","conf":0.6}}
或
{{"skip":true}}"""


def _parse(raw, limit):
    """从模型回复里抠出 JSON。抠不出来就当没话说。"""
    s = str(raw or "").strip()
    if not s:
        return None
    if "```" in s:
        import re
        m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
        if m:
            s = m.group(1).strip()
    try:
        got = json.loads(s)
    except Exception:                     # noqa: BLE001
        return None
    if not isinstance(got, dict) or got.get("skip"):
        return None
    text = str(got.get("text") or "").strip()
    if not text or len(text) > limit + 6:
        return None
    kind = got.get("kind")
    if kind not in (KIND_YOU, KIND_WORK):
        kind = KIND_YOU
    try:
        conf = max(0.0, min(1.0, float(got.get("conf") or 0.5)))
    except (TypeError, ValueError):
        conf = 0.5
    return {"kind": kind, "text": text,
            "basis": [str(got.get("basis") or "")][:1], "conf": conf}


def sniff(now=None, force=False):
    """生成一条推测并入库。返回 dict 或 None（没话说/调不动都返回 None）。

    force=True 只给「右键 → 现在推一句」用：绕过节流，方便当场试。
    绕过的只有「今天几条 / 隔多久」，材料不够照样返回空。
    """
    import pet_memory as pm
    now = int(now or time.time())
    if not force:
        ok, why = due(now)
        if not ok:
            print(f"[hunch] 不说：{why}")
            return None
    elif not _cfg().get("enabled", True):
        print("[hunch] 手动试：总开关关着，照样推一条")
    facts = _facts()
    if not facts:
        print("[hunch] 不说：脑子里没料")
        return None
    said = "、".join(i["text"] for i in pm.impressions(limit=6)) or "（还没有）"
    c = _cfg()
    limit = int(c.get("max_chars") or 26)
    prompt = [{"role": "user", "content": PROMPT.format(
        facts="\n".join(facts), said=said, limit=limit)}]
    key = say_ai.load_key()
    if not key:
        print("[hunch] 没有 key")
        return None
    talk = say_ai.cfg()
    try:
        import requests
        r = requests.post(say_ai.API_URL, json={
            "model": str(talk.get("model") or "deepseek-chat"),
            "messages": prompt,
            "temperature": 0.9,
            "max_tokens": 120,
        }, timeout=float(talk.get("timeout") or 6),
            headers={"Authorization": "Bearer " + key,
                     "Content-Type": "application/json"})
        r.raise_for_status()
        data = r.json()
        raw = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
    except Exception as exc:              # noqa: BLE001  这一层不许把桌宠弄死
        print("[hunch] 调用失败:", type(exc).__name__, exc)
        return None

    got = _parse(raw, limit)
    if not got:
        print("[hunch] 弃用（不是一句能说出口的推测）：", str(raw)[:60])
        return None
    yuan = (int(usage.get("prompt_tokens") or 0) * say_ai.PRICE_IN
            + int(usage.get("completion_tokens") or 0) * say_ai.PRICE_OUT)
    try:
        say_ai._log_call("hunch", yuan, got["text"])
    except Exception:                     # noqa: BLE001
        pass
    pm.remember_impression(got["kind"], got["text"], got["basis"], got["conf"], now=now)
    print(f"[hunch] {got['kind']}：{got['text']}（{got['conf']}，依据 {got['basis']}）")
    return got


def mark_said(text, now=None):
    """说出口了记一笔，节流靠它。"""
    import pet_memory as pm
    return pm.mark_impression(text, said=True, now=now)


def reject(text):
    """被纠正了：以后不再复述这条。"""
    import pet_memory as pm
    return pm.mark_impression(text, wrong=True)
