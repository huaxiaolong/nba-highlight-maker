# NBA Highlight Maker

在 macOS 上从比赛录像制作球员集锦：读取 NBA 官方逐回合数据，定位候选片段，人工审核后导出。支持勾选本场出场球员，保留运动战进球、助攻和原解说，不包含罚球。

## 环境与素材

- macOS、Python 3.9+、Swift 编译器（Xcode Command Line Tools）。
- FFmpeg（含 `ffprobe`）；下载另需 `yt-dlp`。Python 无第三方包依赖。
- 前置视频可选；开启水印时需要本地 `assets/nba2k-logo.png`。

录像、审核数据、成片和第三方媒体不随源码提交，使用时自行准备或从备份还原。球队封面 Logo 缺失时会自动下载；自备前置视频放入 `media/`。开场源码见 [intro/README.md](intro/README.md)。

## 快速开始

在项目目录执行。下面以 `0022600005` 为例，处理其他比赛时替换 Game ID、地址和目录。

### 1. 查询 Game ID，准备录像

先查询 NBA 官方比赛列表，复制目标比赛的 10 位 Game ID（保留开头的 `0`）：

```bash
# 今天的所有比赛
python3 scripts/nba_highlights.py games

# 指定日期
python3 scripts/nba_highlights.py games --date 2026-10-24

# 今天湖人的比赛
python3 scripts/nba_highlights.py games --team LAL

# 指定日期，列出湖人或快船参加的比赛
python3 scripts/nba_highlights.py games --date 2026-10-24 --team LAL LAC
```

日期和开赛时间统一使用**北京时间**。球队参数仅接受英文球队代码（不区分大小写），例如湖人 `LAL`、快船 `LAC`。输出包含 Game ID、开赛时间、客队、主队和状态。多个球队匹配任意一队，不限于两队直接交锋；无比赛时会明确提示。查询需要联网。

下载 YouTube 集锦：

```bash
python3 scripts/nba_highlights.py download --game-id 0022600005 \
  --game-dir ./recordings/0022600005 \
  --source-type highlights --ocr-profile spectrum \
  'https://www.youtube.com/watch?v=GU4e--bCuwQ'
```

支持完整微博、YouTube HTTPS 地址，画质取最高可用值。下载成功后自动写入 `recordings.json`；失败保留中间文件，可用同一命令重试。脚本不自动读取浏览器登录信息。

| 素材 | 配置 |
| --- | --- |
| 完整录像 | `--source-type recording`，默认值 |
| 剪辑集锦 | 必须指定 `--source-type highlights` |
| ESPN 计分牌 | `--ocr-profile espn`，默认值 |
| Spectrum SportsNet 计分牌 | `--ocr-profile spectrum` |

计分牌配置按画面选择，与下载平台无关。当前仅校准这两种样式，其他样式需调整识别规则。

已有本地分节录像可直接放入比赛目录，命名为 `Q1.mp4`—`Q4.mp4`、`OT1.mp4` 等。其他文件名、跨节录像或本地集锦需在该目录的 `recordings.json` 登记：

```json
{
  "game_id": "0022600005",
  "recordings": [
    {"file": "video.mp4", "periods": [1, 2, 3, 4],
     "source_type": "highlights", "ocr_profile": "spectrum"}
  ]
}
```

文件必须位于比赛目录内。请确认视频对阵、节次和 Game ID 一致；出现四节不表示集锦收录了全部回合。

### 2. 选择球员并分析

下载完成后启动审核台：

```bash
python3 scripts/nba_highlights.py review
```

打开本机 `http://127.0.0.1:8767/`。点击“重新载入”可刷新比赛列表，包括刚下载的“待分析”比赛。

1. 选择比赛，勾选需要的出场球员，点击“应用球员”。首次不预选任何球员。
2. 点击“复制分析命令”，在终端执行。命令只分析已选球员。
3. 分析完成后，回到页面点击“重新载入”。

也可直接指定球员 ID：

```bash
python3 scripts/nba_highlights.py analyze --game-id 0022600005 \
  --game-dir ./recordings/0022600005 --players 1629029 1630559
```

默认输出到 `output/0022600005/`。省略 `--players` 时必须已有保存的球员选择；没有选择会报错并列出出场球员 ID，不会自动分析全部球员。

更换素材组合时，用 `--output-dir` 创建独立审核版本。重新分析会保留原有人工区间和状态；需要官方赛后修订时加 `--refresh-data`，随后重新核查候选和旧审核记录。

### 3. 审核

桌面审核区采用三栏布局：左侧回合列表独立滚动，中间播放视频，右侧调整区间并保存。球员选择位于“球员与分析”折叠区；前置视频、导出命令、封面和标题位于右上角“导出与投稿”窗口。

