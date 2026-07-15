# 树树工具箱(shushu-study-toolbox)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Context(为什么做)

Jim(独立创始人,无开发经验,代码全靠 agent)日常看英文视频/资料学习,需要把"下载、字幕、翻译、字幕入视频、精华笔记、资料总结"六件事整合成一个 agent skill:自己天天用,并开源到 GitHub 让别人 15 分钟跑通。设计文档已逐段确认:`~/Desktop/shushu-study-toolbox/docs/2026-07-14-design.md`。商用版假设仍冻结(growth-tree `docs/superpowers/2026-07-14-bilingual-tool-product-hypothesis.md`),本项目是个人版。

**交付物与顺序(Jim 确认)**:① skill 本体 → ② README + GitHub 发布指南 → ③ 小红书图文笔记 + 3 分钟口播稿(小红书活动不急,等成品截图)。

**Goal:** 在 `~/Desktop/shushu-study-toolbox/` 建成可用、可测、可开源的六工具 agent skill,并产出配套文档与宣发物料。

**Architecture:** 单 skill 包多工具路由:`SKILL.md` 总入口按意图路由到 `tools/*.md` 六份工具手册;Python 脚本干机械活(yt-dlp 下载/faster-whisper 转写/SRT 处理/ffmpeg 封装),会话内 agent 干语言活(翻译/笔记/摘要),不调外部 API。正本住桌面 = git 仓库,软链接进 `~/.claude/skills/` 全局生效。

**Tech Stack:** Python ≥3.10、yt-dlp、faster-whisper、`srt` 库、static-ffmpeg(兜底)+ 系统 ffmpeg(优先)、pytest、GitHub Actions(macOS/Windows/Ubuntu 矩阵)。

## Global Constraints(每个任务都隐含遵守)

- 机器名 `shushu-study-toolbox`;中文显示名一律「树树工具箱」;工具显示名格式「树树工具箱 - [功能]」
- 仓库根 = `~/Desktop/shushu-study-toolbox/`(已 git init,含设计文档与 .gitignore;growth-tree 仓库一行不动)
- 脚本干机械活、agent 干语言活;**不调任何外部 LLM/翻译 API,不管理任何 key**
- 跨平台纪律:全程 `pathlib`;所有文件读写显式 `encoding="utf-8"`;文件名清洗 Windows 非法字符 `<>:"/\|?*`;`subprocess` 不用 `shell=True`
- 产出集中到 `config.json` 的 `output_dir`,每素材一个文件夹 `<output_dir>/<YYYY-MM-DD>-<slug>/`
- 报错说人话("是什么+怎么办"),长任务不静默降级(转写前先告知耗时并确认)
- 中文文档为主;README 附 English quick start;License MIT
- 每个任务结束都 `git commit`(仓库内),提交信息中文,格式 `feat:/fix:/docs:/test:/chore:`
- Jim 机器实况:Python 3.14.6 / ffmpeg 7.1.1 / yt-dlp 2026.03.17 / brew / gh 已装。faster-whisper(依赖 ctranslate2)在 3.14 可能无 wheel:doctor 必须能检测并给出「用 Python 3.13 建 venv」的指引,转写不可用时其余五工具照常工作(优雅降级)
- 版权红线:README 与所有物料注明「下载与转写仅供个人学习,遵守各平台服务条款」

---

## 阶段一:skill 本体

### Task 1: 仓库骨架 + 公共模块 common.py(配置加载/文件名清洗/素材目录)

**Files:**
- Create: `scripts/common.py`、`config.example.json`、`requirements.txt`、`tests/test_common.py`、`docs/2026-07-14-implementation-plan.md`(本计划存档)

