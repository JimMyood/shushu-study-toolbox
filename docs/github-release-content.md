# GitHub 发布内容文档（复制即用）

这份文档只存放 GitHub 页面上需要填写的文字。发布步骤见 [`publish-to-github.md`](publish-to-github.md)。仓库链接已使用当前发布账号 `JimMyood`；若仓库所有者改变，请同步更新全文。

## 1. 仓库基础信息

| 字段 | 填写内容 |
| --- | --- |
| Repository name | `shushu-study-toolbox` |
| Visibility | `Public` |
| Default branch | `main` |
| License | `MIT` |
| Homepage | 首次发布留空 |

### Description（中文，推荐）

```text
本地 Agent Skill：把视频、字幕、网页和 PDF 变成双语学习材料，无需外部 API Key。
```

### Description（英文备选）

```text
A local agent skill that turns videos, subtitles, web pages, and PDFs into bilingual study materials—without an external API key.
```

### Topics

```text
agent-skill
ai-tools
bilingual
claude-code
codex
faster-whisper
ffmpeg
learning-tools
python
srt
study-tool
yt-dlp
```

## 2. 对外链接

仓库首页：

```text
https://github.com/JimMyood/shushu-study-toolbox
```

安装说明：

```text
https://github.com/JimMyood/shushu-study-toolbox#三步安装
```

首个 Release：

```text
https://github.com/JimMyood/shushu-study-toolbox/releases/tag/v1.0.0
```

## 3. 首个 Release

- Tag：`v1.0.0`
- Target：`main`
- Title：`Shushu Study Toolbox v1.0.0 — 首个公开版本`

### Release 正文

````markdown
## 树树工具箱是什么

树树工具箱是一套运行在本地的 Agent Skill，把视频、字幕、网页和 PDF 整理成可复习的双语学习材料。它适合接入 Claude Code、Codex 等能够读取 `SKILL.md` 并执行本地脚本的 AI agent。

它不是播放器或在线转写站，而是一条可以重复运行、逐步检查的学习工作流。下载、切块、时间轴校验和封装由脚本完成；翻译、总结和解释交给 agent，不需要填写外部 AI API Key。

## v1.0.0 能做什么

- 下载视频或音频；
- 优先获取来源字幕，缺少字幕时可在确认后本地转写；
- 分块翻译 SRT，并校验序号、时间轴和条目数；
- 生成双语学习笔记；
- 软封装或硬烧录双语字幕；
- 精读网页和 PDF，整理为结构化学习材料。

## 快速开始

```bash
git clone https://github.com/JimMyood/shushu-study-toolbox.git
cd shushu-study-toolbox
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  command -v "$candidate" >/dev/null && "$candidate" --version
done
PYTHON_BIN="<实际存在的 Python 3.10-3.13 命令>"
"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.json config.json
python scripts/doctor.py
```

上面是 macOS / Linux 示例，运行前必须替换 `PYTHON_BIN` 的占位符；Windows 命令见 README 的“三步安装”。

随后让你的 agent 读取仓库根目录的 `SKILL.md`，直接描述目标，例如：

> 帮我把这个英文视频做成双语学习笔记：<URL>

详细安装方式、命令和安全边界见 [README](https://github.com/JimMyood/shushu-study-toolbox#readme)。

## 兼容性

- 推荐 Python 3.13；
- 本地转写链路支持 Python 3.10–3.13；
- Python 3.14 可运行除本地转写之外的主要功能；
- 需要按所用功能安装 `ffmpeg`、`yt-dlp` 等本地依赖。

## 验证记录

- 全量回归测试通过；
- CI：Ubuntu、macOS、Windows × Python 3.11、3.13；
- 真实端到端样例：NASA Goddard 65.32 秒公开视频，产出 19 条双语字幕，并验证软字幕、硬字幕和本地转写链路。

NASA 素材仅用于流程验证，不代表 NASA 对本项目的认可或背书。

## 使用边界

请只处理你有权使用的公开素材。不要绕过登录、付费、DRM 或站点限制；下载与本地转写仅用于个人学习。
````

## 4. GitHub 仓库首页发布语

### 中文版

```text
树树工具箱 v1.0.0 开源了。

它把英文视频学习里最碎的几步——下载、字幕、翻译、时间轴校验、笔记和封装——整理成一套本地 Agent Skill。接入 Claude Code 或 Codex 后，可以直接用自然语言发起任务。

6 个工具、0 个外部 API Key；推荐 Python 3.13。仓库里包含完整 README、测试、跨平台 CI 和真实端到端验证记录。

项目地址：https://github.com/JimMyood/shushu-study-toolbox
```

### English version

```text
Shushu Study Toolbox v1.0.0 is now open source.

It packages the repetitive parts of learning from English videos—download, subtitles, translation, timeline validation, notes, and muxing—into one local agent skill. Connect it to Claude Code or Codex and start a workflow in plain language.

Six tools, no external AI API key, with Python 3.13 recommended. The repository includes documentation, tests, a cross-platform CI matrix, and a real end-to-end verification record.

Repository: https://github.com/JimMyood/shushu-study-toolbox
```

## 5. 小红书首评

```text
GitHub：https://github.com/JimMyood/shushu-study-toolbox

完整安装步骤在 README。你最想先跑双语字幕，还是学习笔记？
```

## 6. 可选社交预览图文案

如果以后补一张 1280×640 的 GitHub Social Preview，可使用：

```text
SHUSHU STUDY TOOLBOX
Turn media into bilingual study materials
6 tools · Local-first · No external API key
```

## 7. 发布前最后检查

- [ ] 全文仓库链接均指向 `JimMyood/shushu-study-toolbox`；
- [ ] 仓库 URL 在无痕窗口可打开；
- [ ] `v1.0.0` Release URL 可打开；
- [ ] README 图片、安装命令和目录锚点有效；
- [ ] Actions 的 6 个矩阵任务全部通过；
- [ ] 没有 Token、Cookie、私人素材或本机配置进入提交；
- [ ] 小红书正文与首评只在以上检查通过后发布。
