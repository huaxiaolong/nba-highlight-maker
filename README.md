# NBA Highlight Maker

在 macOS 上从比赛录像制作球员集锦：读取 NBA 官方逐回合数据，定位候选片段，人工审核后导出。支持勾选本场出场球员，保留运动战进球、助攻和原解说，不包含罚球。

## 环境与素材

- macOS、Python 3.9+、Swift 编译器（Xcode Command Line Tools）。
- FFmpeg（含 `ffprobe`）；下载另需 `yt-dlp`。Python 无第三方包依赖。
- 前置视频可选；开启水印时需要本地 `assets/nba2k-logo.png`。

录像、审核数据、成片和第三方媒体不随源码提交，使用时自行准备或从备份还原。球队封面 Logo 缺失时会自动下载；自备前置视频放入 `media/`。开场源码见 [intro/README.md](intro/README.md)。

## 快速开始

在项目目录执行。下面以 `0022600005` 为例，处理其他比赛时替换 Game ID、地址和目录。

### 1. 准备录像

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

### 2. 分析

```bash
python3 scripts/nba_highlights.py analyze --game-id 0022600005 \
  --game-dir ./recordings/0022600005
```

默认输出到 `output/0022600005/`，首次分析包含全部出场球员。可用 `--players 球员ID ...` 缩小范围。已有版本保存过球员勾选时，优先沿用该选择。

更换素材组合时，用 `--output-dir` 创建独立审核版本。重新分析会保留原有人工区间和状态；需要官方赛后修订时加 `--refresh-data`，随后重新核查候选和旧审核记录。

### 3. 审核

```bash
python3 scripts/nba_highlights.py review
```

打开本机 `http://127.0.0.1:8767/`。顶部选择比赛和审核版本，然后：

1. 勾选出场球员，点击“应用球员”。选择会保存，新增回合进入待审核。
2. 选择回合，切换到“源录像”，确认动作、起止时间和音轨后保存。
3. 处理所选球员的全部待确认记录，再复制页面中的导出命令。

| 状态 | 含义 |
| --- | --- |
| `pending` | 待确认，阻止导出 |
| `keep` | 已确认保留 |
| `exclude` | 主动排除，需填写原因 |
| `missing` | 已检查所有提供的源视频并确认未收录，需填写依据 |

自动候选不等于动作完整，OCR 未定位也不等于缺失。共享回合的源录像和区间需一致。保存会直接写回 `review.csv`；多个窗口发生修改冲突时，重新载入后再操作。

### 4. 导出与投稿

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