**Interfaces(后续所有脚本消费):**
- `load_config() -> dict` — 读仓库根 `config.json`,不存在时返回 `DEFAULTS` 并打印引导语「复制 config.example.json 为 config.json 可自定义」;路径一律 `Path(__file__).resolve().parent.parent`(resolve 穿透软链接,保证从 `~/.claude/skills/` 调用也定位到正本)
- `sanitize_filename(name: str) -> str` — 去除 `<>:"/\|?*` 与控制字符,压缩空白,截断到 80 字符
- `item_dir(config: dict, title: str, date_str: str) -> Path` — 返回并创建 `<output_dir>/<date_str>-<sanitize后的slug>/`;`output_dir` 支持 `~` 展开
- `DEFAULTS = {"output_dir": "~/ShushuStudy", "native_lang": "zh", "source_lang": "auto", "whisper_model": "small", "video_quality": "1080", "subtitle_layout": "original-top"}`

**Steps:**

- [ ] **1. 写失败测试** `tests/test_common.py`:

```python
from pathlib import Path
import json, sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import common

def test_sanitize_removes_windows_illegal_chars():
    assert common.sanitize_filename('How: to "win" <fast>?') == "How to win fast"

def test_sanitize_truncates_to_80():
    assert len(common.sanitize_filename("x" * 200)) <= 80

def test_defaults_when_no_config(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "REPO_ROOT", tmp_path)
    cfg = common.load_config()
    assert cfg["native_lang"] == "zh" and cfg["subtitle_layout"] == "original-top"

def test_item_dir_expands_home_and_creates(tmp_path):
    cfg = {"output_dir": str(tmp_path / "out")}
    d = common.item_dir(cfg, 'A/B: test', "2026-07-14")
    assert d.exists() and d.name == "2026-07-14-AB test"
```

- [ ] **2. 跑测试确认失败**:`cd ~/Desktop/shushu-study-toolbox && python3 -m pytest tests/ -v` → 预期 `ModuleNotFoundError: common`
- [ ] **3. 实现 `scripts/common.py`**(按上述接口;`load_config` 用 `json.loads(path.read_text(encoding="utf-8"))`,浅合并 DEFAULTS)
- [ ] **4. 写 `config.example.json`**(DEFAULTS 内容 + 每字段 `_comment` 说明)与 `requirements.txt`:

```
yt-dlp>=2026.1.1
srt>=3.5
static-ffmpeg>=2.7
faster-whisper>=1.1 ; python_version < "3.14"
pytest>=8
```

(3.14 上 faster-whisper 由 doctor 检测并指引,不让 `pip install -r` 整体失败)
- [ ] **5. 跑测试确认通过** → 预期 4 passed
- [ ] **6. 把本计划复制为 `docs/2026-07-14-implementation-plan.md`,commit**:`feat: 公共模块与仓库骨架(配置/清洗/素材目录)`

### Task 2: doctor.py 环境自检

**Files:** Create: `scripts/doctor.py`、`tests/test_doctor.py`

**Interfaces:**
- CLI:`python3 scripts/doctor.py [--json]` — 逐项检查并打印 ✅/❌ 与**当前平台**的修复命令;全过 exit 0,否则 exit 1。`--json` 输出 `{"python": true, "yt_dlp": true, "ffmpeg": "system|static|missing", "faster_whisper": false, "output_dir_writable": true}`
- `find_ffmpeg() -> tuple[str, str]` — 返回 `(路径, "system"|"static")`:先 `shutil.which("ffmpeg")`,再尝试 `import static_ffmpeg`,都无则 raise `FileNotFoundError`(**mux.py 与 tests 复用此函数**)
- 检查项与修复指引:Python≥3.10;yt-dlp 可 import;ffmpeg(见上);faster-whisper 可 import(失败时提示:「转写功能不可用。Python 3.14 暂无该包,可 `brew install python@3.13` 后用 3.13 建 venv;不用转写功能可忽略」);`output_dir` 可写。ffmpeg 缺失的平台命令:mac `brew install ffmpeg` / Windows `winget install ffmpeg` / Linux `sudo apt install ffmpeg`,或「pip 已含 static-ffmpeg 兜底,通常无需手装」

