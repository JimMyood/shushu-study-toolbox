# Task 10 端到端真机验收报告

日期：2026-07-15
基线：`63283392284a1cb212fa4249bf6e0190a4ca4ba8`
工作树：`/Users/jim/Desktop/shushu-study-toolbox/.worktrees/implement-study-toolbox`

## 结论

- “我要学这个视频”学习流水线真实跑通：官方英文字幕抓取、清理、校验、分块、逐行简体中文翻译、双语合并和学习笔记均完成。
- 收藏流水线在同一 `item_dir` 真实跑通：重新下载 720p MP4，保留 `video.mp4` / `meta.json`，生成可开关字幕的 `video.subbed.mp4`。
- 原文与双语字幕都是 19 条 cue，逐条 `start` / `end` 完全一致。
- `video.subbed.mp4` 保留 H.264 / AAC，并且只新增一个 `mov_text` 字幕流；输入、输出时长同为 65.317732 秒。
- 三张截图均来自真实结果，已检查 PNG、非空、尺寸与可读性；没有使用 imagegen。
- 素材来源为 NASA Goddard，仅用于本项目教育 / 信息型验收与截图，不暗示 NASA 背书。

## 素材与目录

- YouTube：`https://www.youtube.com/watch?v=iXiH6KCBhFE`
- NASA 官方页面：`https://svs.gsfc.nasa.gov/12323`
- 官方署名：NASA's Goddard Space Flight Center
- `meta.json`：标题 `Where can you #SpotHubble?`，上传者 `NASA Goddard`，时长 65.0 秒，日期 2016-08-05。
- 输出根：`/private/tmp/shushu-study-toolbox-e2e-20260715`
- `item_dir`：`/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble`
- 没有读取或复用旧的 `/private/tmp/shushu-study-e2e` 产物。

`item_dir` 由 `common.item_dir` 真实生成：

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python -c 'import sys; sys.path.insert(0,"scripts"); from common import item_dir; print(item_dir({"output_dir":"/private/tmp/shushu-study-toolbox-e2e-20260715"}, "Where can you #SpotHubble?", "2026-07-15"))'
/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble
[exit 0]
```

## 环境预检

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python scripts/doctor.py
复制 config.example.json 为 config.json 可自定义
✅ Python 3.14.6（要求 ≥ 3.10）
✅ yt-dlp 可导入
✅ ffmpeg（system）：/opt/homebrew/bin/ffmpeg
❌ 转写功能不可用。Python 3.14 暂无该包……
❌ 输出目录不可写：/Users/jim/ShushuStudy……
[exit 1]
```

本次不需要本地转写（来源有官方英文字幕），并且任务明确指定了可写的 `/private/tmp/shushu-study-toolbox-e2e-20260715`，因此这两项不阻断字幕、下载、翻译、笔记与封装能力。

## 现场问题一：官方 SRT 含纯空白 cue

### RED

先只改测试：

- `test_cli_subs_removes_blank_cues_before_atomic_publish`
- `test_cli_subs_rejects_all_blank_cues_without_publishing`

第一次误用了系统 `pytest`；系统 Python 3.14 缺少项目依赖 `srt`，收集阶段 exit 2。这个错误不算 RED：

```console
$ pytest -q tests/test_fetch.py -k 'blank_cues'
E   ModuleNotFoundError: No module named 'srt'
[exit 2]
```

切换到任务 venv 后见证了预期 RED：混合样本仍发布纯空白 cue，全空样本错误地返回 exit 0。

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python -m pytest -q tests/test_fetch.py -k 'blank_cues'
FF                                                                       [100%]
2 failed, 52 deselected in 0.18s
[exit 1]
```

### GREEN

最小实现位于 `scripts/fetch.py`：

- 只在 yt-dlp 的临时目录中以 UTF-8 解析 SRT。
- 过滤 `cue.content.strip()` 为空的 cue。
- 非空 cue 的 index、start、end 与 content 原样保留。
- 全部为空时抛出中文 `FetchError`，exit 1，无 traceback，也不发布最终文件。
- 只有清理成功后才 `replace` 到 `subs.orig.srt`；干净 SRT 不重写，保持原有字节格式。

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python -m pytest -q tests/test_fetch.py -k 'blank_cues'
..                                                                       [100%]
2 passed, 52 deselected in 0.25s
[exit 0]

$ /private/tmp/shushu-study-toolbox-venv/bin/python -m pytest -q tests/test_fetch.py
......................................................                   [100%]
54 passed in 0.22s
[exit 0]
```

## 学习流水线真实命令

### 1. 重新抓取官方英文字幕

沙箱内首次联网按预期失败：

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python scripts/fetch.py subs 'https://www.youtube.com/watch?v=iXiH6KCBhFE' --lang en --out '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble'
网络连接失败，暂时无法访问视频站点。请检查网络连接后重试。
[exit 1]
```

同一命令经 sandbox escalation 重新联网：

```console
字幕已保存：/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/subs.orig.srt
[exit 0]
```

真实官方 SRT 原本的 cue index 有空档；清理后保留 19 个非空 cue，文件 1306 bytes。

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python scripts/srt_tools.py validate '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/subs.orig.srt'
校验通过：19 条字幕
[exit 0]
```

