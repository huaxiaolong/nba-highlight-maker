# 湖人开场 · 统一开场

直接用浏览器打开 `lakers-intro.html`。页面会播放 4 秒动画，然后定格；可暂停、重播、拖动时间。系统启用减少动态效果时，默认定格，仍可主动点击播放。

顺序：大字 LAKERS → 小字 LOS ANGELES → 完整湖人 Logo。深紫背景，金色大字，没有背景线条、独立篮球或球员信息。只参考 Spirit 开场的字号层级与节奏。

HTML 提供无声动画预览；生成的 MP4 可通过 `export --prepend intro/lakers-intro.mp4` 加入集锦。两段文字保持固定位置和字号，只做透明度渐变，避免细微缩放和位移引起的字形边缘抖动。大字在 1920 × 1080 设计画布上的字号为 420px。两段文字后，完整 Logo 渐入并轻微放大，2.7 秒后定格到结束，不裁切 Logo。

## 素材

- `lakers-logo.svg`：NBA 官方原图，保留未修改。 https://cdn.nba.com/logos/nba/1610612747/global/L/logo.svg


`node tests/check_intro.cjs` 使用 Node.js 内置断言检查两段文字及 Logo 的顺序、渐变过渡、移除篮球、时间轴数值和控件逻辑，不安装浏览器、不进行截图或视频捕获。浏览器预览不需要 Node.js。

## 开场视频

`lakers-intro.mp4`：1920 × 1080、25 FPS、4 秒、100 帧，H.264/yuv420p 视频及 48 kHz 双声道无声 AAC，支持快速播放。没有 2K Logo、预览控件或 BGM。`export` 默认不添加前置内容；用 `--prepend` 按需指定本片或其他视频。

本次使用现有应用内浏览器，临时将动画区设为原生 1920 × 1080。等待字体和 Logo 加载、暂停自动播放后，通过时间控件逐帧设置 i/25 秒，捕获动画区的 100 张 JPEG 画面，再用 FFmpeg 编码。不是实时录屏。临时页面、画面和浏览器尺寸覆盖已清理；未安装新的浏览器组件。

验收记录在 `video-verification.json`：尺寸、帧率、帧数、时长、编码格式和音轨检查通过，moov 位于 mdat 前，支持快速播放。抽查两段文字和最终 Logo，完整视频解码检查通过。

## 洛杉矶风景片

`la-scenery.mp4` 来自 The2Travellers 的《Cinematic Los Angeles Travel Film / City of Angels in 4K GH5 / California》：https://www.youtube.com/watch?v=biC7sU89rTE 。下载 1920×1080 视频与原音轨，原始内容保存在 `la-scenery.mp4`，不另存相同内容的副本。`la-scenery.mp4` 使用完整原片，内容和音轨不做删减，不叠加 2K 标志。来源、下载时间和校验值在 `la-scenery-source.json` 中；转载及音乐授权未核实。导出会精确重编码为 H.264/AAC、25 FPS 后拼接，保持 Logo 和音频规则与正片分开。