**Steps:**
- [ ] **1. 写失败测试**:`test_doctor_json_shape`(跑 `--json`,断言 5 个 key 都在)、`test_find_ffmpeg_returns_tuple`(本机应返回 system)
- [ ] **2. 确认失败 → 3. 实现 → 4. 确认通过**(Jim 机器上预期:faster_whisper 为 false、其余 true,exit 1 且给出 venv 指引——这本身就是一次真实验收)
- [ ] **5. Commit**:`feat: doctor 环境自检(平台感知修复指引 + ffmpeg 双保险)`

### Task 3: srt_tools.py — SRT 解析/分块/双语合并/校验(核心,严格 TDD)

**Files:** Create: `scripts/srt_tools.py`、`tests/test_srt_tools.py`、`tests/fixtures/sample.srt`(手写 5 条 cue 的英文字幕)

**Interfaces(翻译工作流的机械骨架,tools/translate.md 消费):**
- `chunk IN.srt --size 40 --out-dir DIR` — 拆成 `chunk_000.txt`…,每行一条 cue 文本(行内换行折叠为空格),并写 `manifest.json`(记录每块行数与 cue 索引区间)。agent 逐块翻译,产出同名 `chunk_000.zh.txt`(行数必须一致)——**断点续跑 = 哪块缺 `.zh.txt` 就补哪块**
- `merge IN.srt --chunks-dir DIR --layout original-top --out OUT.srt` — 校验每块译文行数与 manifest 一致(不一致则报「第 N 块行数不符:期望 X 实际 Y,请重翻该块」exit 2),按 layout 把原文/译文上下叠进每条 cue,时间轴原样保留
- `validate FILE.srt` — 能解析、时间轴单调不重叠(重叠仅告警)、无空 cue;通过 exit 0
- 内部用 `srt` 库解析/序列化,不手写解析器

**Steps:**
- [ ] **1. 写失败测试**(核心用例,全部真实代码):

```python
def test_chunk_then_merge_roundtrip(tmp_path):
    # chunk sample.srt → 伪造逐行"译文"(行前加"译:") → merge → 断言 cue 数不变、
    # 每条 cue 文本 = 原文 + "\n" + 译文、时间轴与原文件完全一致
def test_merge_rejects_line_count_mismatch(tmp_path):
    # 译文少一行 → merge exit 2,stderr 含 "行数不符"
def test_layout_translation_top(tmp_path):
    # --layout translation-top 时译文在上
def test_validate_catches_empty_cue(tmp_path): ...
```

- [ ] **2. 确认失败 → 3. 实现 → 4. 确认通过 → 5. Commit**:`feat: SRT 分块/双语合并/校验(支持断点续跑)`

### Task 4: fetch.py — 下载视频/仅音频/官方字幕(封装 yt-dlp)

**Files:** Create: `scripts/fetch.py`、`tests/test_fetch.py`

**Interfaces(tools/download.md、tools/subtitle.md 消费):**
- `python3 scripts/fetch.py subs URL --lang en --out DIR` — 官方字幕优先、自动字幕次之,转成 SRT 存 `DIR/subs.orig.srt`;**无任何字幕时 exit 3**(区别于一般错误的约定信号,agent 据此走转写)并打印可用字幕语言列表
- `python3 scripts/fetch.py audio URL --out DIR` — 仅音频 m4a(供转写,不下整个视频)
- `python3 scripts/fetch.py video URL --quality 1080 --out DIR` — mp4 优先、限高 quality;同时写 `DIR/meta.json`(url/title/uploader/duration_s/date)
- 实现要点:`import yt_dlp` 走 Python API(不 subprocess,便于拿元数据与错误分类);`build_opts(mode, quality, out_dir) -> dict` 纯函数抽出便于测试;错误翻译成人话:地区限制/会员内容/网络失败各给一句"怎么办"
- **Produces:** `meta.json` 的字段名就是上面五个,notes/收藏流水线都读它