### 2. 当前 `srt_tools chunk`

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python scripts/srt_tools.py chunk '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/subs.orig.srt' --size 40 --out-dir '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/translation-chunks'
[exit 0]
```

`manifest.json` 记录 `cue_count=19`、一个 chunk、`line_count=19`、起止 index 为 1 / 26。逐行忠实写入 `chunk_000.zh.txt` 后检查：

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python -c 'from pathlib import Path; import sys; a=Path(sys.argv[1]).read_text(encoding="utf-8").splitlines(); b=Path(sys.argv[2]).read_text(encoding="utf-8").splitlines(); ok=len(a)==len(b) and all(x.strip() for x in b); print(f"源文 {len(a)} 行，译文 {len(b)} 行，译文均非空：{ok}"); raise SystemExit(not ok)' '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/translation-chunks/chunk_000.txt' '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/translation-chunks/chunk_000.zh.txt'
源文 19 行，译文 19 行，译文均非空：True
[exit 0]
```

### 3. 合并与校验双语字幕

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python scripts/srt_tools.py merge '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/subs.orig.srt' --chunks-dir '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/translation-chunks' --layout original-top --out '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/subs.bi.srt'
[exit 0]

$ /private/tmp/shushu-study-toolbox-venv/bin/python scripts/srt_tools.py validate '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/subs.bi.srt'
校验通过：19 条字幕
[exit 0]
```

逐条比对结果：

```console
原文 cue=19，双语 cue=19，start/end 完全一致：True
[exit 0]
```

### 4. `notes.md`

笔记只使用 `meta.json` 与已校验字幕。手册结构检查：

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python -c 'from pathlib import Path; import sys; p=Path(sys.argv[1]); t=p.read_text(encoding="utf-8"); hs=["## 一句话核心","## 适合谁看","## 核心要点","## 核心概念","## 常见误区","## 学以致用","## 原文金句"]; missing=[h for h in hs if h not in t]; print("缺失:"+",".join(missing) if missing else "结构检查通过"); raise SystemExit(bool(missing) or not t.strip())' '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/notes.md'
结构检查通过
[exit 0]
```

人工约束检查结果：

```text
一句话核心：58 字，句末标点 1 个
适合谁看：含数字分钟 True
核心要点：5 条，字数 [57, 50, 54, 58, 70]
核心概念：2 条；学以致用：2 条；原文金句：3 条
误区占位精确：True
模板约束全部通过：True
[exit 0]
```

金句三条均能在 `subs.orig.srt` 原文中核对。没有依据的误区严格写为 `(素材未纠正常见误区)`。

视觉验收还发现原手册模板没有 Markdown 空行，Pandoc 会把 `##` 当普通正文；已给真实 `notes.md` 和 `tools/notes.md` 模板补上必要空行。`tools/notes.md` 最终 119 行，仍不超过 120 行。

## 收藏流水线真实命令

### 1. 重新下载最高 720p 视频

以下命令经 sandbox escalation 联网：

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python scripts/fetch.py video 'https://www.youtube.com/watch?v=iXiH6KCBhFE' --quality 720 --out '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble'
视频已保存：/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/video.mp4
元数据已保存：/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/meta.json
[exit 0]
```

### 2. 可开关字幕软封装

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python scripts/mux.py soft '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/video.mp4' '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/subs.bi.srt' --out '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/video.subbed.mp4'
[exit 0]
```

`ffprobe` 原始证据：

```console
$ ffprobe -v error -show_entries format=duration:stream=index,codec_type,codec_name -of json '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/video.mp4'
streams: h264(video), aac(audio)
duration: 65.317732
[exit 0]

$ ffprobe -v error -show_entries format=duration:stream=index,codec_type,codec_name -of json '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/video.subbed.mp4'
streams: h264(video), aac(audio), mov_text(subtitle)
duration: 65.317732
[exit 0]
```

显式断言：

```text
输入流：[('video', 'h264'), ('audio', 'aac')]
输出流：[('video', 'h264'), ('audio', 'aac'), ('subtitle', 'mov_text')]
新增流：[('subtitle', 'mov_text')]
输入时长=65.317732s，输出时长=65.317732s，可播放且时长合理：True
[exit 0]
```

为真实截图额外运行：

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python scripts/mux.py burn '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/video.mp4' '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/subs.bi.srt' --out '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/video.preview.burn.mp4'
硬烧录需重编码，耗时约与视频时长相当。
[exit 0]
```

## `item_dir` 产物与尺寸

```text
meta.json                                             176 bytes
notes.html                                          6,270 bytes
notes.md                                            2,312 bytes
subs.bi.srt                                         2,007 bytes
subs.orig.srt                                       1,306 bytes
translation-chunks/chunk_000.txt                      668 bytes
translation-chunks/chunk_000.zh.txt                   701 bytes
translation-chunks/manifest.json                      404 bytes
video.mp4                                       15,185,772 bytes
video.preview.burn.mp4                          14,766,351 bytes
video.subbed.mp4                               15,188,090 bytes
```

## 截图采集

### Markdown 转 HTML

按 `md-to-html` 的 warm 锁定主题运行：

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python /Users/jim/.agents/skills/md-to-html/scripts/md2html.py '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/notes.md' --theme warm
✓ /private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/notes.html
[exit 0]
```

