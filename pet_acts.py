# -*- coding: utf-8 -*-
"""动作的「性格演绎」层：什么时候演哪个动作，演的时候说什么。

素材是别人画的（109 个动作），但**说什么**决定它像不像大肥鱼。
同一个「钱袋满溢」，换个萌宠会配「哇好多钱呀」，大肥鱼只会来一句
「这个月还能撑几天」。

三条规则：
1. 动作要和当下情境对得上（深夜不该跳宅舞，饭点不该写代码）
2. 台词必须是它自己的立场，不是素材原配的卖萌文案
3. 大多数动作不配台词——一直说话就是噪音，安静地做才有活物感

三条诚实边界（加新动作时必须遵守）：
A. 可以演「在看」，不能演「看懂了」。
   演「凑近屏幕」「托腮盯着」都行；不能说「这段逻辑有问题」「我看完了」——
   它没有屏幕内容，说了就是编。
B. 可以演「在算」，不能报「算出的数」。
   演「掰手指」「敲计算器」可以；不能报具体金额、余额、天数——
   数字只能来自真的查过（credits_guard 那条路），不能来自动作表演。
C. 可以演「被打断不爽」，不能演「知道你在忙什么」。
   演「甩尾巴」「叹气」可以；不能说「又在改那破代码」——
   除非 awareness 真的读到了窗口标题，否则那是猜的。

这不是洁癖。人格可以夸张，但一旦开始编造"它知道的事"，
下次它说真话你也不会信了——那才是真的坏了。
"""
from __future__ import annotations

import random

# 台词出口统一过人格守卫：禁词、越界词、表情一律拦。
# 单人跑 pet_acts 时（没有 pet_memory）就不过滤，别把鱼搞哑。
try:
    from pet_memory import guard_line as _guard_line
except Exception:                                   # pragma: no cover
    def _guard_line(_s):
        return True, ""

_GUARDED = {}


def _guarded(bank):
    """把这个动作的台词池过滤一遍，缓存起来。池子是模块常量，不会变。"""
    key = id(bank)
    ok = _GUARDED.get(key)
    if ok is None:
        ok = [s for s in bank if _guard_line(s)[0]]
        _GUARDED[key] = ok
    return ok


def clear_guard_cache():
    """人设改了之后清一次。

    不清的话旧结果还在缓存里，新加的拦截词要重启才生效——
    设置面板点了保存却没反应，是最容易让人以为「面板是假的」的那种坑。
    """
    _GUARDED.clear()

# ---------------------------------------------------------------- 动作分组
# 按「为什么做这件事」分组，比按素材名分组有用得多。
GROUPS = {
    "rest": [
        "idle", "peek", "yawn", "stretch", "nap", "doze", "float_step",
        "hum", "mumble_daze", "mumble_screen", "mumble_wiping",
        "mirror", "cat", "brush",
    ],
    "money": [
        "wallet_broke", "wallet_count", "wallet_empty", "wallet_coins",
        "wallet_normal", "wallet_full", "redpacket", "eat_token",
    ],
    "grind": [
        "coding", "note_taking", "work_busy", "work_think",
        "work_archive", "work_pace",
    ],
    "impatient": [
        "tail_slap", "game_rage", "work_sigh", "tap_desk", "think_mumble",
        "run_side",
    ],
    "proud": [
        "work_cheer", "dance_maid", "dance_cute", "dance_sway", "spin",
        "curtsey", "violin", "flute", "juggle_balls",
    ],
    "eat": [
        "eat", "meal", "sneak",
        "eat_breakfast", "eat_lunch", "eat_dinner", "eat_rice",
        "eat_dumpling", "eat_tangyuan", "eat_zongzi", "eat_niangao",
        "eat_qingtuan", "eat_laba", "eat_chongyang", "eat_noodles",
        "eat_crab", "eat_watermelon", "eat_tanghulu", "eat_icecream",
        "eat_hotpot", "snack_big", "snack_caught", "what_to_eat",
        "eat_bowl", "eat_onigiri", "expect_meal",
    ],
    "whale": ["whale", "bubbles", "animals", "butterfly", "crab_walk"],
    "play": [
        "kite", "swing", "hobbyhorse", "shuttlecock", "top", "balloon",
        "watergun", "rubik", "gomoku", "toy_car", "jump_grab", "run",
        "crouch", "walk", "jump", "trot_happy",
    ],
    "magic": ["magic_card", "magic_flower", "pigeon", "ghost", "dressup"],
    "festival": [
        "mooncake", "fireworks", "lantern", "river_lantern", "snowman",
        "xmas_tree", "pumpkin", "gift", "write_fu", "lion_dance",
        "chrysanthemum", "needle", "leaves", "fan",
    ],
    "click": ["click_wave", "click_angry", "click_shy", "click_happy",
              "click_tickle"],
}

