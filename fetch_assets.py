# -*- coding: utf-8 -*-
"""从 dsh-pet 仓库拉动画素材（走 ghproxy，raw.githubusercontent 在这台机器被 SSL 拦）。

只拉用得上的那批：待机 / 点击 / 拖拽 / 睡觉 / 小动作。
全量 110 个约 55MB，没必要——桌宠真正会用到的就这十几个。

用法：
    python fetch_assets.py            # 拉默认清单
    python fetch_assets.py --list     # 只看会拉什么
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE = ("https://ghproxy.net/https://raw.githubusercontent.com/"
        "PC2005-cloud/dsh-pet/main/dsh-pet/assets/webm/")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets_webm")

# 名字 → 用途。取名时按用途归，集成时好找。
WANTED = [
    # 待机
    "待机呼吸休闲.webm",
    "东张西望.webm",
    "哈欠连天.webm",
    "超大伸懒腰.webm",
    "打瞌睡被惊醒.webm",
    "小幅度原地360度旋转展示.webm",
    "原地重力下蹲压缩.webm",
    "被吓一跳.webm",
    # 点击回应
    "点击回应-元气挥手.webm",
    "点击回应-傲娇生气.webm",
    "点击回应-害羞惊讶.webm",
    "点击回应-开心跃动.webm",
    # 拖拽
    "被鼠标拖拽悬空反馈.webm",
    # 睡觉
    "睡觉-开始.webm",
    "睡觉-循环.webm",
    "睡觉-起来.webm",
]


def all_names():
    """仓库里的全量清单（_remote_list.json 由 api.github.com 拉下来）。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_remote_list.json")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [x["name"] for x in json.load(f)]


def already(name):
    p = os.path.join(OUT, name)
    return os.path.exists(p) and os.path.getsize(p) > 50_000


def fetch(name, retries=3):
    path = os.path.join(OUT, name)
    url = BASE + urllib.parse.quote(name)
    for i in range(retries):
        try:
            r = requests.get(url, timeout=90,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and len(r.content) > 50_000:
                with open(path, "wb") as f:
                    f.write(r.content)
                return len(r.content)
            print(f"    HTTP {r.status_code}  {len(r.content)} bytes")
        except Exception as e:
            print(f"    第 {i+1} 次失败: {type(e).__name__} {str(e)[:70]}")
        time.sleep(2)
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    wanted = all_names() if "--all" in sys.argv else WANTED
    if not wanted:
        print("拿不到全量清单，先取 _remote_list.json")
        return 1
    if "--list" in sys.argv:
        for n in wanted:
            print(("有 " if already(n) else "缺 ") + n)
        return 0

    print(f"目标 {len(wanted)} 个")
    todo = []
    for n in wanted:
        fn = n if n.endswith(".webm") else n + ".webm"
        if not already(fn):
            todo.append(fn)
    skip = len(wanted) - len(todo)
    if not todo:
        print(f"全部已就位（{skip} 个）")
        return 0
    print(f"需要下载 {len(todo)} 个，跳过 {skip} 个。并发 8 路……")

    results = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch, n): n for n in todo}
        for fu in as_completed(futs):
            n = futs[fu]
            size = fu.result()
            results[n] = size
            if size:
                print(f"  ✓ {n}  {size/1024:.0f} KB")
            else:
                print(f"  ✗ {n} 失败")

    ok = sum(1 for v in results.values() if v)
    fail = len(results) - ok
    total = sum(v for v in results.values() if v)
    print(f"\n完成：新下 {ok} 个（{total/1024/1024:.1f} MB），跳过 {skip}，失败 {fail}")
    if fail:
        print("失败的可重跑一次，已下好的会自动跳过。")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