**Steps:**
- [ ] **1. 写失败测试**(不联网,只测纯函数):`build_opts("video", "1080", d)` 断言 format 串含 `height<=1080`、outtmpl 落在 d 内;`build_opts("audio",...)` 断言 `format="bestaudio..."`;`classify_error()` 对含 "This video is available to Members" 的异常返回会员提示
- [ ] **2-4. TDD 循环** → **5. 手动冒烟(联网,一次)**:任选一条短 YouTube 视频跑三个子命令,确认产物与 exit code;把命令与结果记录进 commit message
- [ ] **6. Commit**:`feat: fetch 下载视频/仅音频/官方字幕(exit 3 约定 + 人话报错)`

### Task 5: transcribe.py — 本地转写(封装 faster-whisper)

**Files:** Create: `scripts/transcribe.py`、`tests/test_transcribe.py`

**Interfaces:**
- `python3 scripts/transcribe.py AUDIO --model small --lang auto --out OUT.srt` — 转写并直接产出 SRT;开跑前打印预计耗时(按音频时长 × 模型速率粗估,small≈0.3×实时)供 agent 转告用户
- `import faster_whisper` 放函数内 lazy import;缺包时报 doctor 同款指引后 exit 4(**约定:4 = 转写能力缺失**,agent 据此告知用户并停止,不静默跳过)
- `estimate_minutes(duration_s: float, model: str) -> int` 纯函数可测

**Steps:**
- [ ] **1. 写失败测试**:`estimate_minutes(600, "small")` 返回 3;无 faster-whisper 环境跑 CLI → exit 4 且 stderr 含 "3.13"
- [ ] **2-4. TDD 循环**(Jim 机器 3.14 正好是"缺包路径"的真实测试环境;转写成功路径留给 CI 或 venv 手测,计划不强求本机跑通)
- [ ] **5. Commit**:`feat: transcribe 本地转写(耗时预估 + 缺包优雅降级 exit 4)`

### Task 6: mux.py — 字幕入视频(封装 ffmpeg)

**Files:** Create: `scripts/mux.py`、`tests/test_mux_smoke.py`

**Interfaces:**
- `python3 scripts/mux.py soft VIDEO SRT --out OUT.mp4` — 软字幕封装(`-c copy -c:s mov_text`,秒级完成)
- `python3 scripts/mux.py burn VIDEO SRT --out OUT.mp4` — 硬烧录(`subtitles=` 滤镜),执行前打印「需重编码,耗时约与视频时长相当」
- ffmpeg 路径取自 `doctor.find_ffmpeg()`(复用,双保险逻辑只写一处)

**Steps:**
- [ ] **1. 写冒烟测试**(不需要真视频,ffmpeg 自己造):

```python
def test_soft_mux_adds_subtitle_stream(tmp_path):
    ff, _ = doctor.find_ffmpeg()
    v = tmp_path / "t.mp4"  # lavfi 生成 1 秒黑屏视频
    subprocess.run([ff, "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
                    "-y", str(v)], check=True, capture_output=True)
    out = tmp_path / "out.mp4"
    mux.soft(v, FIXTURE_SRT, out)
    probe = subprocess.run([ff.replace("ffmpeg", "ffprobe"), "-v", "error",
                            "-select_streams", "s", "-show_entries", "stream=codec_type",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert "subtitle" in probe.stdout
```

- [ ] **2-4. TDD 循环 → 5. Commit**:`feat: mux 软字幕封装 + 硬烧录`

### Task 7: 六份工具手册 tools/*.md(含 v2 笔记口径移植)

**Files:** Create: `tools/download.md`、`tools/subtitle.md`、`tools/translate.md`、`tools/embed.md`、`tools/notes.md`、`tools/digest.md`

**Interfaces:** 每份手册固定结构:`# 树树工具箱 - [功能]` → 触发语示例(中英各 2 条)→ 前置(需要哪些脚本/配置)→ 步骤(exact 命令 + exit code 分支处理)→ 产物路径 → 常见故障。手册是写给 agent 执行的,命令必须可直接复制运行。