# 按节令挑动作（月-日区间 → 动作）。只放特征最强的几个。
SEASON = [
    ((1, 1), (2, 15), "write_fu"),          # 春节前后写福字
    ((5, 1), (6, 10), "eat_zongzi"),        # 端午
    ((8, 1), (9, 20), "mooncake"),          # 中秋
    ((9, 25), (10, 10), "eat_chongyang"),   # 重阳
    ((10, 20), (11, 5), "pumpkin"),         # 万圣
    ((12, 15), (12, 31), "xmas_tree"),      # 圣诞
    ((12, 20), (1, 10), "snowman"),         # 下雪季
]

# ---------------------------------------------------------------- 台词
# 一句一断，都压在 34 字以内。不配台词的动作直接安静地做。
LINES = {
    "wallet_broke": ["见底了。这个月剩下二十天怎么过。",
                     "数完了，比我想的还少。",
                     "别看了，看也不会变多。"],
    "wallet_count": ["再数一遍，还是这个数。",
                     "数钱这事，越数越清醒。",
                     "少的那笔，你想不起来就没了。"],
    "wallet_empty": ["空的。上个月也是这样。",
                     "袋子是空的，这事得记下来。"],
    "wallet_coins": ["响了。听着多，其实不多。",
                     "有几块是有几块，别当一万。"],
    "wallet_normal": ["还行，没超。今天算安全。",
                      "这个数能站住，先别动它。"],
    "wallet_full": ["满了。这种感觉一年没几次。",
                    "钱在的时候别乱花，这话我说过。"],
    "redpacket": ["收了就得记一笔，不然算不清。",
                  "红包也是钱，别当白得的。"],
    "eat_token": ["又在烧 token。烧的是钱。",
                  "这个也记账，别以为不算。"],
    "coding": ["又改。改完还得再改。",
               "这行不对。但不归我管。",
               "写完了没人看，看了又要改。"],
    "note_taking": ["记下来。不记就是没发生。",
                    "今天的账，今天记完。",
                    "写清楚点，明天你就忘了。"],
    "work_busy": ["一件接一件。停不下来。",
                  "手上的还没完，新的又来了。"],
    "work_think": ["这事得想清楚再动。",
                   "想不明白就先别做。"],
    "work_archive": ["归档。该收的收掉。",
                     "东西堆着不清，迟早出问题。"],
    "work_pace": ["等你半天了。",
                  "左右晃也没用，你得给个话。"],
    "work_cheer": ["成了。就这一次。", "办完了。这次没返工。"],
    "work_sigh": ["干不完。反正也干不完。",
                  "累。这个不用解释。"],
    "tail_slap": ["拍一下地，不代表我生气。",
                  "别催。催也一样慢。"],
    "game_rage": ["不玩了。这游戏有毛病。",
                  "输了就是输了，别再开一局。"],
    "tap_desk": ["敲两下，提醒你还在。",
                 "我在。你倒是说话。"],
    "think_mumble": ["想了半天，想到一半又绕回去。",
                     "这事没那么简单，别急着下结论。"],
    "mumble_daze": ["发呆也算干活。别打断。",
                    "脑子空一会儿，比瞎忙强。"],
    "mumble_screen": ["看这么久，你到底在找什么。",
                      "屏幕又不会回答你。"],
    "mumble_wiping": ["擦干净了心里也乱。",
                      "桌面干净解决不了问题。"],
    "nap": ["眯一会儿，别喊我。",
            "五分钟。就五分钟。"],
    "yawn": ["困。这个点本来就该困。",
             "打个哈欠，继续。"],
    "stretch": ["伸一下就舒服了。",
                "坐太久，骨头都硬了。"],
    "peek": ["随便看看。没别的意思。",
             "东张西望也是待着。"],
    "hum": ["哼两句。不妨碍你。"],
    "mirror": ["看看有没有变。好像没有。"],
    "cat": ["它比你会来事。",
            "摸两下就走，跟你一样。"],
    "dance_maid": ["跳一段。反正没别人。",
                   "女仆的活不止倒茶。"],
    "dance_cute": ["别扭。但跳完了。",
                   "可爱这事我不负责。"],
    "dance_sway": ["晃两下，脑子能清醒点。"],
    "spin": ["转一圈，看看这边有没有事。",
             "原地转也是转。"],
    "curtsey": ["礼数是礼数，态度是态度。",
                "鞠个躬不代表我服。"],
    "violin": ["拉两下。别指望好听。"],
    "flute": ["吹一段。气不够长。"],
    "juggle_balls": ["三件事一起抛，哪件都不敢落地。",
                     "手上这么多，掉一个就全乱。"],
    "eat_hotpot": ["火锅这事，得有人在才叫吃。",
                   "涮到第三盘就不看菜单了。"],
    "eat_watermelon": ["夏天就该吃这个。"],
    "snack_caught": ["被抓到了。就一口。",
                     "吃一口怎么了，又没动你的账。"],
    "snack_big": ["先吃。账回头记。",
                  "饿的时候想不了事。"],
    "what_to_eat": ["吃什么。每次都问，每次都答不上。"],
    "eat_breakfast": ["早饭得吃，这句我说过多少遍。"],
    "eat_dinner": ["一天就这顿最像样。"],
    "whale": ["我本来就是鲸。别装没见过。"],
    "bubbles": ["吐个泡泡。不代表我高兴。"],
    "animals": ["热闹一下。热闹完还是我一个。"],
    "butterfly": ["好看的东西，看两秒就够。"],
    "crab_walk": ["横着走怎么了。能到就行。"],
    "magic_card": ["变的不是牌，是你没看清。"],
    "magic_flower": ["凭空出来一朵。就当是真的。"],
    "pigeon": ["飞了。找不回来了。"],
    "ghost": ["这个样子，跟你熬夜的时候差不多。"],
    "fireworks": ["放一下。一年也没几天能放。"],
    "mooncake": ["月饼太甜。但今天得吃。"],
    "snowman": ["堆一个。明天就化了。"],
    "xmas_tree": ["弄好了。看着挺像那么回事。"],
    "gift": ["拆了。里面是什么不重要。"],
    "write_fu": ["写一个。字不算好，意思到了。"],
    "lion_dance": ["热闹归热闹，该干的还得干。"],
    "kite": ["飞起来了。线得拽住。"],
    "swing": ["荡两下，回到原来的位置。"],
    "hobbyhorse": ["骑一会儿。假装在赶路。"],
    "shuttlecock": ["踢几个。这点运动量也够呛。"],
    "top": ["转起来就不倒了。停了就倒。"],
    "balloon": ["吹大了就得松手。"],
    "watergun": ["滋两下，图个凉快。"],
    "rubik": ["转不出来就拆。这招一直有用。"],
    "gomoku": ["想赢我？先看三步。"],
    "toy_car": ["推着玩。小时候就干这个。"],
    "run": ["跑两步。也就两步。"],
    "run_side": ["跑一趟，又把事拖到最后一刻。",
                 "急成这样，怪谁。"],
    "trot_happy": ["今天没什么坏事，先跑两步。",
                   "腿自己想动，拦不住。"],
    "crouch": ["蹲一下，缓一缓。"],
    "dressup": ["换一身。换个心情，账还是一样的账。"],
    "leaves": ["落一身。扫了还会落。"],
    "eat_crab": ["吃这个得下手。慢就慢点。"],
    "crab_walk": ["横着走怎么了。能到就行。"],
    "click_wave": ["在。"],
    "click_angry": ["别戳。有事说事。"],
    "click_shy": ["看着呢，别老点。"],
    "click_happy": ["行，算你识相。"],
    "click_tickle": ["痒。手拿开。"],
    "drag": ["放下。地上我自己会走。"],
    "startle": ["吓一跳。你走路带风啊。"],

    # ---- 节令食物：一年就几天，得有话说，不然跟平时没区别 ----
    "eat_dumpling": ["饺子得趁热。凉了就是面片。",
                     "包的人不说话，吃的人就懂了。"],
    "eat_tangyuan": ["圆的。图个说法而已。"],
    "eat_zongzi": ["拆的时候小心，手比嘴容易出事。"],
    "eat_niangao": ["黏。但年年得有这一口。"],
    "eat_qingtuan": ["艾草味冲。这才是它的意思。"],
    "eat_laba": ["八样东西煮一锅。凑齐了才有这口。"],
    "eat_chongyang": ["一层一层。吃到最后一层，忘了前面什么味。"],
    "eat_noodles": ["一根到底，不许咬断。规矩是这么定的。"],
    "eat_lunch": ["中午这顿，吃了就想睡。"],
    "eat_rice": ["没菜也能吃。饿的时候不挑。"],
    "eat_bowl": ["一碗就够。多了也是浪费。",
                 "饭吃完了，碗还是那个碗。"],
    "eat_onigiri": ["饭团最省事，捏两下就能吃。",
                    "米压紧了才不散。人也一样。"],
    "expect_meal": ["还没到点，先坐着等。",
                    "闻到了。今天有戏。"],
    "eat_tanghulu": ["酸在外头，甜在里头。反过来也一样。"],
    "eat_icecream": ["化得比吃得快。"],
    "jump_grab": ["够着了。差一点就够不着。"],
    "chrysanthemum": ["菊花开得正好。这时候该看的就是它。"],
    "fan": ["扇两下。热是扇不走的。"],
    "lantern": ["飞走了。许的愿自己记着。"],
    "river_lantern": ["飘远了就不归我管了。"],
    "needle": ["手不巧就是手不巧，别怪针。"],
    "pumpkin": ["灯是我刻的。糖是别人给的。"],
    "eat": ["吃就吃，别边说边漏。",
            "这顿吃完，再说下顿的事。"],
    "meal": ["开饭。别一边吃一边盯着屏幕。",
             "饭凉了。先吃，剩下的事等会儿。"],
    "sneak": ["被看见了。那就一起吃。",
              "偷偷吃一口，也算吃过了。"],
    "walk": ["走两步。坐太久，腰先不答应。",
             "挪一挪，别一直钉在椅子上。"],
    "jump": ["跳一下，脑子能清醒点。",
             "活动活动。就这一下。"],
}

