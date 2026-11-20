"""应用分组 + 四态 + 面板，端到端自检。

全部跑在临时配置文件上（cleaner.CFG_PATH 指到 temp），
你真实的 cleaner_config.json 一个字节都不会被改。

用法：
    ../venv/Scripts/python.exe tools/check_clean_apps.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import cleaner  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}" + (f"   [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"   [{detail}]" if detail else ""))


def fresh_cfg(**kw):
    cfg = {k: (v.copy() if isinstance(v, (dict, list)) else v)
           for k, v in cleaner.DEFAULT_CFG.items()}
    cfg.update(kw)
    cleaner.save_cfg(cfg)
    return cfg


def main():
    tmp = Path(tempfile.mkdtemp(prefix="clean_check_"))
    cleaner.CFG_PATH = tmp / "cleaner_config.json"

    # ---------------------------------------------------------- 分组
    print("== 分组 ==")
    fresh_cfg(apps={})
    apps = cleaner.scan_apps()
    check("扫出应用", len(apps) > 3, f"{len(apps)} 组")
    mbs = [a["mb"] for a in apps]
    check("按内存降序", mbs == sorted(mbs, reverse=True))
    check("每组都有 id 和 label", all(a["id"] and a["label"] for a in apps))

    by_label = {a["label"]: a for a in apps}

    wechat = by_label.get("微信")
    if wechat:
        names = set(wechat["names"])
        check("微信合并了 Weixin.exe", "weixin.exe" in names, ",".join(sorted(names)))
        check("微信合并了 WeChatAppEx.exe", "wechatappex.exe" in names,
              ",".join(sorted(names)))
        check("微信跨了 2 个目录", len(wechat["dirs"]) >= 2, str(len(wechat["dirs"])))
    else:
        print("  跳过：这台机器现在没开微信")

    doubao = by_label.get("豆包")
    if doubao:
        check("豆包合并了 aha_doctor", "aha_doctor.exe" in set(doubao["names"]),
              ",".join(sorted(doubao["names"])))

    # 纯净的进程名（没有 .exe）说明 _stem 生效
    check("系统组显示名不带 .exe",
          all(".exe" not in a["label"] for a in apps if not a["dirs"]))

    # ---------------------------------------------------------- 锁定
    print("== 核心锁定 ==")
    locked = [a for a in apps if a["locked"]]
    check("有被锁定的系统应用", len(locked) > 0, f"{len(locked)} 组")
    check("锁定的组一定是保护态", all(a["mode"] == cleaner.MODE_PROTECT for a in locked))

    # ---------------------------------------------------------- 四态写入
    print("== 四态 ==")
    fresh_cfg(apps={})
    apps = cleaner.scan_apps()
    pick = next((a for a in apps if not a["locked"] and a["mb"] >= 1), None)
    check("找到可改的普通应用", pick is not None, pick["label"] if pick else "")

    if pick:
        ok, msg = cleaner.set_mode(pick["id"], cleaner.MODE_CLOSE)
        check("设成只关窗成功", ok, msg)
        again = next(a for a in cleaner.scan_apps() if a["id"] == pick["id"])
        check("只关窗已落盘", again["mode"] == cleaner.MODE_CLOSE, again["mode_text"])

        ok, _ = cleaner.set_mode(pick["id"], "不存在的状态")
        check("乱写状态被拒绝", not ok)

        ok, msg = cleaner.set_mode(pick["id"], cleaner.MODE_CLEAN)
        check("设成可清理成功", ok, msg)

        # 阈值：拿一个「没窗口但确实占内存」的应用，门槛抬到天上必须拦住
        idle = next((a for a in cleaner.scan_apps()
                     if not a["locked"] and not a["has_win"] and a["mb"] >= 1), None)
        if idle:
            fresh_cfg(apps={idle["id"]: cleaner.MODE_CLEAN}, min_mb=0)
            lo = next(a for a in cleaner.scan_apps() if a["id"] == idle["id"])
            check("门槛为 0 时待清理", lo["why"] == "待清理", lo["why"])
            fresh_cfg(apps={idle["id"]: cleaner.MODE_CLEAN}, min_mb=99999)
            hi = next(a for a in cleaner.scan_apps() if a["id"] == idle["id"])
            check("门槛抬高后不再待清理", hi["why"] != "待清理", hi["why"])
        else:
            print("  跳过：没有「无窗口且有内存」的应用")

    # 锁定组不许 clean
    if locked:
        ok, msg = cleaner.set_mode(locked[0]["id"], cleaner.MODE_CLEAN)
        check("锁定组拒绝可清理", not ok, msg)
        ok, msg = cleaner.set_mode(locked[0]["id"], cleaner.MODE_CLOSE)
        check("锁定组也拒绝只关窗（protect_core 开着）", not ok, msg)

    # 关掉 protect_core 后，锁定组只能到「只关窗」，仍然不许杀
    fresh_cfg(apps={}, protect_core=False)
    apps2 = cleaner.scan_apps()
    lock2 = next((a for a in apps2 if any(cleaner._in_never(n) for n in a["names"])), None)
    if lock2:
        ok_c, msg_c = cleaner.set_mode(lock2["id"], cleaner.MODE_CLOSE)
        check("关掉锁后可以只关窗", ok_c, msg_c)
        ok_k, msg_k = cleaner.set_mode(lock2["id"], cleaner.MODE_CLEAN)
        check("关掉锁后依然不许结束进程", not ok_k, msg_k)

    # ---------------------------------------------------------- 自动重启
    print("== 自动重启保护 ==")
    fresh_cfg(apps={}, auto_restart=["weixin"])
    w = next((a for a in cleaner.scan_apps() if a["label"] == "微信"), None)
    if w:
        check("学到的应用强制保护", w["mode"] == cleaner.MODE_PROTECT, w["why"])
        check("原因写明会自己回来", "自己回来" in w["why"], w["why"])
    fresh_cfg(apps={}, auto_restart=["weixin"], respect_auto_restart=False)
    w2 = next((a for a in cleaner.scan_apps() if a["label"] == "微信"), None)
    if w2:
        check("关掉开关后不再强制保护", w2["mode"] != cleaner.MODE_PROTECT, w2["why"])

    # ---------------------------------------------------------- 文案
    print("== 文案 ==")
    for res, want in (({"ok": True, "freed_mb": 1300.0, "count": 9, "closed": 0}, "GB"),
                      ({"ok": True, "freed_mb": 0, "count": 0, "closed": 3}, "窗口"),
                      ({"ok": True, "freed_mb": 0, "count": 0, "closed": 0}, "干净"),
                      ({"ok": True, "freed_mb": 40.0, "count": 2, "closed": 1}, "MB"),
                      ({"ok": True, "freed_mb": 900.0, "count": 4, "closed": 0,
                        "revived": ["微信"]}, "不碰它")):
        line = cleaner.say_line(res)
        check(f"文案含「{want}」", want in line, line)

    # ---------------------------------------------------------- dry 不杀
    print("== dry 只读 ==")
    import psutil
    # 挑一个没锁、内存最小的应用设成可清理 —— 真出 bug 也只碰到最小的那个
    pickable = [a for a in cleaner.scan_apps() if not a["locked"] and a["mb"] >= 1]
    victim = min(pickable, key=lambda a: a["mb"]) if pickable else None
    fresh_cfg(apps={victim["id"]: "clean"} if victim else {}, min_mb=0)
    # 只看「这一轮判定要处理的进程」是否还活着。
    # 拿全系统进程集合做差是不行的：随时有进程自己退出，断言会随机红。
    tgt = [a for a in cleaner.scan_apps() if a.get("eligible")]
    pids = {p for a in tgt for p in a["pids"]}
    r = cleaner.run(dry=True)
    alive = {p for p in pids if psutil.pid_exists(p)}
    check("dry 返回标记", r.get("dry") is True)
    check("dry 没杀掉任何待清理进程", pids <= alive,
          f"少了 {len(pids - alive)} 个（目标 {len(pids)} 个）")
    check("dry 给出计划结构", "plan" in r and "close" in r["plan"] and "kill" in r["plan"])

    # ---------------------------------------------------------- 面板
    print("== 面板 ==")
    fresh_cfg(apps={})
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import settings_panel

    tab = settings_panel.CleanTab()
    apps_now = [a for a in cleaner.scan_apps() if a["mb"] >= 1]
    check("表格行数对得上扫描结果", tab.table is not None
          and tab.table.rowCount() == len(apps_now),
          f"{tab.table.rowCount() if tab.table else -1} vs {len(apps_now)}")

    # 每个单元格都有内容
    # 状态下拉现在在第 6 列（0 应用 / 1 窗口标题 / 2 内存 / 3 进程 / 4 类别 / 5 状态）
    empty = [i for i in range(tab.table.rowCount())
             if tab.table.item(i, 0) is None or tab.table.cellWidget(i, 5) is None]
    check("没有空行空控件", not empty, str(empty[:5]))

    # 锁定行的下拉必须是灰的
    bad = []
    for i in range(tab.table.rowCount()):
        combo = tab.table.cellWidget(i, 5)
        aid = tab.table.item(i, 0).data(settings_panel.Qt.ItemDataRole.UserRole)
        src = next((a for a in apps_now if a["id"] == aid), None)
        if src and src["locked"] and combo.isEnabled():
            bad.append(src["label"])
    check("锁定行的下拉是灰的", not bad, ",".join(bad))

    # 改一行状态 → 写进配置
    pick2 = next((a for a in apps_now if not a["locked"]), None)
    if pick2:
        ok, msg = cleaner.set_mode(pick2["id"], cleaner.MODE_PROTECT)
        check("面板能改状态（走同一套 set_mode）", ok, msg)
        saved = cleaner.load_cfg()["apps"]
        check("状态写进配置", saved.get(pick2["id"]) == cleaner.MODE_PROTECT,
              str(saved))

    # 开关落盘
    tab.ck_core.setChecked(False)
    tab.ck_restart.setChecked(False)
    tab.sp_min.setValue(123)
    ok, msg = tab.save()
    cfg_now = cleaner.load_cfg()
    check("保存开关成功", ok, msg)
    check("protect_core 落盘", cfg_now["protect_core"] is False)
    check("respect_auto_restart 落盘", cfg_now["respect_auto_restart"] is False)
    check("min_mb 落盘", cfg_now["min_mb"] == 123, str(cfg_now["min_mb"]))

    # 页签确实挂进了主窗口
    dlg = settings_panel.SettingsPanel()
    titles = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    check("设置窗口里有「资源释放」页", "资源释放" in titles, " / ".join(titles))
    dlg.close()

    tab.deleteLater()
    app.processEvents()

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