**关键内容:**
- `subtitle.md`:先 `fetch.py subs`;**exit 3 → 告知用户「无官方字幕,转写约需 X 分钟,继续吗?」确认后** `fetch.py audio` + `transcribe.py`(不静默降级口径写死在手册里)
- `translate.md`:`srt_tools.py chunk` → agent 按块翻译(手册内含翻译质量口径:忠实原意、术语准确、口语字幕不逐字直译、保留说话人语气;每块译完行数自查)→ `merge`。中断续跑规则写明
- `notes.md`:**移植 growth-tree `note_prompt.md` 写作口径,适配个人版**。笔记为 Markdown,`native_lang` 为主、关键术语双语对照。模板(写进手册,agent 照此产出):

```markdown
# {标题}
> 来源:{url} · {时长/字数} · {日期}
## 一句话核心
{单句 30-80 字:只说最值得记住的一件事,不许两句拼接}
## 适合谁看
{1 条:具体前置基础 + 带数字的预计投入时长;禁写"适合所有人/初学者"}
## 核心要点
{4-6 条,每条 40-120 字,含上下文/因果/边界;关键术语后括注英文原词}
## 核心概念
{0-4 条:**术语 (English Term)** — ≤100 字解释;只收真正需要解释的}
## 常见误区
{0-3 条,只写素材明确纠正过的;没有就写"(素材未纠正常见误区)"}
## 学以致用
{1-3 条具体行动,今天就能做的那种}
## 原文金句
{0-3 条英文原句 + 中文翻译(个人版允许忠实引用,替代 n-gram 闸门)}
```

  口径保留:诚实不画饼、不确定的事实不补写不猜测;**不移植**:applyBySkill 技能树绑定、JSON 输出、n-gram 原创性闸门
- `digest.md`:网页用 WebFetch/浏览器读、PDF 用 Read;先问「全文翻译还是双语摘要?」(摘要默认走 notes 模板精简版:一句话核心 + 核心要点 + 金句)

**Steps:**
- [ ] **1. 写六份手册**(每份 60-120 行)→ **2. 自查**:手册中每条命令在本机敲一遍确认无 typo → **3. Commit**:`docs: 六份工具手册(移植 v2 笔记口径,适配个人版)`

### Task 8: SKILL.md 总入口 + 意图路由 + 双流水线

**Files:** Create: `SKILL.md`

**Interfaces:** frontmatter `name: shushu-study-toolbox`;`description` 一句话覆盖全部触发词(中英):「树树工具箱:下载视频、获取/翻译字幕、双语字幕嵌入、双语精华笔记、资料翻译总结。Use when the user wants to download/transcribe/translate videos or subtitles, embed bilingual subtitles, or turn videos/articles into bilingual study notes.」