# 没配台词的动作安静地做，这是刻意的：一直说话就成噪音了。

_GROUP_OF = {}
for _g, _items in GROUPS.items():
    for _n in _items:
        _GROUP_OF[_n] = _g


def group_of(name):
    return _GROUP_OF.get(name)


def line_for(name, prob=1.0):
    """取这个动作的台词。prob 控制说不说——默认说，调用方自己降。

    出口过人格守卫：越界词命中就换一条；整池都不合格就闭嘴，宁缺毋滥。
    """
    bank = LINES.get(name)
    if not bank or random.random() > prob:
        return None
    pool = _guarded(bank)
    if not pool:
        return None
    return random.choice(pool)


def season_anim(month, day):
    for (m0, d0), (m1, d1), name in SEASON:
        if (m0, d0) <= (m1, d1):
            if (m0, d0) <= (month, day) <= (m1, d1):
                return name
        else:                                   # 跨年区间
            if (month, day) >= (m0, d0) or (month, day) <= (m1, d1):
                return name
    return None


def phase_of(hour):
    """一天里的时段。桌宠的行为密度和动作类型都按这个走。"""
    if 0 <= hour < 6:
        return "deep_night"
    if 6 <= hour < 9:
        return "morning"
    if 9 <= hour < 12:
        return "work_am"
    if 12 <= hour < 14:
        return "lunch"
    if 14 <= hour < 18:
        return "work_pm"
    if 18 <= hour < 21:
        return "dinner"
    return "night"