所有中间 HTML 均位于 `/private/tmp`；没有跟踪 HTML。

### Browser-use 与安全回退

`browser-use doctor` exit 0；命名会话为 `task10-e2e`。首次沙箱内打开本地 HTML：

```console
$ browser-use --session task10-e2e open 'file:///private/tmp/shushu-task10-terminal.html'
Error: Failed to start daemon
[exit 1]
```

显式 close 后，经 escalation 重试：

```console
$ browser-use --session task10-e2e open 'file:///private/tmp/shushu-task10-terminal.html'
Error: Failed to establish CDP connection to browser: did not receive a valid HTTP response
[exit 1]

$ browser-use --session task10-e2e close
Browser closed
[exit 0]

$ browser-use sessions
No active sessions
[exit 0]
```

没有空转：改用本机离线 Playwright + Google Chrome 的安全渲染脚本。终端图命令 exit 0：

```console
$ node /Users/jim/.agents/skills/xiaohongshu-card/scripts/render.js /private/tmp/shushu-task10-terminal.html docs/assets/terminal-pipeline.png
docs/assets/terminal-pipeline.png
[exit 0]
```

`notes.html` 所在目录含 `#SpotHubble`，首次 `file://` 被当成 fragment 而 exit 1；复制同一 HTML 到无 `#` 的 `/private/tmp/shushu-task10-notes.html` 后重渲染 exit 0：

```console
$ node /Users/jim/.agents/skills/xiaohongshu-card/scripts/render.js /private/tmp/shushu-task10-notes.html docs/assets/notes-preview.png
docs/assets/notes-preview.png
[exit 0]
```

初次 `mux.py burn` 的默认 libass 字体把中文画成方框，视觉检查不通过。最终直接从真实 `video.mp4` 与真实 `subs.bi.srt` 在 36 秒处抽帧，使用 `/private/tmp` 可写 fontconfig cache 与本机 `Arial Unicode MS`：

```console
$ env XDG_CACHE_HOME=/private/tmp/shushu-font-cache ffmpeg -hide_banner -loglevel error -i '/private/tmp/shushu-study-toolbox-e2e-20260715/2026-07-15-Where can you #SpotHubble/video.mp4' -ss 00:00:36 -vf "subtitles=filename='/private/tmp/shushu-task10-subs.srt':fontsdir='/private/tmp/shushu-cjk-fonts':force_style='FontName=Arial Unicode MS,FontSize=26,Outline=2,Shadow=0,MarginV=28,Alignment=2'" -frames:v 1 -update 1 -y docs/assets/bilingual-subtitles.png
[exit 0]
```

### 最终 PNG 证据

| 文件 | 字节 | 尺寸 | 实际视觉检查 |
| --- | ---: | ---: | --- |
| `docs/assets/terminal-pipeline.png` | 780,190 | 2160×2880 | 真实命令、退出码、19 cues、ffprobe 与 NASA 来源均清晰；注明不代表 NASA 背书。 |
| `docs/assets/bilingual-subtitles.png` | 591,654 | 1280×720 | 真实 36 秒视频画面；英文与简体中文两行完整可辨、无方框。 |
| `docs/assets/notes-preview.png` | 565,390 | 2160×2880 | warm 模板标题、来源、核心句、5 条要点及所有后续模板节均清晰分节。 |

三张均由 `file` 识别为非空 PNG；`sips` 返回上表尺寸。仅这三张 PNG 位于 `docs/assets/`，没有跟踪其他图片或临时 HTML。

## 全量测试

```console
$ /private/tmp/shushu-study-toolbox-venv/bin/python -m pytest -q
........................................................................ [ 46%]
........................................................................ [ 92%]
............                                                             [100%]
156 passed in 5.05s
[exit 0]
```

## 改动清单

- `scripts/fetch.py`：发布前在临时目录清理空白字幕 cue，全空时人话失败。
- `tests/test_fetch.py`：两条先红后绿的回归测试。
- `tools/notes.md`：模板补 Markdown 空行，确保 Pandoc / warm 页面正确分节。
- `docs/assets/terminal-pipeline.png`
- `docs/assets/bilingual-subtitles.png`
- `docs/assets/notes-preview.png`
- `.superpowers/sdd/task-10-report.md`

计划提交信息（精确）：`test: 端到端验收(学习+收藏流水线)+ 宣发截图`

## 遗留说明

- `browser-use` 本机 daemon/CDP 连接仍失败；命名会话已明确关闭且 `browser-use sessions` 显示无活跃会话。截图已用本机离线 Playwright 安全完成，不影响本次交付。
- doctor 的 `faster-whisper` 仍不可用，但本素材使用官方字幕，没有走本地转写；其他本次需要的能力已真实验证。
- `video.preview.burn.mp4` 是截图验收允许的临时硬字幕成品，保留在 `/private/tmp` 的 `item_dir`，不进入 Git。
