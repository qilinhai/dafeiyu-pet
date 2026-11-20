"""每日签到积分守门人。

背景：BreezeBlue 每天能白领 400 积分（约 2000 字），过期不补。
      /v1/balance 的 topups 里有一条 description="daily claim credits" 的入账记录，
      拿它就能判断「今天到底领没领」，不需要猜。

判断口径（两层，避免时区差误报）：
  1. 距上次 daily claim >= NAG_AFTER_HOURS（默认 20 小时）→ 新周期已开，可以催
  2. 该入账的本地日期 != 今天 → 就是今天还没领
  两条都成立才催，宁可少催一次，不冤枉人。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import requests

API_BASE = "https://api.breeze.blue/v1"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
CLAIM_DESC = "daily claim"
NAG_AFTER_HOURS = 20       # 距上次领取超过这么久，说明新的一天开始了
LOW_CREDITS = 120.0        # 低于这个数：能念 600 字左右，要省着用
CRITICAL_CREDITS = 40.0    # 低于这个数：基本哑巴边缘


def _headers(key):
    return {"xi-api-key": key, "User-Agent": UA, "Accept": "application/json"}


def fetch_balance(key, timeout=20):
    """返回 /v1/balance 原始字典；失败返回 None。"""
    key = str(key or "").strip()
    if not key:
        return None
    try:
        r = requests.get(f"{API_BASE}/balance", headers=_headers(key), timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _parse_dt(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone()
    except Exception:
        return None


def daily_claim_status(key, now=None):
    """核心判定。

    返回 dict：
      ok          是否查到了（False 时 reason 说明原因）
      balance     当前积分
      claimed_today  今天的 400 分领了没
      should_nag  要不要催
      hours_since 距上次领取多少小时（None = 从没领过）
      low         余额是否偏低
      critical    余额是否已到危险线
      last_claim  上次领取的本地时间
      today_claim 今天那笔的积分（没领则 0）
    """
    now = now or datetime.now().astimezone()
    d = fetch_balance(key)
    if d is None:
        return {"ok": False, "reason": "查询失败", "balance": None,
                "claimed_today": None, "should_nag": False}

    try:
        balance = float(d.get("balance") or 0)
    except (TypeError, ValueError):
        balance = 0.0

    claims = []
    for t in (d.get("topups") or []):
        if CLAIM_DESC in str(t.get("description") or "").lower():
            dt = _parse_dt(t.get("created_at"))
            if dt is not None:
                claims.append((dt, float(t.get("amount") or 0)))
    claims.sort(key=lambda x: x[0], reverse=True)

    last_dt, last_amt = (claims[0] if claims else (None, 0.0))
    hours = None
    if last_dt is not None:
        hours = (now - last_dt).total_seconds() / 3600.0

    claimed_today = bool(last_dt is not None and last_dt.date() == now.date())
    today_claim = last_amt if claimed_today else 0.0

    should_nag = False
    if not claimed_today:
        # 没领过，或者距上次已经过了 NAG_AFTER_HOURS
        should_nag = (hours is None) or (hours >= NAG_AFTER_HOURS)

    res = {
        "ok": True,
        "balance": round(balance, 1),
        "claimed_today": claimed_today,
        "should_nag": should_nag,
        "hours_since": None if hours is None else round(hours, 1),
        "low": balance < LOW_CREDITS,
        "critical": balance < CRITICAL_CREDITS,
        "last_claim": last_dt.strftime("%Y-%m-%d %H:%M") if last_dt else "",
        "today_claim": round(today_claim, 1),
        "checked_at": now.strftime("%Y-%m-%d %H:%M"),
    }
    # 仅调试：DAFEIYU_FAKE_UNCLAIMED=1 假装今天没领，用来验证「催」的整条链路
    if os.environ.get("DAFEIYU_FAKE_UNCLAIMED") == "1":
        res.update({"claimed_today": False, "should_nag": True, "today_claim": 0.0,
                    "debug": "fake_unclaimed"})
    return res


def pick_line(st):
    """把状态翻译成一句人话（桌宠气泡 / 命令行共用）。"""
    if not st.get("ok"):
        return "积分没查到，网断了还是 Key 挂了？"

    bal = st.get("balance") or 0
    if st.get("should_nag"):
        if st.get("critical"):
            return (f"只剩 {bal:g} 积分了，今天那 400 分还没领——再不领我今晚就得闭嘴。"
                    f"breezeblue.ai 订阅页，签到")
        if st.get("low"):
            return f"今天的 400 积分还没领，卡里只剩 {bal:g} 了，先领再去念长句"
        return f"今天那 400 积分还没领呢，领一下，别让我哪天突然哑了"

    if st.get("claimed_today"):
        if st.get("critical"):
            return f"今天领过了（+{st.get('today_claim'):g}），但只剩 {bal:g}，省着点用"
        return f"今天领过了，卡里 {bal:g} 分，够念一阵子"

    # 今天没领，但距上次还不到 20 小时：新周期大概率还没开
    h = st.get("hours_since")
    if h is None:
        return "还没领过每日积分，去订阅页点一下"
    return f"上次领是 {h:g} 小时前，新的一天应该还没到，卡里 {bal:g} 分"


def nag_interval_minutes(st):
    """催的间隔：越接近哑巴催得越急。"""
    if st.get("critical"):
        return 20
    if st.get("low"):
        return 35
    return 60