# 各时段偏向哪一类动作。权重是「组」的，不是单个动作的：
# 先按权重选组，再在组内均匀挑，这样加素材不用改权重表。
PHASE_WEIGHTS = {
    "deep_night": {"rest": 60, "impatient": 12, "grind": 16,
                   "whale": 6, "money": 4},
    "morning": {"rest": 34, "eat": 26, "grind": 22, "play": 10,
                "money": 6},
    "work_am": {"grind": 34, "money": 20, "rest": 22, "impatient": 12,
                "play": 6, "proud": 4},
    "lunch": {"eat": 46, "rest": 24, "play": 14, "money": 10},
    "work_pm": {"grind": 32, "money": 22, "rest": 20, "impatient": 14,
                "proud": 6, "play": 4},
    "dinner": {"eat": 42, "rest": 26, "play": 14, "money": 12},
    "night": {"rest": 34, "proud": 14, "whale": 12, "money": 14,
              "play": 10, "magic": 8},
}
DEFAULT_WEIGHTS = {"rest": 40, "play": 20, "money": 20, "grind": 20}


# 关掉「按时段加权」时用这套：九类平均铺开，几点都一样。
# 留着 rest 略高一点——一条鱼不管几点，多数时候还是在发呆。
FLAT_WEIGHTS = {"rest": 24, "eat": 20, "play": 20, "grind": 20,
                "money": 16, "proud": 14, "impatient": 12,
                "whale": 10, "magic": 10}


