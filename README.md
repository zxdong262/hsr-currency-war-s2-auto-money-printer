# Auto Money Printer · 崩坏：星穹铁道「货币战争 S2」自动印钞机

<img src="assets/app-icon.png" width="96" align="right" alt="icon">

**中文** ｜ [English](#english)

> 🤖 本项目由 **WorkBuddy（DeepSeek V4.1 Flash）** 生成 —— 需求、代码、调试、打包和文档均由
> AI 在 WorkBuddy 里完成。
> Generated with **WorkBuddy (DeepSeek V4.1 Flash)**: code, debugging, packaging and docs.

---

## 这是什么

一个 Windows 桌面程序，自动玩《崩坏：星穹铁道》里的**「货币战争 S2」**小游戏：靠反复
**买 → 复制 → 卖**把金币刷起来。

它**不读内存、不改游戏文件、不注入 DLL、不联网**，纯粹「看屏幕 + 动鼠标」：

| 环节 | 用什么 |
| --- | --- |
| 截游戏画面 | Windows Graphics Capture（`windows-capture`） |
| 找按钮 / 卡片 / 图标 | OpenCV 模板匹配（多尺度 + ROI） |
| 读金币数字、「黑市主理人」标签 | RapidOCR（本地离线模型，无需 Tesseract） |
| 点鼠标 / 拖卡片 / 按 ESC | `SendInput`（pyautogui） |

**两个任务**

- **Task 1 进货**：打开市场 → 刷新到出现「完美投影仪」→ 买一包（9 币 / 3 个）→ 读金币 →
  再刷新检查，金币不足 9 就停。
- **Task 2 出货**：把「完美投影仪」连续拖进卡槽复制（默认最多 36 次，货用完就停）→
  在屏幕底部 20% 区域统一点击复制出来的卡 → 点「出售」（每张 +5）。

**安全设计**：任何时候按 `F7` 停止、`F8` 紧急停止；把鼠标甩到屏幕角落也会触发 failsafe 急停；
Dry run 模式只写日志不动鼠标，可以拿历史截图离线回放。

## 下载即用

1. 到 [Releases](../../releases) 下载最新的 `AutoMoneyPrinter.zip`（约 100 MB）
2. 解压到任意目录，双击 `AutoMoneyPrinter.exe`
3. 同意 UAC 提示，在游戏里按 `F6` 开始

程序自带模板和离线 OCR 模型，目标机器**不需要装 Python**。配置、日志、调试截图写在
`%APPDATA%\AutoMoneyPrinter\`。

> **为什么必须管理员权限**：`StarRail.exe` 以管理员身份运行，Windows 的 UIPI 会**静默丢弃**
> 低权限进程发往高权限窗口的 `SendInput`。不提权的话界面能开、但点击和按键根本进不了游戏。
> `--no-elevate` 可以跳过提权（只能看画面），Diagnostics 页会明确警告。

## 界面

原生 Tk 窗口，四个页签：

- **Control** —— 开始 / 停止 / 急停，当前状态与计数（买了几次、卖了几张、赚了多少）、最近事件
- **Settings** —— 快捷键、常用数值、分辨率切换（切换前弹确认框）
- **Log** —— 实时日志，一键打开 `bot.log` 与调试截图目录
- **Diagnostics** —— 权限、游戏窗口、模板路径，以及 `Probe screen` 现场识别自检

窗口顶部的 `语言` 下拉框可随时切换中文 / English，选择写入 `config.json` 的 `language`，
下次启动保持（日志和事件名始终是英文）。默认 `zh`。

## 快捷键

| 键 | 作用 |
| --- | --- |
| `F6` | 开始 |
| `F7` | 停止 |
| `F8` | 紧急停止 |
| `F9` | 开始 / 停止切换 |

都能在 Settings 页改（点 `Set...` 后直接按键，也支持 `<ctrl>+<alt>+s` 这种写法）。热键走
`RegisterHotKey` + `GetAsyncKeyState` 轮询双保险，**不依赖窗口焦点**，在游戏里直接按就行。

## 从源码运行

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

start.bat          :: 自动提权，打开原生 GUI
start_gui.bat      :: 同上，直接打开 Settings 页
```

国内网络可加 `-i https://mirrors.aliyun.com/pypi/simple/`。

命令行参数：

```bat
python main.py --settings          :: 打开 GUI 的 Settings 页
python main.py --language en       :: 临时切英文
python main.py --run --dry-run     :: 不开 GUI 跑一轮，只记日志不动鼠标
python main.py --probe "screenshot/*.png"   :: 离线验证模板命中率
python main.py --web               :: 旧网页控制台（仅源码调试用，打包版不含）
```

**分辨率**：模板、ROI、锚点、卡槽几何、出售区保护距离都按 `display.ref_height: 1440` 保存，
运行时按当前画面高度自动缩放（`1920×1080` → 0.75），所以 1440p / 1080p 共用一套配置。
Settings 页的分辨率按钮会先弹确认框，确认后才调用 `ChangeDisplaySettingsW`（只切主显示器、
不写注册表）。

## 配置文件

`config.json` 是随程序发布的默认配置，打包版首次运行会复制到 `%APPDATA%\AutoMoneyPrinter\`。
常用项：

| 键 | 说明 |
| --- | --- |
| `dry_run` | `true` 只记录动作、不动鼠标 |
| `capture.backend` | `wgc` / `mss` / `auto`，默认 `auto` |
| `capture.window` | 游戏窗口标题，用于 WGC 按窗口抓图 |
| `hotkeys.*` | 四个热键 |
| `match.use_roi` | `false` 时全屏搜索，换了分辨率或窗口位置可以关掉 |
| `behavior.sell_zone.bottom_fraction` | 只允许在屏幕底部这个比例内点击/出售复制卡（默认 0.2） |
| `economy.starting_money` | OCR 完全读不到金币时的兜底起始值 |

## 打包

```bat
build.bat
```

产出 `dist/AutoMoneyPrinter/` 与 `dist/AutoMoneyPrinter.zip`（约 98 MB）。PyInstaller onedir
模式，`console=False`，图标来自 `assets/app-icon.ico`。

## 实现要点（踩过的坑）

- **截图必须用 WGC**，不能用 mss。游戏是全屏 flip-model 渲染，GDI `BitBlt`（mss 那套）抓到的是
  游戏启动前的桌面残影，于是会出现「游戏明明在屏幕上，却报 market button not visible」。
- **模板是从 4480×1440 桌面截图 1:1 裁的**，所以每个模板在 `config.json` 里带 `roi`。换布局后把
  `match.use_roi` 设 `false`，或把 `roi` 改成 `null`。
- **会变的数字只当锚点**：`target-to-drag.png` 右下角的数量角标只裁上半部 `[0,0,103,58]`；
  `money-count.png` 只拿左侧金币当锚点，右边数字交给 OCR。商店弹窗打开时金币会被重绘（模板分
  只有 ~0.6），锚点找不到就改用 `money.fallback_roi_rel`（右下角）直接 OCR，并加亮度校验挡掉
  「卡片详情弹窗盖住右下角」；彻底读不到才退化成账本记账。
- **打开商店会「开了又立刻关」**：入口图标在会滚动的列表里，底色一变模板就失效，所以改成
  **OCR 读「黑市主理人」标签 + 点标签右侧固定偏移的图标**（模板只作兜底）。另外普通点击是
  down + up 两次事件，弹窗在 down 时已经盖住屏幕，up 落在遮罩上就等于点关闭 —— 所以
  `behavior.market_open.styles` 依次尝试 `away`（按下后指针上移 260 px 再松开）/ `hold`（按住
  160 ms）/ `click`，每次用「刷新按钮是否出现」验证，失败换下一种并记 `OPEN MARKET RETRY`。
- **卡槽模板只裁卡面中间**（`crop [20,40,130,100]`）：边框、右上角星级/装备角标、底部金条在
  卡被装饰后会变，整卡匹配掉到 0.80 以下；裁中间后同一张卡在 0.86–1.0 八个尺度上稳定 0.86+，
  其他角色最高只有 0.49，所以阈值能放到 0.80。
- **卖出区域硬保护**：复制卡只允许在屏幕底部 20% 内点击/出售。卡槽在屏幕中部，低阈值重扫会以
  ~0.74 误命中它，所以除了 ROI 还有几何校验（中心 y ≥ `min_y`、不能靠近已知卡槽），被拒绝的
  命中会写 `SELL ZONE REJECT`，探测图上画红线标出边界。即使 `use_roi: false` 这道保护依然生效。

## 项目结构

```
main.py             入口：GUI / --run / --probe / --web，负责提权
app/
  bot.py            状态机：两个任务的主循环
  actions.py        鼠标/键盘动作（含市场开启重试策略）
  vision.py         截图、模板匹配、多尺度、ROI
  gui.py            Tk 界面四个页签
  i18n.py           中英文案
  hotkeys.py        全局热键
  config.py         配置读写与默认值合并
  runtime.py        提权、路径解析（源码 / 打包两种形态）
  display.py        分辨率切换与缩放
  window.py         窗口枚举、前台切换、权限检测
  logbus.py         结构化日志 + 事件总线
  web.py            旧网页控制台（源码调试用）
src/pngs/           模板图片
assets/             应用图标与角色源图
tests/              离线状态机测试（需要本地 screenshot/）
docs/               设计笔记
```

## 常见问题

- `no game UI on screen (best match ...)` —— 游戏不在前台或抓错了画面。点界面 `Focus game`，
  或在游戏里按 `F6`；仍失败就看 `--probe` 的分数和 `capture.backend`。
- `the game runs as administrator but this app does not` —— 用 `start.bat` 重开并同意 UAC。
- `'refresh' not found ... best score 0.4` —— 市场没打开、画面还在加载，或 ROI 与新分辨率不匹配。
- 换机器 / 分辨率后先跑 `python main.py --probe "screenshot/*.png"`，命中分数应该 ~0.99。
- 仓库里**没有**放运行截图和日志（`.gitignore` 已排除 `screenshot/`、`logs/`、`dist/`、
  `build/`），所以 `tests/test_offline.py` 需要你自己放几张游戏截图到 `screenshot/` 才能跑。

## 致谢

- 应用图标来自开源社区项目 [deepseek-whale-girl](https://github.com/TreapGoGo/deepseek-whale-girl)
  （MIT）的 `characters/brand/whale-girl-logo-master.png` —— 也就是网友口中的「蓝色大肥鱼」鲸鱼娘，
  套一层浅蓝圆角底后生成 `assets/app-icon.ico`，源图见 `assets/whale-girl.png`。角色版权归原作者
  与社区所有，本项目与其无隶属关系，仅作图形素材使用。
- OCR 用 [RapidOCR](https://github.com/RapidAI/RapidOCR)，截图用
  [windows-capture](https://github.com/NiiightmareXD/windows-capture)。
- 代码与文档由 **WorkBuddy（DeepSeek V4.1 Flash）** 生成。

## 免责声明

仅供**学习与自动化技术研究**。修改游戏客户端、模拟输入可能违反游戏用户协议，存在账号风险；
请自行承担使用后果，不要用于商业用途或线上环境。本项目与米哈游 / HoYoverse 无关。

---

## English

**What it is** — a Windows desktop bot that plays the *Honkai: Star Rail* "Currency War S2"
minigame for you, farming coins with a **buy → duplicate → sell** loop. It does **not** read
memory, patch files, inject DLLs or talk to the network: it only looks at the screen and moves
the mouse.

| Step | Tech |
| --- | --- |
| Capture the game | Windows Graphics Capture (`windows-capture`) |
| Find buttons / cards / icons | OpenCV template matching (multi-scale + ROI) |
| Read the coin counter and the `黑市主理人` label | RapidOCR (offline models, no Tesseract needed) |
| Click, drag, press ESC | `SendInput` via pyautogui |

**Task 1 (buy)** — open the market, refresh until `完美投影仪` shows up, buy a 9-coin / 3-unit
pack, re-read the balance, refresh again; stop when the balance drops below 9.
**Task 2 (sell)** — drag the projector onto the card slot up to 36 times, then click the
duplicated cards in the bottom 20% strip of the screen and hit `出售` (+5 each).

**Safety** — `F7` stops, `F8` is a panic stop, and slamming the mouse into a screen corner
triggers the pyautogui failsafe. Dry-run mode logs every action without touching the mouse, so
you can replay historical screenshots offline.

### Download

Grab `AutoMoneyPrinter.zip` from [Releases](../../releases) (~100 MB), unzip anywhere and run
`AutoMoneyPrinter.exe`. Accept the UAC prompt - **elevation is mandatory**: `StarRail.exe` runs
as administrator and Windows UIPI silently drops synthetic input sent from a lower-integrity
process, so an unelevated copy can see the game but never click it. Python is **not** required on
the target machine; config, logs and debug frames live in `%APPDATA%\AutoMoneyPrinter\`.

### GUI

Four tabs: **Control** (start / stop / panic, live counters, recent events), **Settings**
(hotkeys, economy values, resolution with a confirmation dialog), **Log** (live log plus buttons
to open `bot.log` and the debug frame folder) and **Diagnostics** (elevation, game window,
template paths, `Probe screen`). The `Language` selector in the header switches 中文 / English
live and persists to `config.json` (`language`); log and event names stay in English. Default `zh`.

### Hotkeys

`F6` start · `F7` stop · `F8` panic · `F9` toggle. All editable in Settings (`Set...` then press
the keys, or type `<ctrl>+<alt>+s`). They are OS-level (`RegisterHotKey` plus a `GetAsyncKeyState`
poll), so they work while the game has focus.

### Run from source

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
start.bat          :: elevates, opens the native GUI
start_gui.bat      :: same, straight to the Settings tab
```

Extra flags: `--settings`, `--language en|zh`, `--run --dry-run` (headless single pass, no mouse),
`--probe "screenshot/*.png"` (offline template check), `--web` (legacy browser console, source
tree only), `--no-elevate`.

Templates, ROIs, anchors, bench geometry and the sell-zone guard are authored at
`display.ref_height = 1440` and scaled to the live frame, so 1080p and 1440p share one config.
The resolution buttons in Settings ask for confirmation before calling `ChangeDisplaySettingsW`
(primary monitor, dynamic, nothing written to the registry).

### Build

```bat
build.bat
```

Writes `dist/AutoMoneyPrinter/` and `dist/AutoMoneyPrinter.zip` (~98 MB). PyInstaller onedir,
`console=False`, icon from `assets/app-icon.ico`.

### Implementation notes (the traps)

- **Capture must be WGC.** The game renders as a fullscreen flip-model surface, so GDI `BitBlt`
  (what mss uses) returns the stale desktop underneath - hence "the game is on screen but the
  market button is not visible". `capture.backend` accepts `wgc`, `mss` or `auto`.
- **Templates were cropped 1:1 from a 4480×1440 desktop**, so each one carries an `roi`. If your
  layout differs, set `match.use_roi: false` or null the ROIs.
- **Dynamic bits are used as anchors only.** The stack count on `target-to-drag.png` is cropped
  away (`[0,0,103,58]`); `money-count.png` keeps just the coin icon as an anchor and hands the
  digits to OCR. Opening the shop repaints the coin (score ~0.6), so a missing anchor falls back
  to OCR on `money.fallback_roi_rel` (bottom-right) with a brightness guard that rejects the
  card-info popup; a ledger fallback keeps the balance correct if OCR fails entirely.
- **The shop "opens then instantly closes".** The entry icon lives in a scrolling list, so the
  template collapses as the backdrop changes; the entry is located by OCR on the `黑市主理人` label
  plus a fixed offset to its right, with the icon template as a fallback. A plain click sends
  mouse-down then mouse-up - the panel appears on down, so the up lands on the backdrop and closes
  it. `behavior.market_open.styles` therefore tries `away` (release 260 px higher), `hold` (160 ms)
  and `click`, verifying each against the refresh button and logging `OPEN MARKET RETRY`; the shop
  is reopened automatically if it disappears mid-run.
- **The card template keeps only the middle of the portrait** (`crop [20,40,130,100]`). Borders,
  the top-right star/gear badge and the gold footer change once a card is decorated, dropping the
  full-card score under 0.80, while the centre still scores 0.86+ across scales 0.86-1.0 (other
  characters peak at 0.49) - so the threshold can sit at 0.80.
- **Hard sell-zone guard.** Duplicated cards may only be clicked/sold in the bottom 20% of the
  screen. The drop slot sits mid-screen and the low-threshold rescan matches it at ~0.74, so a
  geometric check (centre y ≥ `min_y`, far from known slots) rejects it even with ROIs disabled;
  rejected hits are logged as `SELL ZONE REJECT` and the probe image draws the boundary in red.

### Troubleshooting

- `no game UI on screen (best match ...)` - the game is not in the foreground or the wrong surface
  was captured. Hit `Focus game`, press `F6` in the game, then check the `--probe` scores and
  `capture.backend`.
- `the game runs as administrator but this app does not` - relaunch through `start.bat` and accept
  the UAC prompt.
- `'refresh' not found ... best score 0.4` - the market is closed, the frame is still loading, or
  the ROIs no longer match your resolution.
- After changing machine or resolution, run `python main.py --probe "screenshot/*.png"`; hits
  should score ~0.99.
- Screenshots, logs and build output are **not** in this repo (`.gitignore` excludes
  `screenshot/`, `logs/`, `dist/`, `build/`), so `tests/test_offline.py` needs your own frames in
  `screenshot/` before it can run.

### Credits

- App icon derived from `characters/brand/whale-girl-logo-master.png` in the MIT-licensed community
  project [deepseek-whale-girl](https://github.com/TreapGoGo/deepseek-whale-girl) - the "blue chubby
  fish" (鲸鱼娘) mascot - composited on a pale blue rounded square; source art in
  `assets/whale-girl.png`. The artwork belongs to its original creators and community; this project
  is not affiliated with them.
- OCR by [RapidOCR](https://github.com/RapidAI/RapidOCR), capture by
  [windows-capture](https://github.com/NiiightmareXD/windows-capture).
- Code and docs generated with **WorkBuddy (DeepSeek V4.1 Flash)**.

### Disclaimer

For **learning and automation research only**. Automating input may violate the game's terms of
service and can put your account at risk - you accept the consequences. Not for commercial use.
Not affiliated with miHoYo / HoYoverse.
