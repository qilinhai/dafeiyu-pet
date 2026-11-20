"""资源释放面板自检：窗口标题列、系统筛选、搜索、列序、状态可改。

重点不是「控件建出来了」，而是：
  · 窗口标题真的从系统里读到了（不是空列）
  · 「只看系统」筛出来的每一行都标系统，「只看我装的」一行系统都没有
  · 搜索能搜到窗口标题里的字（用户就是看着标题找东西的）
  · 表格列序和表头一一对应，状态下拉还在第 6 列
全程离屏（offscreen），不显示窗口、不写配置。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PySide6.QtWidgets import QApplication  # noqa: E402

import settings_panel as sp  # noqa: E402
import cleaner  # noqa: E402

ok = fail = 0


def check(name, cond, got=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}   → {got}")


def main():
    app = QApplication.instance() or QApplication([])
    w = sp.CleanTab(pet=None)
    w.reload()

    apps = w._apps
    check("扫描到应用", len(apps) > 3, f"n={len(apps)}")
    check("七列表头", w.COLS == ("应用", "窗口标题", "内存", "进程", "类别",
                                "状态", "这一趟会怎么处理"), w.COLS)
    check("表头与列数一致", w.table.columnCount() == len(w.COLS),
          f"{w.table.columnCount()}")

    # ---- 窗口标题列真的有东西
    with_win = [a for a in apps if a.get("titles")]
    check("有应用采到窗口标题", len(with_win) >= 3,
          f"{len(with_win)} 个有标题")
    if with_win:
        sample = max(with_win, key=lambda a: a["mb"])
        check("标题非空且不是占位窗",
              all(t.strip() for t in sample["titles"]),
              sample["titles"][:2])

    # ---- 系统标记
    sys_apps = [a for a in apps if a.get("system")]
    usr_apps = [a for a in apps if not a.get("system")]
    check("识别出了系统级应用", len(sys_apps) >= 5, f"{len(sys_apps)} 个")
    check("识别出了用户应用", len(usr_apps) >= 5, f"{len(usr_apps)} 个")
    check("系统级里没有 svchost 被漏掉",
          any("svchost" in a["id"] for a in sys_apps),
          [a["id"] for a in sys_apps][:6])
    check("用户装的软件没被误标成系统",
          not any(a["id"] in ("doubao", "weixin", "quark") for a in sys_apps),
          [a["id"] for a in sys_apps if a["id"] in ("doubao", "weixin", "quark")])

    # ---- 筛选
    w.cb_filter.setCurrentIndex(1)          # 只看我装的
    n_user = w.table.rowCount()
    check("「只看我装的」行数 = 非系统应用数",
          n_user == len([a for a in usr_apps if a["mb"] >= 1]),
          f"{n_user} vs {len([a for a in usr_apps if a['mb'] >= 1])}")
    cats = [w.table.item(r, 4).text() for r in range(min(n_user, 40))]
    check("「只看我装的」里没有系统行", all(c == "我装的" for c in cats),
          [c for c in cats if c != "我装的"][:3])

    w.cb_filter.setCurrentIndex(2)          # 只看系统
    n_sys = w.table.rowCount()
    check("「只看系统」行数 = 系统应用数",
          n_sys == len([a for a in sys_apps if a["mb"] >= 1]),
          f"{n_sys} vs {len([a for a in sys_apps if a['mb'] >= 1])}")
    cats = [w.table.item(r, 4).text() for r in range(min(n_sys, 40))]
    check("「只看系统」里全是系统行", all(c == "系统" for c in cats),
          [c for c in cats if c != "系统"][:3])
    check("筛选后下拉还在（状态列没被筛掉）",
          w.table.cellWidget(0, 5) is not None, "cellWidget 为空")

    w.cb_filter.setCurrentIndex(0)
    check("切回全部，行数恢复", w.table.rowCount() == len([a for a in apps if a["mb"] >= 1]),
          f"{w.table.rowCount()}")

    # ---- 搜索（按窗口标题里的字搜）
    if with_win:
        probe = with_win[0]["titles"][0][:4]
        w.ed_search.setText(probe)
        hit = w.table.rowCount()
        check(f"搜「{probe}」能命中", hit >= 1, f"命中 {hit} 行")
        check("搜索结果确实含该标题",
              any(probe in (w.table.item(r, 1).text() or "")
                  for r in range(hit)),
              "标题列里找不到")
        w.ed_search.setText("")
        check("清空搜索后恢复", w.table.rowCount() == len([a for a in apps if a["mb"] >= 1]),
              f"{w.table.rowCount()}")

    # ---- 搜不存在的词
    w.ed_search.setText("zzz_不存在的东西_zzz")
    check("搜不到时是空表而不是报错", w.table.rowCount() == 0, w.table.rowCount())
    w.ed_search.setText("")

    # ---- 窗口标题列内容形态
    texts = [w.table.item(r, 1).text() for r in range(w.table.rowCount())]
    check("无窗口的显示「（无窗口）」或标题", all(t for t in texts), texts[:3])
    multi = [a for a in apps if len(a.get("titles") or []) > 1]
    if multi:
        check("多窗口用 +N 折叠", any("+" in t for t in texts), texts[:5])
    else:
        check("多窗口用 +N 折叠（当前没有多窗口应用，跳过）", True, "skip")

    print(f"\n{'=' * 40}\n通过 {ok} / {ok + fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
