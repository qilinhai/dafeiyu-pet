# 大肥鱼桌宠 🐋

一个住在 Windows 桌面上的透明宠物：会走会坐、会说话、会看家。

它不是一个「桌宠皮肤 + 几段动画」的玩具。这一版把三件事缝在了一起——**陪伴**（人格 + 记忆 + 语音）、**看家**（手机通知、系统状态、资源释放）、**干活**（给本机 AI 工具提供桥接 API）。全部本地运行，数据不出机器。

19,000 行 Python，44 个模块，114 段动作，无边框透明置顶窗口，PySide6 绘制。

![动作总览](docs/overview.png)

> 上图是 114 段动作的逐帧总览（每格一个动作的首帧），由 `assets_anim_v2/` 直接拼出。

---

## 目录

- [一、项目背景](#一项目背景)
- [二、主要功能](#二主要功能)
- [三、功能逻辑](#三功能逻辑)
- [四、实现流程](#四实现流程)
- [五、实现效果实测](#五实现效果实测)
- [六、目录结构](#六目录结构)
- [七、运行方式](#七运行方式)
- [八、打包](#八打包)
- [九、关键注意事项](#九关键注意事项)
- [十、引用出处](#十引用出处)
- [十一、协议与致谢](#十一协议与致谢)

---

## 一、项目背景

起点是社区里的 DeepSeek 二创形象「鲸鱼娘 · 大肥鱼」——一条蓝色的胖鲸鱼。上游项目 [1190fasheqi/dafeiyu-pet](https://github.com/1190fasheqi/dafeiyu-pet) 用三视图（正 / 侧 / 背）做了个能走能拖能喂的桌宠，动画素材来自 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 的 100+ 段透明 webm。

这一版是在那个基础上的**重度二创**，动机很具体：

| 原来的问题 | 这一版怎么解 |
|---|---|
| 三视图只能左右走，动作少，站着像贴纸 | 烤了 114 段 VP9-alpha 动画成逐帧 webp，10fps 播放，带呼吸缩放、转向交叉淡化、惯性加减速 |
| 只会说固定台词 | 接 DeepSeek 做「说话前先想一下」，但它改一个数字就作废整句（见 §3.5） |
| 桌宠就是桌宠，跟自己干活没关系 | 加了本地桥接 API，本机的 AI 工具能直接让它说话、报状态、查积分 |
| 电脑上挂一堆没用的进程，手动关太烦 | 做了「释放资源」：按应用归组判定、四态裁决、不误杀 |
| 手机的通知全在手机里，人不在跟前就漏了 | adb 抓通知，按规则过滤后念出来；微信 / QQ 只念「群里 @ 我」 |

一句话概括取舍：**不做花活，做「天天都会用到的那几下」**。

---

## 二、主要功能

### 2.1 桌宠本体

- **114 段动作**，10fps，画布 314×340，脚底对齐 311px；含待机、走路、吃、玩、工作、节日、钱包余额等分组
- **三种模式**：自由散步 / 跟随鼠标 / 原地待着
- **交互**：左键拖拽（侧身朝拖动方向）、单击蹦跳回嘴、双击弹出快捷面板、右键完整菜单、托盘菜单同款
- **外观**：大小三档、亮度、地面阴影、屏幕边缘探头、鼠标穿透、窗口置顶、开机自启
- **拖文件到鱼身上**：停够 500ms 吃掉（进回收站），可自动动嘴而不必松手
- **分层降帧**：人在电脑前 50fps；人离开十分钟后降到 10fps；窗口隐藏降到 2.5fps——画面上没人看的时候不烧电

### 2.2 双击快捷面板（七个入口）

`播放日报` · `抖音看板` · `喂食` · `表演个节目` · `打开悠悠` · `释放资源` · `静音`

双击是「我要对它做点什么」最快的入口，所以日常高频动作放这里，比翻右键菜单少点两下。

### 2.3 设置面板（六个页签）

`人设` · `运行` · `报时` · `通知` · `资源释放` · `移机`

规矩是：**凡是用户可能想自己调的值，都做成控件，不写死在代码里**。阈值、开关、词表、音色、时段、白名单全在面板里，保存即生效（按文件 mtime 热加载，不用重启）。

### 2.4 手机联动

- **通知播报**：`adb` 每 2 秒抓一次手机通知，按规则判定要不要念
- **过滤规则**（面板可改）：微信 / QQ 只念「群里有人 @ 我」；图片、视频、语音、文件、红包、通话一律不念；纯噪音包（系统 UI、下载器、桌面）直接挡；营销标题挡掉；紧急词（地震 / 停电 / 台风…）永远放行
- **手机控制**：无线连接（mDNS 自动发现 + 缓存地址兜底）、断开、启动 / 停止监听、看监听日志、开机自动连
- **反向推送**：电脑上的事可以推到手机通知栏；还能唤起手机上的 YOYO

### 2.5 资源释放（一键腾内存）

- 双击面板一键触发；按**应用**归组（一个应用挂着几十个进程也只显示一行）
- **四态裁决**：`保护` / `只关窗` / `可清理` / `忽略`
- 先发 `WM_CLOSE` 关窗，等 0.9 秒复查——窗口真关了才 `terminate`，赖着不走的记 `stuck` 不硬杀
- 清理后 2.2 秒复查：**自动重启**的进程会被识别出来，自动改判为「保护」，下次不碰
- 白名单 + 核心进程锁（`protect_core`）+ 系统进程识别
- 面板 7 列：应用 / 窗口标题 / 内存 / 进程数 / 类别 / 状态 / 这一趟会怎么处理；支持「只看我装的 / 只看系统」筛选

### 2.6 整点报时

- 时段可设（起止都算，支持跨午夜；起止相同 = 全天）
- 六段随机台词，`{time}` 占位替换成中文时间说法
- 可选只弹气泡或同时念出来；睡着 / 拖动时不打扰

### 2.7 语音

- **Breeze TTS**（`api.breeze.blue`）：多音色、可调语速音量，同一句话同音色只请求一次（本地缓存）
- 不该出人声的时候用「叮」代替（`ding.py`，5 个提示音模板）
- **一键静音**：念诵和提示音全关，气泡照旧；静音时快捷按钮实心化，一眼能看见状态

### 2.8 人格与记忆

- `persona.json` 定「怎么说」：6 条性格，每条都有 `trait`（是什么）+ `guard`（不许怎么演）+ `bound`（越界词，命中整句作废）
- `pet_memory.json` 存「记得什么」：分层记忆 + 去重 + 备份 + 文件锁
- `say_ai.py` 在说话之前先想一句，但有三条硬底线（见 §3.5）
- `pet_growth.py` / `pet_hunch.py`：成长值与「推测感知」——它会自己判断现在有没有话想说

### 2.9 本地桥接 API

只监听 `127.0.0.1`，带 token 鉴权（`secrets.token_urlsafe(32)`，存 `%LOCALAPPDATA%\DaFeiYuBridge`）：

| 接口 | 作用 |
|---|---|
| `GET /health` | 存活探针（启动器靠它判「是不是已经在跑」） |
| `GET /status` | 当前模式、位置、状态 |
| `POST /say` | 让它说一句话（可指定念或不念） |
| `POST /tts` | 只出声不弹气泡 |
| `GET /credits` | 积分 / 额度查询 |
| `GET/POST /memory` | 读写记忆 |
| `POST /mode` | 切模式 |

### 2.10 抖音收藏归档

收藏夹链接丢进来 → 抓文案 → 存归档 → 新收藏冒泡播报 → 每天中午出一份日报。

---

## 三、功能逻辑

### 3.1 渲染：一帧里发生什么

窗口是 `QWidget` + `WA_TranslucentBackground` + 无边框 + 置顶。每一帧（50fps）依次做：

1. 取当前动作序列帧（`anim_lib` 从 `assets_anim_v2/<动作>/fNNN.webp` 顺序读）
2. 叠呼吸缩放（按帧号连续算，不是按时间取整——降帧时才不抖）
3. 叠位移惯性（加减速，不是匀速平移）
4. 转向时对新旧朝向做交叉淡化
5. 画气泡 / 地面阴影 / 亮度
6. 只在需要时 `update()`，静止画面不重绘

**为什么不用 `QMediaPlayer` 直接播 webm**：实测 Qt 解 VP9 出来是 NV12，alpha 全 255——透明通道被丢掉，桌宠会变成一个黑底方块。烤成逐帧 webp 是唯一稳的路。

### 3.2 动作调度

`anim_config.json` 控制频率与配额：`density`（crazy / normal / quiet）、`fidget_gap`（小动作间隔）、`act_gap`（正式表演间隔）、`cooldown`、`lonely_minutes`（多久没人理就自己找事做）、`time_of_day`（按时段挑动作）、`group_scale`（各类动作权重）、`banned` / `only`（禁用 / 只允许某些动作）。

睡觉、拖动、聊天中、静音——这些状态会压制对应类型（不打扰优先）。

### 3.3 通知过滤：判定顺序是设计出来的，不是凑出来的

```
is_important(pkg, title, body)
  ├─ 1. 聊天类先判：是微信 / QQ 吗？
  │      ├─ 不是「群里 @ 我」 → 直接不念（紧急词也救不回来）
  │      └─ 是 @ 我，但带媒体（图 / 视频 / 通话）→ 默认不念
  ├─ 2. 紧急词命中 → 念（这是唯一能越过「噪音包」的规则）
  ├─ 3. 噪音包（系统 UI / 下载器 / 桌面 / 云服务）→ 不念
  ├─ 4. 营销标题（"限时" "领取" "中奖"…）→ 不念
  └─ 5. 其余 → 念
```

**聊天判断排在紧急词前面**，是这一版被用户返工过一次的地方：把它放在后面，微信私聊里只要出现「停电」就会被念出来，而用户要的是「聊天类彻底不念，只留群里 @」。

已知边界：群主「@所有人」和群公告，通知文案同样是 `[有人@我]`，离线分辨不出来，会念。

### 3.4 资源释放：四层引擎

```
① 探测   psutil 拿 pid / name / ppid / rss / exe / user
          Win32 拿窗口标题（EnumWindows + GetWindowThreadProcessId）
② 归组   product_dir(exe) 往上走，跳过 bin / app / v1.0.0.23 / win32-x64 这类容器层
          再沿父进程链做并查集（union(父, 子) —— 反了会让子目录当组长）
          运行时目录（venv / node / usr / edgewebview）不许认亲，否则会把两只不相干的程序并成一只
③ 裁决   保护 protect / 只关窗 close / 可清理 clean / 忽略 ignore
          + core_locked（核心进程任何设置都动不了）+ system（系统进程默认不列）
④ 执行   关窗 WM_CLOSE → sleep 0.9 → 复查 → 窗口真关了才 terminate
          2.2 秒后复查：自动重启的进程 → 记 auto_restart 并自动改成「保护」
```

判定为 `clean` 的组，**先关窗再结束**——早期版本先看「有没有窗口」，结果挂着一堆没有可见窗口的进程永远清不掉。

### 3.5 说话：三条硬底线

`say_ai.py` 把「事实句」变成「一句带看法的话」，但：

1. **数字不许错**：模型输出里的数字必须能在原文里找到，否则整句作废、退回模板。宁可没看法，不能有错话。
2. **紧急只讲事实**：地震预警这类不调模型，直接念，不花那 2 秒。
3. **不花没必要的钱**：同类 45 秒只调一次、同内容 1 小时复用、每小时 / 每天 / 每天花费都有上限。超了退回模板，播报不哑。

没 key、断网、模型抽风一律返回空串，由调用方退回原模板——这一层永远不许抛异常。

### 3.6 语音链路

```
要说话 → 静音？→ 是：只弹气泡
                 └ 否：有 TTS key？
                        ├ 有 → 本地缓存命中？→ 播缓存
                        │                     └ 未命中 → 请求 → 写缓存 → 播
                        └ 无 → 播「叮」提示音
```

---

## 四、实现流程

```
                    ┌──────────────── pet.py（主循环 50fps）────────────────┐
                    │  动作调度 anim_lib/anim_manifest ← anim_config.json   │
   assets_anim_v2 ──┤  绘制：帧 → 呼吸 → 惯性 → 淡化 → 气泡                │
   sprites/         │  交互：单击/双击/拖拽/右键/托盘                       │
                    └───────┬───────────────────────┬──────────────────────┘
                            │                       │
             ┌──────────────▼──────────┐   ┌────────▼─────────────┐
             │ settings_panel（6 页签）│   │ bridge_server        │
             │ 写各 json → mtime 热加载│   │ 127.0.0.1 + token    │
             └──────────────┬──────────┘   └────────┬─────────────┘
                            │                       │
   ┌────────────────────────▼───────────────────────▼──────────────────────┐
   │ 规则与数据层                                                          │
   │  persona.json（怎么说）  pet_memory.json（记得什么）  ai_talk.json     │
   │  notify_config.json（念什么）  cleaner_config.json（关什么）           │
   └───────┬──────────────────┬──────────────────┬─────────────────────────┘
           │                  │                  │
   ┌───────▼──────┐  ┌────────▼───────┐  ┌───────▼────────┐
   │ phone_notify │  │ cleaner        │  │ say_ai / tts   │
   │ (独立进程)   │  │ (按需调用)     │  │ (说话前想一下) │
   │ adb → 过滤   │  │ 归组 → 裁决    │  │ DeepSeek/Breeze│
   └──────────────┘  └────────────────┘  └────────────────┘
```

**一次「群里有人 @ 我」的完整路径**：

1. `phone_notify.py` 每 2 秒 `adb shell dumpsys notification --noredact`
2. 解析出 pkg / title / text，`is_important()` 判定 → 是 @ 我
3. 拼成「群名：内容」→ 走 `say_ai` 决定要不要加一句看法（受预算限制）
4. 通过 `bridge_server` 的 `/say` 让桌宠开口：气泡 + 语音
5. 落一行日志（`phone_notify.log`），方便事后核对判定对不对

---

## 五、实现效果（实测）

**环境**：Windows LTSC，i5-8300H / 32GB / GTX 960 2GB @ 2560×1440@100Hz

| 项目 | 实测数据 |
|---|---|
| 进程数 / 内存（豆包） | 24–26 进程 / 1733–1924 MB |
| 进程数 / 内存（微信） | 15 进程 / 636 MB（Weixin×5 + WeChatAppEx×9 + crashpad×1） |
| 一次「释放资源」可腾出 | 约 6.1 GB（quark 1853 + edge 886 + 百度网盘 801 + 壁纸 766 + office6 628 + cef 473 + escrcpy 395 + 火绒商店 292 + idm 61，单位 MB） |
| 空闲内存 | 18.2–21.3 GB / 32 GB |
| 桌宠本体开销 | 给 dwm 增加约 0.43% CPU；空闲时 DPC 0–0.39% |
| 通知抓取间隔 | 2 秒 |
| 自检结果 | 33/33 模块导入通过；真启动桥接健康检查 `{ok: true}`，存活 6 秒无异常 |

**判定准确性**（这一版专门修过的三个坑）：

1. 360 DesktopLite 自带一个 `notepad.exe`——按名字匹配会误关 360 的窗口、杀掉 2 个进程。改成按「刚启动的 PID 反查所属组」。
2. 豆包被叫成 `aha_doctor`——并查集方向写反，子目录当了组长。改成 `union(父, 子)`。
3. 在终端里跑的 python 把桌宠的 venv 并进了自己——加了 `RUNTIME_DIRS`，运行时目录不许认亲。

---

## 六、目录结构

```
dafeiyu-pet/
├── pet.py                    主程序：窗口、绘制、交互、菜单、调度（4,529 行）
├── settings_panel.py         设置面板：人设/运行/报时/通知/资源释放/移机（2,126 行）
├── wb_menu.py                自绘右键菜单（QMenu 贴边会折叠压住父级，见模块头注释）
├── anim_lib.py               序列帧读取与缓存
├── anim_manifest.py          动作清单、分组、时段映射
├── anim_config.py            动作节奏读写（anim_config.json）
├── pet_acts.py               动作 / 表演调度
├── pet_memory.py             分层记忆（去重、备份、加锁）
├── pet_growth.py             成长值
├── pet_hunch.py              「推测感知」：现在有没有话想说
├── say_ai.py                 说话前想一下（DeepSeek，含三条硬底线）
├── voice_lexicon.py          念法词典（数字、金额、缩写怎么念）
├── voice_style.py            念法风格
├── tts_breeze.py             Breeze TTS 客户端（缓存、限流）
├── notify_voice.py           提示音与人声的选择
├── ding.py                   「叮」提示音（5 个模板）
├── chime.py                  整点报时
├── cleaner.py                「释放资源」四层引擎
├── awareness.py              系统状态感知（CPU/内存/GPU，Win32 调用）
├── phone_notify.py           手机通知监听（独立进程，端口 8795 单实例锁）
├── phone_link.py             无线 adb 连接（mDNS 发现 + 缓存兜底）
├── phone_panel.py            手机控制面板后端
├── phone_connect_dialog.py   连接手机对话框
├── bridge_server.py          本地桥接 API（127.0.0.1 + token）
├── memory_panel.py           记忆查看 / 编辑
├── file_feeding.py           拖文件喂食（回收站）
├── move_stride.py            走路步幅
├── board_link.py             抖音看板联动
├── douyin_archive.py         抖音收藏归档
├── douyin_take.py            单条收藏的摘要
├── douyin_panel.py           抖音看板面板
├── zhengzhou_today.py        「今日」播报（天气 + 日期）
├── credits_guard.py          积分守门人（查额度、提醒）
├── launcher.py               启动器（幂等：已在跑就直接报告）
├── notify_launcher.py        通知监听启动器（幂等）
├── notify_stop.py            停监听
├── spawn_notify.py           彻底脱离进程树拉起监听（沙箱环境用）
├── bake_anim.py              webm → 逐帧 webp 烘焙（开发用）
├── preprocess.py             三视图抠图（上游脚本）
├── preprocess2.py            边缘去污 + 多尺寸精灵（上游脚本）
├── fetch_assets.py           从上游拉动画素材（开发用）
├── check_sheet.py / sheet_all.py / make_zip.py   素材 / 打包小工具
├── hooks/
│   ├── wb_hook.py            在本机 AI 工具的 hook 事件上挂钩（Stop / Notification…）
│   └── hook_config.json      hook 开关与话术
├── tools/                    29 个自检脚本（见 §7.4）
├── assets_anim_v2/           114 个动作 × 逐帧 webp（8,744 张，185 MB）+ meta.json
├── assets_webm/              114 段源 webm（VP9-alpha，56 MB）
├── sprites/                  三视图精灵（正/侧/背 × 多尺寸）+ icon.png
├── docs/
│   ├── overview.png          动作总览图
│   └── README.upstream.md    上游原始 README（保留出处）
├── config.example.json       配置示例（首次运行自动生成 config.json）
├── persona.json              人格设定
├── ai_talk.json              说话策略与预算
├── notify_config.json        通知过滤规则
├── cleaner_config.json       应用分组与四态
├── anim_config.json          动作节奏
├── sound_config.json         提示音
├── voice_lexicon.json        念法词典
├── move_strides.json         步幅
├── douyin_config.json        抖音归档配置
├── requirements.txt          运行依赖
├── requirements-dev.txt      开发 / 烘焙 / 打包依赖
├── dafeiyu-pet.spec          PyInstaller 打包配置
├── setup.cmd                 一键建 venv + 装依赖
├── start-pet.cmd             启动桌宠（幂等）
├── start-notify.cmd          启动通知监听（幂等）
├── stop-notify.cmd           停止通知监听
└── LICENSE                   MIT
```

---

## 七、运行方式

### 7.1 环境

- Windows 10 / 11（用到 Win32 API：`WM_CLOSE`、`EnumWindows`、注册表自启）
- Python **3.11+**
- 可选：NVIDIA 显卡 + `nvidia-ml-py`（读显卡温度）；Escrcpy / platform-tools（手机联动）

### 7.2 三步跑起来

> 只想用、不想装 Python：跳到 [§8.1 直接下载](#81-直接下载不想装-python)。

```bat
:: 1. 建虚拟环境并装依赖（只需一次）
setup.cmd

:: 2. 启动（重复双击也不会起第二只）
start-pet.cmd
```

或者手动：

```bash
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe pet.py
```

第一次运行会在程序目录生成 `config.json`。想改默认值，把 `config.example.json` 复制成 `config.json` 再改。

### 7.3 可选配置

| 想做 | 怎么办 |
|---|---|
| 让它出声 | 右键 → 语音 → 设置 Breeze Key（`api.breeze.blue`）；不填就只弹气泡 + 叮 |
| 让它说话有看法 | 把 DeepSeek Key 写进 `config.json` 的 `ds_api_key`（或 `deepseek_key.txt`） |
| 天气 | `config.json` 的 `city`（默认郑州），走 `wttr.in`，不通自动退到 Open-Meteo |
| 手机联动 | 手机开无线调试 → 右键 → 手机 → 连接（自动 mDNS 或手填 IP / 端口 / 配对码） |
| 开机自启 | 右键 → 开机自启（写 `HKCU\...\Run`） |

### 7.4 自检脚本

`tools/` 下的脚本都是「真跑一遍再看结论」，不是单测桩：

```bash
python tools/smoke_import.py       # 33 个模块逐个 import
python tools/smoke_launch.py       # 隔离环境真启动一只，验桥接后收掉
python tools/check_cleaner.py      # 归组 / 裁决逻辑
python tools/check_clean_panel.py  # 资源释放页签 21 项
python tools/check_chime.py        # 整点报时 31 项
python tools/check_menu.py         # 自绘菜单
```

---

## 八、打包

### 8.1 直接下载（不想装 Python）

到 [Releases](https://github.com/qilinhai/dafeiyu-pet/releases/latest) 下 `DaFeiYuPet-v1.0.0-win64.zip`，解压后双击 `DaFeiYuPet/DaFeiYuPet.exe` 即用。

- 体积约 235 MB（压缩后），解压约 339 MB（其中 185 MB 是 114 段动作素材）
- 已实测：解压无损坏 → 启动 → 本地桥接健康检查 `{"ok": true}` → 进程稳定存活

### 8.2 自己打包

```bash
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
venv\Scripts\python.exe -m PyInstaller --noconfirm dafeiyu-pet.spec
```

产物：`dist/DaFeiYuPet/DaFeiYuPet.exe`（onedir，含 114 段动作素材）。

`dafeiyu-pet.spec` 里有两处是踩过坑之后才加的：**不能 `collect_all("PySide6")`**（会把 WebEngine / 3D / Charts 全拖进来，多出 650 MB），**必须显式列 hiddenimports**（本地模块是运行时按需 import 的，静态分析抓不到）。改完之后 879 MB → 339 MB。

**打包后数据落在哪**（onedir 特有，注意别删）：

| 文件 | 位置 |
|---|---|
| `config.json` | exe 同级目录（可改） |
| `persona.json` / `pet_memory.json` | `_internal/`（`sys._MEIPASS`）；onedir 不会被清，记忆能跨次保留 |
| 桥接 token / 端点 | `%LOCALAPPDATA%\DaFeiYuBridge\` |

---

## 九、关键注意事项

**运行相关**

1. **必须用 `pythonw.exe`**：用 `python.exe` 会在桌面留一个常驻黑框。`pythonw` 下 `sys.stdout is None`，任何 `print` 都会 `AttributeError` 弄死进程——所以 `pet.py` 启动第一件事是把 stdout 接到 `pet_run.log`。
2. **不要重复启动**：桥接端口是单实例依据（默认 8790，占用则 8791 / 8792）。`start-pet.cmd` 走 `launcher.py`，已在跑会直接报告。
3. **通知监听是独立进程**：端口 8795 当锁，多起的会自己退；停桌宠不影响它，反之亦然。
4. **无线 adb 在 Android 17 上每次手机重启都要重新配对**——已知短板，配对失效后监听会静默不吭声，重连一次即可。
5. **`config.json` 含密钥**，已进 `.gitignore`，别提交。

**开发相关**

6. **把值写死在代码里会被返工**：阈值、开关、词表、音色、时段一律外置成 json + 面板控件，且按 mtime 热加载。
7. **面板是唯一的自救入口**：一组控件坏了只灰那一组，不许整页打不开（踩过 `module 'ding' has no attribute 'TEMPLATES'` 导致面板完全打不开）。
8. **`.cmd` 必须纯 ASCII + CRLF**：`cmd.exe` 按启动时的 OEM 代码页（中文系统 936 / GBK）解析批处理，跟文件实际编码无关——UTF-8 中文会被 GBK 咬合、吃掉换行，整段脚本撕碎。中文提示一律交给 Python 打。
9. **判断「能不能用」必须真跑**：单测全过也可能因为一个局部变量遮蔽而 `UnboundLocalError`。
10. **不要用 `importlib.reload` 热更模块**：会冲掉测试里的 mock。用按 mtime 的 `cleaner.fresh()` 这类做法。
11. **改动作素材后要重烤**：`python bake_anim.py`（`assets_webm` → `assets_anim_v2`）。烤的时候用**全局裁切框**、按 `frame.time` 抽帧——素材声明 24fps 实际 12fps，按 fps 元数据抽会错。

**授权相关**

12. **形象不可商用**：角色源自社区二创，形象协议 CC BY-NC-SA 4.0，详见下一节。

---

## 十、引用出处

按「在哪个文件 / 哪一步用到」列出。**所有第三方内容的使用位置都能在这里追到源头。**

### 10.1 代码与形象

| # | 引用位置 | 来源名称 | 链接 | 说明 |
|---|---|---|---|---|
| 1 | `LICENSE`、`sprites/`、`icon.ico`、`preprocess.py`、`preprocess2.py`、`docs/README.upstream.md` | 1190fasheqi/dafeiyu-pet | https://github.com/1190fasheqi/dafeiyu-pet | 桌宠本体原型：三视图行走、拖拽喂食、台词系统、抠图与精灵脚本。**MIT**。本仓库在其基础上大幅二创 |
| 2 | `assets_webm/`、`assets_anim_v2/`、`bake_anim.py`、`anim_manifest.py` | PC2005-cloud/dsh-pet | https://github.com/PC2005-cloud/dsh-pet | 114 段 VP9-alpha 透明动画（640×360）。dsh-pet 的**代码**为 MIT，**宠物形象**为 CC BY-NC-SA 4.0（署名 · 非商业 · 相同方式共享），其仓库要求二创在公开渠道附原作者地址 |
| 3 | `assets_anim_v2/meta.json`、`pet.py` 绘制 | 本仓库 | — | 素材经 `bake_anim.py` 重新烘焙：全局裁切框 → 10fps → 314×340 画布，脚底对齐 311px |
| 4 | `pet.py` 台词表、`persona.json` | DeepSeek 社区梗 | — | 「D指导去吃饭」「吃白饭」「才不是大肥鱼」等台词取自社区整活；上游 README 亦有说明 |
| 5 | `README.md`、`docs/overview.png` | DeepSeek V4 Pro 二创形象「鲸鱼娘 · 大肥鱼」 | — | 角色为社区二创，非官方形象；原型「溟月」出自 B 站 @上善无形，DeepSeek 版设计出自 @ZipZipPipe（见 dsh-pet README） |

### 10.2 第三方库

| # | 引用位置 | 来源名称 | 链接 | 说明 |
|---|---|---|---|---|
| 6 | 全部 GUI | PySide6 / Qt for Python | https://doc.qt.io/qtforpython-6/ | 窗口、绘制、多媒体。**LGPL**；分发二进制需遵守 LGPL（动态链接、保留版权声明） |
| 7 | `cleaner.py`、`awareness.py` | psutil | https://github.com/giampaolo/psutil | 进程枚举、内存统计。**BSD-3** |
| 8 | `say_ai.py`、`douyin_take.py`、`voice_lexicon.py`、`tts_breeze.py`、`credits_guard.py` | Requests | https://requests.readthedocs.io/ | HTTP 客户端。**Apache-2.0** |
| 9 | `pet.py`（GPU 温度） | nvidia-ml-py（import 名 `pynvml`） | https://pypi.org/project/nvidia-ml-py/ | 读 NVIDIA 显卡温度。**BSD**。缺了自动跳过 |
| 10 | `bake_anim.py` | PyAV | https://github.com/PyAV-Org/PyAV | 解 webm 的 VP9 + alpha。**BSD-3**（仅开发时用） |
| 11 | `bake_anim.py`、`preprocess2.py` | Pillow | https://python-pillow.org/ | 图像缩放、webp 写出。**MIT-CMU**（仅开发时用） |
| 12 | `bake_anim.py` | NumPy | https://numpy.org/ | alpha 并集计算。**BSD-3**（仅开发时用） |

### 10.3 外部服务与系统接口

| # | 引用位置 | 来源名称 | 链接 | 说明 |
|---|---|---|---|---|
| 13 | `say_ai.py`、`douyin_take.py`、`voice_lexicon.py` | DeepSeek API | https://platform.deepseek.com/ | `deepseek-chat`，「说话前想一下」与长文摘要。需自备 Key，按 token 计费 |
| 14 | `tts_breeze.py` | Breeze TTS | https://api.breeze.blue/v1 | 语音合成（多音色）。需自备 Key；同一句话只请求一次并缓存 |
| 15 | `pet.py`（`_weather_wttr`）、`zhengzhou_today.py` | wttr.in | https://wttr.in/ | 天气查询（`?format=j1`） |
| 16 | `pet.py`（`_weather_openmeteo`） | Open-Meteo | https://open-meteo.com/ | wttr.in 不通时的兜底天气源，免 Key |
| 17 | `phone_link.py`、`phone_notify.py`、`phone_panel.py` | Android Debug Bridge (adb) / platform-tools | https://developer.android.com/tools/adb | 无线调试、`dumpsys notification` 抓通知、反向推通知。建议通过 [Escrcpy](https://github.com/viarotel-org/escrcpy) 自带 adb 使用 |
| 18 | `awareness.py`、`cleaner.py`、`pet.py` | Win32 API（user32 / kernel32 / dwmapi） | https://learn.microsoft.com/windows/win32/api/ | `EnumWindows` 取窗口标题、`WM_CLOSE` 关窗、`SetWindowDisplayAffinity`、DPI 与置顶 |
| 19 | `chime.py`、`ding.py` | winsound（Python 标准库） | https://docs.python.org/3/library/winsound.html | 提示音播放 |
| 20 | `bridge_server.py` | `http.server` + `secrets`（标准库） | https://docs.python.org/3/library/http.server.html | 本地桥接，仅 `127.0.0.1`，token 鉴权 |
| 21 | `hooks/wb_hook.py` | WorkBuddy hook 事件 | https://www.workbuddy.cn/docs/workbuddy/Overview | 在 `Stop` / `Notification` / `UserPromptSubmit` 等事件上挂钩，让鱼说话或「只叮不念」 |
| 22 | 全局字体 | Microsoft YaHei UI | — | Windows 系统字体，未随仓库分发 |

### 10.4 数据与隐私边界

- 仓库**不含**任何密钥、令牌、个人记忆、手机配对信息、聊天日志（见 `.gitignore`）。
- `config.json`、`pet_memory.json`、`ai_talk_state.json`、`phone_link.json`、`phone_pair.json`、`*.log` 均为运行时生成，**不入库**。
- 所有网络请求只发生在本机 → 用户自己配置的服务（DeepSeek / Breeze / wttr.in / Open-Meteo）；桥接只监听 `127.0.0.1`。

---

## 十一、协议与致谢

**代码**：MIT（见 [LICENSE](LICENSE)），继承自上游 1190fasheqi/dafeiyu-pet。

**形象与动画素材**：不是 MIT。角色源自社区二创，[PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 明确标注宠物形象为 **CC BY-NC-SA 4.0**：

- **署名**：需注明原作者地址 `https://github.com/PC2005-cloud/dsh-pet`
- **非商业**：不可用于任何形式的商业用途
- **相同方式共享**：衍生作品需以相同协议发布

也就是说：**代码可以随便用，鱼不能拿去卖。** 商用请自行替换形象与动画素材（`preprocess.py` / `preprocess2.py` / `bake_anim.py` 就是换皮用的）。

**致谢**

- [1190fasheqi](https://github.com/1190fasheqi) —— 桌宠本体与三视图方案
- [Cpanoe](https://github.com/Cpanoe) —— 上游 PR#3：DeepSeek 对话、wttr.in 天气、CPU/内存/GPU 监控、PyInstaller 配置
- [PC2005-cloud](https://github.com/PC2005-cloud) —— 114 段透明动画与形象设定
- 社区整活 —— 台词里那些梗

---

*这一版是「一个人在用的工具」被整理成「别人也能跑起来」的样子。有不顺的地方，多半是它当初只为我这一台机器写的。*