def weights_for(hour, tired=False, time_of_day=True):
    w = dict(FLAT_WEIGHTS if not time_of_day
             else PHASE_WEIGHTS.get(phase_of(hour), DEFAULT_WEIGHTS))
    if tired:
        w["rest"] = w.get("rest", 0) + 40
    return w


def pick_idle(hour, month, day, available, minutes_idle=0.0, tired=False):
    """待机时挑一个动作。返回 (动作名, 权重说明) 或 (None, None)。

    权重不是均匀的：深夜不跳宅舞，饭点不写代码，久无人理就催。
    """
    ph = phase_of(hour)

    # 久无人理 → 越来越想引起注意
    if minutes_idle > 25 and random.random() < 0.55:
        pool = ["tap_desk", "work_pace", "mumble_screen", "tail_slap",
                "peek", "float_step"]
        return _pick(pool, available), "lonely"

    # 时令动作：一天只给一次机会，命中就演
    if random.random() < 0.05:
        s = season_anim(month, day)
        if s and s in available:
            return s, "season"

    weights = weights_for(hour, tired)

    groups = [g for g in weights if any(n in available for n in GROUPS[g])]
    if not groups:
        return None, None
    g = random.choices(groups, weights=[weights[g] for g in groups], k=1)[0]
    cand = [n for n in GROUPS[g] if n in available]
    if not cand:
        return None, None
    return random.choice(cand), g


def _pick(pool, available):
    cand = [n for n in pool if n in available]
    return random.choice(cand) if cand else None


def pick_click(available, hour):
    """被点的时候怎么回应。深夜被点更不耐烦。"""
    if phase_of(hour) == "deep_night":
        w = [("click_angry", 40), ("click_shy", 30), ("click_wave", 20),
             ("click_tickle", 10)]
    else:
        w = [("click_wave", 32), ("click_happy", 26), ("click_angry", 20),
             ("click_shy", 14), ("click_tickle", 8)]
    cand = [(n, p) for n, p in w if n in available]
    if not cand:
        return None
    return random.choices([n for n, _ in cand],
                          weights=[p for _, p in cand], k=1)[0]


# 通知/聊天的关键词 → 该配什么动作。这是「说什么话配什么动作」的入口。
KEYWORD_ACTS = [
    (("到账", "入账", "收入", "工资", "红包", "退款"), ["wallet_coins",
                                                  "wallet_full", "redpacket"]),
    (("消费", "支出", "扣款", "刷卡", "付款", "转账", "花了"), ["wallet_count",
                                                        "wallet_broke",
                                                        "note_taking"]),
    (("余额", "额度", "账单", "还款", "逾期"), ["wallet_count", "wallet_empty",
                                           "note_taking"]),
    (("报错", "错误", "失败", "bug", "异常", "崩溃"), ["coding", "work_sigh",
                                                 "game_rage"]),
    (("完成", "成功", "通过", "搞定", "上线"), ["work_cheer", "click_happy"]),
    (("会议", "开会", "对接", "方案", "排期"), ["work_busy", "work_think",
                                          "note_taking"]),
    (("吃", "饭", "火锅", "外卖", "餐"), ["eat_hotpot", "eat_dinner",
                                     "what_to_eat"]),
    (("快递", "物流", "到货", "进货", "采购"), ["note_taking", "work_archive"]),
    (("睡", "困", "熬夜", "凌晨"), ["yawn", "nap"]),
    (("地震", "暴雨", "台风", "预警", "紧急"), ["startle", "tail_slap"]),
]


def pick_for_text(text, available):
    """照着一句话挑动作。挑不到就返回 None，不要硬凑。"""
    if not text:
        return None
    for words, acts in KEYWORD_ACTS:
        if any(w in text for w in words):
            cand = [a for a in acts if a in available]
            if cand:
                return random.choice(cand)
    return None