**正文结构:**
1. 首次使用:跑 `doctor.py`;无 `config.json` 则按 `config.example.json` 引导逐项确认生成
2. 工具路由表:用户意图 → 对应 `tools/*.md`(明确指示:执行前必须读对应手册)
3. 两条流水线:**学习**(默认,"我要学这个视频" → subtitle → translate → notes)/**收藏**(学习 + download + embed);流水线产物统一进 `item_dir`
4. 总原则三条:脚本干机械活 agent 干语言活;不静默降级;报错说人话

**Steps:**
- [ ] **1. 写 SKILL.md → 2. 建软链接**:`ln -s ~/Desktop/shushu-study-toolbox ~/.claude/skills/shushu-study-toolbox` → **3. 新开会话验证触发**(说"帮我把这个视频做成双语笔记"看是否命中)→ **4. Commit**:`feat: SKILL.md 总入口(路由 + 学习/收藏流水线)`

### Task 9: CI 三平台矩阵

**Files:** Create: `.github/workflows/ci.yml`

**内容:** `on: [push, pull_request]`;matrix `os: [ubuntu-latest, macos-latest, windows-latest] × python: ["3.11", "3.13"]`;步骤:checkout → setup-python → `pip install -r requirements.txt` → `python scripts/doctor.py --json`(容忍 exit 1,只要 JSON 可解析)→ `pytest tests/ -v`。ubuntu 额外 `sudo apt-get install -y ffmpeg` 测"系统 ffmpeg 路径",其余平台走 static-ffmpeg 兜底路径——两条 ffmpeg 路径都被 CI 覆盖。

**Steps:**
- [ ] **1. 写 ci.yml → 2. 本地等价演练**:`pip install -r requirements.txt && pytest tests/ -v` 全绿 → **3. Commit**:`ci: 三平台矩阵(依赖安装 + doctor + 全量测试)`(推 GitHub 后自动首跑,见 Task 11)

### Task 10: 端到端真机验收(学习流水线)+ 截图采集

**Files:** 无新文件;产出 `<output_dir>/` 下第一个真实素材文件夹 + `docs/assets/` 截图若干

**Steps:**
- [ ] **1. 新开 Claude Code 会话**,甩一条真实英文短视频(5 分钟内、有官方字幕、CC 许可优先)说「我要学这个视频」
- [ ] **2. 验收清单**:字幕抓取 ✓ / 双语 SRT 时间轴与原文一致 ✓ / notes.md 符合模板(一句话核心为单句、要点 4-6 条、误区无据则空)✓ / 产物落在 `<output_dir>/<日期-slug>/` ✓
- [ ] **3. 再跑一次收藏流水线**(同视频):video.mp4 + video.subbed.mp4 可播放、字幕可开关 ✓
- [ ] **4. 采集截图**(供 README 与小红书):终端流水线过程 1 张、双语字幕播放效果 1 张、notes.md 成品 1 张,存 `docs/assets/`
- [ ] **5. 发现的问题现场修复并补测试,Commit**:`test: 端到端验收(学习+收藏流水线)+ 宣发截图`

---

## 阶段二:文档

### Task 11: README.md + LICENSE + GitHub 发布指南

**Files:** Create: `README.md`、`LICENSE`(MIT,版权人 Jim)、`docs/publish-to-github.md`

**README 结构(中文为主):** 顶部一段 English quick start → 是什么(一句话 + 六工具清单表,用「树树工具箱 - xx」显示名 + docs/assets 截图)→ 三步安装:①`git clone` ②`pip install -r requirements.txt && python3 scripts/doctor.py` ③复制配置 + 软链接(分 mac/Linux 的 `ln -s` 与 Windows 的 `mklink /D` 两种写法)→ 各 agent 接入(Claude Code / Codex / 其它:把 SKILL.md 路径挂进各自指令文件)→ 六工具各一条真实用法示例 → FAQ(Python 3.14 无 faster-whisper 怎么办 / 无官方字幕怎么办 / Windows 乱码排查)→ 合规声明(仅供个人学习,遵守各平台 ToS,不得用于传播下载内容)→ MIT

**publish-to-github.md(写给 Jim,复制即用):**

```bash
# 1. 确认登录(已装 gh)
gh auth status
# 2. 在 GitHub 建仓库并推送(公开)
cd ~/Desktop/shushu-study-toolbox
gh repo create shushu-study-toolbox --public --source=. --push
# 3. 补仓库描述与标签
gh repo edit --description "树树工具箱:把英文视频/资料变成双语学习笔记的 agent skill(下载/字幕/翻译/嵌入/笔记/总结)" \
  --add-topic claude-code --add-topic skill --add-topic bilingual --add-topic yt-dlp --add-topic whisper
# 4. 看 CI 是否三平台全绿
gh run watch
```

每步配一句"这步在干什么 + 成功长什么样";末尾附"以后怎么更新"(add/commit/push 三连)。**推送动作由 Jim 亲手执行**(发布行为),agent 只备好指南。

**Steps:**
- [ ] **1. 写三个文件 → 2. 自查**:照 README 三步在一个临时目录模拟陌生人安装(clone 本地路径即可),15 分钟内跑通 doctor → **3. Commit**:`docs: README(中英)+ MIT + GitHub 发布指南`

---

## 阶段三:宣发物料(等 Task 10 截图就位)

### Task 12: 小红书图文笔记

**Files:** Create: `marketing/xiaohongshu.md`

**内容(实拍素材来自 docs/assets,文案成稿而非提纲):**
- 标题 3 选 1(各 ≤20 字,含数字钩子),例:「我把看英文视频的全流程,做成了一个 AI skill」「甩个链接,3 分钟拿到双语字幕+学习笔记」「不用 API key 的双语学习工具箱,开源了」
- 正文 ≈300 字:痛点开场(英文视频想学但语言墙)→ 六工具一句话各介绍 → 「脚本干机械活,AI 干语言活,零 API key」差异点 → GitHub 仓库引导(评论区/主页)
- 配图清单 6 张:①封面(工具清单卡片图,文案给出排版要素)②终端流水线截图 ③双语字幕效果 ④notes.md 成品 ⑤配置文件一屏 ⑥安装三步图
- 话题标签:#AI工具 #Claude #英语学习 #开源 #效率工具 + 活动指定标签(留一行占位由 Jim 填活动 tag——活动名称只有 Jim 知道,这不是计划缺口)
- 合规自查:不出现"破解/免费下载视频"类表述

**Steps:**
- [ ] **1. 成稿 → 2. 通读一遍以小红书语感修一轮(短句、分段、emoji 适量)→ 3. Commit**:`docs: 小红书图文笔记(标题3选1+正文+配图清单)`

### Task 13: 3 分钟口播稿

**Files:** Create: `marketing/script-3min.md`

**结构(≈750 字中文,3 分钟口播节奏,含演示动作标注):**
- 0:00-0:20 钩子:「看英文教程视频,暂停查词十次就看不下去了?我做了个工具,甩个链接给 AI,双语字幕和学习笔记直接到手」
- 0:20-1:00 是什么:树树工具箱 = 6 个工具一个包;逐个一句话报菜名(下载/字幕/翻译/嵌入/笔记/总结)
- 1:00-2:10 实演(录屏配口播,演示节点标注在稿中):贴链接说"我要学这个" → 展示双语 SRT 生成 → 展示笔记成品(镜头停在"常见误区"和"学以致用")
- 2:10-2:40 三个差异点:零 API key(AI 就是你会话里这个)/ 全平台开源 / 15 分钟装好
- 2:40-3:00 行动号召:GitHub 搜 shushu-study-toolbox,README 中文,装完回来评论区交作业
- 每段标注:预计秒数、语气提示、画面/录屏内容

**Steps:**
- [ ] **1. 成稿 → 2. 朗读计时校准到 2:50-3:10 区间(每分钟 ≈250 字上限修剪)→ 3. Commit**:`docs: 3 分钟口播稿(含演示节点与时间轴)`

---

## 验证(端到端)

1. **单元/冒烟**:`pytest tests/ -v` 全绿(本机 + CI 三平台)
2. **能力验收**:Task 10 的学习/收藏双流水线真机跑通,产物齐全可播放
3. **触发验收**:新会话说「把这个视频做成双语笔记」能命中 skill 并按手册执行
4. **陌生人验收**:临时目录按 README 三步装到 doctor 通过 ≤15 分钟
5. **降级验收**:本机 Python 3.14 无 faster-whisper 时,五个非转写工具全部可用,subtitle 手册正确报「转写不可用 + venv 指引」

## 计划自审(已执行)

- 规格覆盖:设计文档全部小节均有对应任务(六工具 ✓ 配置 ✓ doctor ✓ 跨平台三保险 ✓ CI ✓ 错误处理口径进各手册 ✓ README/发布指南 ✓ 宣发两件 ✓);「不做什么」清单未被任何任务违反
- 占位符扫描:无 TBD;唯一留白是小红书活动标签,属 Jim 独有信息,已在文中注明
- 类型/命名一致性:`find_ffmpeg` 在 Task 2 定义、Task 6 复用;`exit 3`(无字幕)/`exit 4`(转写缺失)约定在 Task 4/5 定义、Task 7 手册消费;`meta.json` 字段 Task 4 定义、Task 7/10 消费——均一致
