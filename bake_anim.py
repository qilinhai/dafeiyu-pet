# -*- coding: utf-8 -*-
"""把 dsh-pet 的 webm 动画烤成 PNG 序列帧（带 alpha），供 PySide6 直接画。

为什么不用 QMediaPlayer 播 webm：
    实测 Qt 解出来是 NV12，alpha 全是 255 —— VP9 的透明通道被丢掉，
    桌宠会变成一个黑底方块。烤成 PNG 是唯一稳的路。

两个关键设计：
1) **全局裁切框**：每个动作单独裁边，切换动作时角色会突然变大变小、位置跳。
   先扫所有动画的 alpha 并集，算一个全局框，所有帧按同一个框裁。
2) **按时间抽帧**：素材容器里声明的帧率和实际帧数对不上（声明 24，实际 12），
   按 frame.time 抽才准，不靠 fps 元数据。

用法：
    python bake_anim.py --probe            # 只看会切成多大，不写文件
    python bake_anim.py                    # 默认 12fps / 3 秒 / 340 高
    python bake_anim.py --fps 12 --seconds 3 --height 340
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import av
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "assets_webm")
# 烤到新目录而不是覆盖旧目录：清理几万张旧帧会被安全策略分批拦下来，
# 而且中途失败会留下半新半旧的帧，长短不一比不更新还糟。
DST = os.path.join(HERE, "assets_anim_v2")
META = os.path.join(DST, "meta.json")

SHORT = {
    "待机呼吸休闲": "idle",
    "东张西望": "peek",
    "哈欠连天": "yawn",
    "超大伸懒腰": "stretch",
    "打瞌睡被惊醒": "doze",
    "小幅度原地360度旋转展示": "spin",
    "原地重力下蹲压缩": "crouch",
    "被吓一跳": "startle",
    "点击回应-元气挥手": "click_wave",
    "点击回应-傲娇生气": "click_angry",
    "点击回应-害羞惊讶": "click_shy",
    "点击回应-开心跃动": "click_happy",
    "点击回应-挠痒咯咯笑": "click_tickle",
    "被鼠标拖拽悬空反馈": "drag",
    "睡觉-开始": "sleep_in",
    "睡觉-循环": "sleep_loop",
    "睡觉-起来": "sleep_out",

    # ---- 钱：大肥鱼的核心立场就是「钱的事当天记」 ----
    "余额-分文不剩": "wallet_broke",
    "余额-数金皱眉": "wallet_count",
    "余额-袋空如洗": "wallet_empty",
    "余额-金袋叮当": "wallet_coins",
    "余额-钱袋如常": "wallet_normal",
    "余额-钱袋满溢": "wallet_full",
    "收红包": "redpacket",
    "吃Token": "eat_token",

    # ---- 干活 ----
    "写代码": "coding",
    "轻快记录": "note_taking",
    "工作状态-忙碌点按": "work_busy",
    "工作状态-思考冒泡": "work_think",
    "工作状态-清点归档": "work_archive",
    "工作状态-雀跃庆祝": "work_cheer",
    "工作状态-原地踱步张望": "work_pace",
    "工作状态-垂头叹气冒汗": "work_sigh",
    "原地敲击桌面互动": "tap_desk",
    "原地专心玩魔方": "rubik",
    "下五子棋": "gomoku",

    # ---- 不耐烦 / 情绪 ----
    "用鲸鱼尾巴拍打地面": "tail_slap",
    "玩游戏气急败坏": "game_rage",
    "深度思考碎碎念": "think_mumble",
    "碎碎念-发呆碎碎念": "mumble_daze",
    "碎碎念-对屏碎碎念": "mumble_screen",
    "碎碎念-擦桌碎碎念": "mumble_wiping",

    # ---- 歇着 ----
    "原地小憩沉眠": "nap",
    "原地漂浮踏步": "float_step",
    "原地左转奔跑": "run",
    "侧身急躁跑": "run_side",
    "开心小跑": "trot_happy",
    "悠闲哼歌": "hum",
    "照镜子": "mirror",
    "撸猫": "cat",

    # ---- 得意 / 表演 ----
    "优雅女仆舞": "dance_maid",
    "可爱宅舞": "dance_cute",
    "轻快摇摆舞": "dance_sway",
    "女仆屈膝礼仪": "curtsey",
    "吹笛子": "flute",
    "小提琴演奏": "violin",
    "三球抛接": "juggle_balls",
    "扑克魔术": "magic_card",
    "凭空生花": "magic_flower",
    "变鸽子": "pigeon",
    "萌化小幽灵": "ghost",
    "整体换装试色": "dressup",

    # ---- 吃 ----
    "吃早餐": "eat_breakfast",
    "吃午餐": "eat_lunch",
    "吃晚餐": "eat_dinner",
    "吃白饭": "eat_rice",
    "吃饭·碗饭": "eat_bowl",
    "吃饭团": "eat_onigiri",
    "期待开饭": "expect_meal",
    "吃饺子": "eat_dumpling",
    "吃汤圆": "eat_tangyuan",
    "吃粽子": "eat_zongzi",
    "吃年糕": "eat_niangao",
    "吃青团": "eat_qingtuan",
    "吃腊八粥": "eat_laba",
    "吃重阳糕": "eat_chongyang",
    "吃长寿面": "eat_noodles",
    "吃大闸蟹": "eat_crab",
    "吃西瓜": "eat_watermelon",
    "吃糖葫芦": "eat_tanghulu",
    "吃冰淇淋融化": "eat_icecream",
    "涮火锅": "eat_hotpot",
    "大口吃零食": "snack_big",
    "偷吃零食被抓住": "snack_caught",
    "是啊，吃什么": "what_to_eat",
    "晨间刷牙": "brush",

    # ---- 鲸鱼本体（形象梗）----
    "蓝鲸现世": "whale",
    "鲸鱼吐泡泡特效": "bubbles",
    "动物环绕": "animals",
    "蝴蝶蜜蜂环绕头顶开花": "butterfly",
    "螃蟹走路": "crab_walk",

    # ---- 时令 / 氛围 ----
    "中秋赏月吃月饼": "mooncake",
    "放烟花": "fireworks",
    "放孔明灯": "lantern",
    "放河灯": "river_lantern",
    "放风筝": "kite",
    "堆雪人": "snowman",
    "装点圣诞树": "xmas_tree",
    "讨糖南瓜灯": "pumpkin",
    "拆礼物": "gift",
    "写福字": "write_fu",
    "舞狮头": "lion_dance",
    "插茱萸赏菊": "chrysanthemum",
    "穿针乞巧": "needle",
    "被落叶淹没": "leaves",
    "摇扇纳凉": "fan",
    "吹气球": "balloon",
    "玩水枪": "watergun",
    "荡秋千": "swing",
    "骑木马": "hobbyhorse",
    "踢毽子": "shuttlecock",
    "抽陀螺": "top",
    "原地跳跚抓碎头顶物品": "jump_grab",
    "原地跳跃抓碎头顶物品": "jump_grab",
    "原地蹲下玩玩具汽车": "toy_car",
}


def trim_static(frames, thr=1.2):
    """裁掉头尾的静止段。

    素材是 10 秒的成品，但很多动作真正在动的只有中间五六秒，
    头尾各僵着一两秒。这些帧不裁的话，桌宠点开就干站着等它动，
    而且白占磁盘（一帧几十 KB，109 个动作十几 MB 就打水漂了）。

    只裁头尾，中间的一动不动——中间停顿是动作本身的节奏，裁了会变鬼畜。
    """
    n = len(frames)
    if n < 5:
        return frames
    sig = [f[::12, ::12, 3].astype(np.int16) for f in frames]

    def same(i):
        return float(np.abs(sig[i] - sig[i - 1]).mean()) < thr

    i0 = 0
    while i0 + 1 < n and same(i0 + 1):
        i0 += 1                      # 停在静止段的最后一帧，留一帧当开头
    j1 = n - 1
    while j1 - 1 > i0 and same(j1):
        j1 -= 1                      # 同理，留一帧当结尾
    return frames[i0:j1 + 1]


def body_box(frames):
    """角色本体的大致范围：取每一帧包围盒的中位数。

    这里踩过一次坑：一开始拿所有帧的**并集**当范围，结果「吹气球」
    「大口吃零食」「放烟花」都被判成超出画布要整体缩小——因为气球吹大了、
    零食洒开了、烟花炸开了。可角色本体一直是 205×255，动都没动。
    按并集缩，鱼会莫名其妙缩成一半。

    取中位数就绕开了这些瞬间：绝大多数帧里只有角色，道具是少数。
    """
    bs = [b for b in (bbox(a) for a in frames) if b]
    if not bs:
        return None
    return (int(np.median([b[0] for b in bs])), int(np.median([b[1] for b in bs])),
            int(np.median([b[2] for b in bs])), int(np.median([b[3] for b in bs])))


def fit_plan(frames, gbox):
    """决定这个动作要不要整体缩小，返回 (本体框 + 缩放比)。

    s 恒为 1.0 是常态——道具飞出画布就让它飞出去，那本来也是「出画」的效果。
    只有本体真的比画布大（蓝鲸现世那种）才缩。
    """
    bb = body_box(frames)
    if bb is None:
        return None
    bw, bh = bb[2] - bb[0] + 1, bb[3] - bb[1] + 1
    cw, ch = gbox[2] - gbox[0] + 1, gbox[3] - gbox[1] + 1
    return bb[0], bb[1], bb[2], bb[3], min(1.0, cw / bw, ch / bh)


def compose(arr, box, plan, out):
    """一帧 → 统一画布上的成品图。

    s=1：按同一套框裁，缩放到输出尺寸。位置绝对稳，109 个动作共用一个基准。
    s<1：把取景范围按比例放大再缩回去，等于镜头拉远，角色仍在原位。
    """
    img = Image.fromarray(arr, "RGBA")
    bx0, by0, bx1, by1 = box
    bw, bh = bx1 - bx0 + 1, by1 - by0 + 1
    fx0, fy0, fx1, fy1, s = plan
    if s >= 0.999:
        img = img.crop((bx0, by0, bx1 + 1, by1 + 1))
        if img.size != out:
            img = img.resize(out, Image.LANCZOS)
        return img
    vw, vh = int(round(bw / s)), int(round(bh / s))
    vh = min(vh, arr.shape[0])
    vw = min(vw, arr.shape[1])
    cx = (fx0 + fx1) // 2
    vx0 = max(0, min(cx - vw // 2, arr.shape[1] - vw))
    vy0 = max(0, min(by1 - vh + 1, arr.shape[0] - vh))
    img = img.crop((vx0, vy0, vx0 + vw, vy0 + vh)).resize(out, Image.LANCZOS)
    return img


def decode(path, target_fps=None, max_seconds=None):
    """解出 RGBA 帧。

    **必须用 libvpx-vp9 解码器**：PyAV 默认的 native vp9 不处理 matroska 的
    BlockAdditional，VP9 的 alpha 就存在那里。用默认解码器解出来是 yuv420p，
    alpha 全是 255，桌面上一放就是个黑底方块。这个坑排查了很久，别改回去。

    抽帧按 packet 时间戳算，不信任容器声明的 fps（声明 24，实际 12）。
    """
    frames = []
    last_idx = -1
    codec = av.CodecContext.create("libvpx-vp9", "r")
    with av.open(path) as container:
        stream = container.streams.video[0]
        tb = stream.time_base or 0
        stopped = False
        for packet in container.demux(stream):
            if packet.size == 0:                       # flush 包
                for frame in codec.decode(packet):
                    arr = frame.reformat(format="rgba").to_ndarray()
                    frames.append(arr)
                continue
            t = float(packet.pts * tb) if packet.pts is not None else 0.0
            if max_seconds and t > max_seconds:
                stopped = True
                break
            for frame in codec.decode(packet):
                if target_fps:
                    idx = int(round(t * target_fps))
                    if idx == last_idx:
                        continue
                    last_idx = idx
                arr = frame.reformat(format="rgba").to_ndarray()
                frames.append(arr)
        if stopped:
            pass
    return frames


def bbox(arr, thr=48, min_px=4):
    """这一帧里不透明内容的边界。

    thr 不能太低：VP9 压缩会在角色周围留一圈 alpha 很弱的脏像素，
    阈值取 8 的话整个 640×360 都算有内容，等于白裁。
    另外行列上至少 min_px 个像素才算边界，单点噪点直接忽略。
    """
    a = arr[:, :, 3]
    mask = a > thr
    rows = np.where(mask.sum(axis=1) >= min_px)[0]
    cols = np.where(mask.sum(axis=0) >= min_px)[0]
    if len(rows) == 0 or len(cols) == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])


def scan_box(files, fps, seconds, sample=3):
    """第一遍：只扫裁切框，不驻留帧。

    109 个动作全解出来塞内存是 1.2GB，直接爆。这一步每个动作只抽样
    sample 分之一的帧算 alpha 并集，够准了。
    """
    gx0 = gy0 = 10 ** 9
    gx1 = gy1 = -1
    src = None
    info = {}
    for f in files:
        stem = f[:-5]
        boxes = []
        n = 0
        try:
            codec = av.CodecContext.create("libvpx-vp9", "r")
            with av.open(os.path.join(SRC, f)) as c:
                st = c.streams.video[0]
                tb = st.time_base or 0
                for pkt in c.demux(st):
                    if pkt.size == 0:
                        continue
                    t = float(pkt.pts * tb) if pkt.pts is not None else 0.0
                    if seconds and t > seconds:
                        break
                    for fr in codec.decode(pkt):
                        n += 1
                        if n % sample:
                            continue
                        arr = fr.reformat(format="rgba").to_ndarray()
                        if src is None:
                            src = (arr.shape[1], arr.shape[0])
                        b = bbox(arr)
                        if b:
                            boxes.append(b)
        except Exception as e:
            print(f"  {stem}: 扫描失败 {e}")
            continue
        if not boxes:
            print(f"  {stem}: 全透明，跳过")
            continue
        info[stem] = len(boxes)
        gx0 = min(gx0, min(b[0] for b in boxes))
        gy0 = min(gy0, min(b[1] for b in boxes))
        gx1 = max(gx1, max(b[2] for b in boxes))
        gy1 = max(gy1, max(b[3] for b in boxes))
    return (gx0, gy0, gx1, gy1), src, info


def ref_box(ref_name, fps, seconds, pad_ratio=0.18):
    """只按基准动作（待机）算框，再按比例放大。

    为什么不用全量并集：烟花、动物环绕这类特效动作画面范围极大，
    并进来会把画布撑到接近整幅，角色本体被压得极小，且切动作时位置乱跳。
    以角色本体为准，特效超出的部分裁掉——桌宠窗口本来就那么点大。
    """
    p = os.path.join(SRC, ref_name + ".webm")
    if not os.path.exists(p):
        return None
    frames = decode(p, fps, seconds)
    boxes = [b for b in (bbox(a) for a in frames) if b]
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    w, h = x1 - x0 + 1, y1 - y0 + 1
    mx, my = int(w * pad_ratio), int(h * pad_ratio * 0.7)
    sh, sw = frames[0].shape[0], frames[0].shape[1]
    return (max(0, x0 - mx), max(0, y0 - my),
            min(sw - 1, x1 + mx), min(sh - 1, y1 + my))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=0,
                    help="0 = 全长（素材都是 10 秒，默认全烤后再裁静止段）")
    ap.add_argument("--height", type=int, default=340)
    ap.add_argument("--ref", default="待机呼吸休闲",
                    help="按哪个动作的体型定画布")
    ap.add_argument("--mode", default="ref", choices=["ref", "union"],
                    help="ref=按基准动作定框（默认，稳）；union=全量并集")
    ap.add_argument("--only", default="", help="只处理名字含该子串的动作")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(SRC):
        print(f"没有素材目录：{SRC}")
        return 1
    files = [f for f in sorted(os.listdir(SRC)) if f.endswith(".webm")]
    if args.only:
        files = [f for f in files if args.only in f]
    if not files:
        print("assets_webm 里没有 webm，先跑 fetch_assets.py --all")
        return 1

    print(f"扫描 {len(files)} 个动画……")
    if args.mode == "ref":
        box = ref_box(args.ref, args.fps, args.seconds)
        if not box:
            print(f"基准动作 {args.ref} 不可用，退回 union")
            box, src, _ = scan_box(files, args.fps, args.seconds)
        else:
            src = None
    else:
        box, src, _ = scan_box(files, args.fps, args.seconds)

    if box[2] < 0:
        print("没有解出任何有效帧")
        return 1
    if src is None:
        # ref 模式下要知道原图尺寸，从基准动作拿
        fr = decode(os.path.join(SRC, args.ref + ".webm"), 1, 0.2)
        src = (fr[0].shape[1], fr[0].shape[0])

    gx0, gy0, gx1, gy1 = box
    pad = 3
    gx0, gy0 = max(0, gx0 - pad), max(0, gy0 - pad)
    gx1, gy1 = min(src[0] - 1, gx1 + pad), min(src[1] - 1, gy1 + pad)
    # 上边界直接顶到素材顶边。
    # 原来按待机动作上下各留 18% 余量，结果「原地跳跃」「放风筝」「荡秋千」
    # 这些角色会跳出取景框，头顶被切掉一截——检测到 31 个动作有贴边，
    # 其中 5 个是切头。素材本身已经给跳跃留了空间，跟着它走就对了。
    gy0 = 0
    cw, ch = gx1 - gx0 + 1, gy1 - gy0 + 1

    scale = args.height / ch
    out_w, out_h = round(cw * scale), round(ch * scale)
    print(f"画布 ({gx0},{gy0})→({gx1},{gy1}) = {cw}×{ch}  "
          f"→ 输出 {out_w}×{out_h}")

    if args.probe:
        print("（probe 模式，未写文件）")
        return 0

    # 不整目录删：一是没必要（每个动作目录下面会清旧帧），
    # 二是整目录 rmtree 会被安全策略拦下来。
    os.makedirs(DST, exist_ok=True)

    meta_path = META
    meta = {"canvas": [out_w, out_h], "crop": [gx0, gy0, cw, ch],
            "fps": args.fps, "anims": {}}
    if args.only and os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)

    total_bytes = 0
    total_frames = 0
    ok = 0
    fits = []
    for f in files:
        stem = f[:-5]
        name = SHORT.get(stem, stem)
        try:
            frames = decode(os.path.join(SRC, f), args.fps, args.seconds)
        except Exception as e:
            print(f"  {stem}: 解码失败 {e}")
            continue
        if not frames:
            print(f"  {stem}: 无帧")
            continue
        raw_n = len(frames)
        frames = trim_static(frames)
        fi = fit_plan(frames, (gx0, gy0, gx1, gy1))
        if fi is None:
            print(f"  {stem}: 全透明，跳过")
            continue
        if fi[4] < 0.999:
            fits.append((name, round(fi[4], 2)))
        d = os.path.join(DST, name)
        os.makedirs(d, exist_ok=True)
        for i, arr in enumerate(frames):
            img = compose(arr, (gx0, gy0, gx1, gy1), fi, (out_w, out_h))
            fp = os.path.join(d, f"f{i:03d}.webp")
            # RGB 有损、alpha 无损：边缘才不会有色晕
            img.save(fp, "WEBP", quality=82, alpha_quality=100, method=4)
            total_bytes += os.path.getsize(fp)
        meta["anims"][name] = {"frames": len(frames), "fps": args.fps,
                               "src": stem, "fit": round(fi[4], 3)}
        total_frames += len(frames)
        ok += 1
        del frames
        tag = f"  缩放{fi[4]:.2f}" if fi[4] < 0.999 else ""
        print(f"  {name:18} {meta['anims'][name]['frames']:3} 帧"
              f"（{raw_n}→裁静止）{tag}")

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f"\n完成：{ok} 个动作，{total_frames} 帧，"
          f"磁盘 {total_bytes/1024/1024:.1f} MB")
    if fits:
        print("超出画布、整体缩放进来的动作：")
        for n, s in fits:
            print(f"  {n:18} ×{s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