1. 选择回合，切换到“源录像”，确认动作、起止时间和音轨后保存。
2. 处理所选球员的全部待确认记录，再复制页面中的导出命令。

已有分析结果时，更改球员选择会保留原审核记录；新增球员的回合进入待审核。只缩小球员范围不需要重新分析。

| 状态 | 含义 |
| --- | --- |
| `pending` | 待确认，阻止导出 |
| `keep` | 已确认保留 |
| `exclude` | 主动排除，需填写原因 |
| `missing` | 已检查所有提供的源视频并确认未收录，需填写依据 |

自动候选不等于动作完整，OCR 未定位也不等于缺失。共享回合的源录像和区间需一致。保存会直接写回 `review.csv`；多个窗口发生修改冲突时，重新载入后再操作。

### 4. 导出与投稿

预览与成片沿用比赛素材帧率，不再固定降为 25 帧。多源素材取最高帧率，片头统一使用同一输出帧率后拼接。

```bash
python3 scripts/nba_highlights.py export \
  --review ./output/0022600005/review.csv \
  --no-watermark
```

默认不拼接任何前置视频。比赛片段默认添加 2K 水印，用 `--no-watermark` 关闭。

需要片头、风景片或其他内容时，用 `--prepend` 指定本地视频，按参数顺序播放。前置视频保留原音轨，无音轨时补静音，均不加水印：

```bash
python3 scripts/nba_highlights.py export --review ./output/0022600005/review.csv \
  --prepend ./media/opening.mov ./media/another.mp4 --no-watermark
```

自备素材放在 `media/`（已忽略，不提交 Git）；现有 `intro/la-scenery.mp4` 也已忽略。审核台自动列出 `media/` 下的视频（支持子目录），勾选后用“上移 / 下移”编排顺序；不勾选则不拼接。选择按审核版本保存，新增文件后点击“刷新素材”。设置在重新导出后生效。

导出按已保存的球员选择筛选，也可用 `--players 球员ID ...` 指定范围。输出为 1920×1080、25 FPS、H.264/AAC；成功后替换该审核版本的同名产物。

| 输出 | 用途 |
| --- | --- |
| `highlights.mp4` | 最终成片 |
| `cover.png`、`cover_4x3.png`、`cover_3x4.png` | 16:9、4:3、3:4 投稿封面 |
| `title.txt`、`title_douyin.txt`、`description.txt` | B 站标题、抖音标题和简介 |
| `selection.json`、`export.json` | 回合选择与导出记录 |

审核台“封面与标题”区可预览、下载和复制投稿素材。抖音标题最多 30 字符，空格和标点计数。修改审核或球员选择后，需重新导出更新成片和标题。

## 常用命令

```bash
# 默认打开指定审核版本；端口被占用时可加 --port 8768
python3 scripts/nba_highlights.py review --review ./output/0022600005/review.csv

# 命令行修改区间：3 是官方事件编号，时间相对于源录像
python3 scripts/nba_highlights.py adjust 3 00:27.3 00:36.5 \
  --player 1631117 --review ./output/0022600005/review.csv

# 只更新默认输出目录的封面与文案，不重新编码视频
python3 scripts/nba_highlights.py cover --game-id 0022600005

# 查看全部参数
python3 scripts/nba_highlights.py --help
python3 scripts/nba_highlights.py analyze --help
```

## 数据与排错

- **备份**：保留源录像、`recordings.json` 和整个审核版本目录。`review.csv`、`players.json`、`opening.json` 含人工结果，不能靠重跑 OCR 恢复。本地文件的忽略范围见 [.gitignore](.gitignore)。
- **定位不准**：检查计分牌配置与录像身份，在源录像中手工定位。集锦跳剪、回放和分屏均需人工核查。
- **无法导出**：检查所选球员是否仍有 `pending`、区间冲突，或缺少源文件、音轨和开场素材。
- **Vision 报错**：先在正常终端运行。开发工具沙盒可能限制 Vision；不要关闭系统安全设置。
- **下载失败**：查看终端原始错误；YouTube 验证问题参考 [yt-dlp 文档](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube)。

## 目录

| 目录 | 内容 |
| --- | --- |
| `nba_highlight_maker/` | Python 业务代码 |
| `scripts/` | 命令行入口 |
| `native/` | Swift OCR 与封面渲染 |
| `web/` | 审核台页面 |
| `tests/` | Python、JavaScript 测试 |
| `assets/`、`intro/` | 图像配置、来源记录与开场源码 |
| `media/`、`recordings/`、`output/` | 本地前置素材、录像与审核产物 |

## 开发验证

Python 测试使用临时目录和合成数据；播放器检查另需 Node.js。按改动选择相关测试，例如：

```bash
python3 -m unittest tests.test_players tests.test_review_console tests.test_cover
node tests/test_review_player.js
```
