# 树树工具箱 - 获取字幕

## 触发语示例

- 中文：「帮我拿到这个视频的中文字幕。」
- 中文：「这个视频没有字幕的话，问过我再本地转写。」
- English: "Get the subtitles for this video in English."
- English: "If captions are unavailable, ask before transcribing locally."

## 前置

- 仅处理用户有权访问和用于个人学习的素材，并遵守平台服务条款。
- 在仓库根目录运行；先执行 `python scripts/doctor.py`。
- 以下代码块以 Bash/zsh 展示；PowerShell 请用实际值替换变量后逐条运行。
- 准备来源链接与真实标题；字幕语言、转写模型和素材目录必须来自
  `common.py prepare` 返回的 JSON。
- 语言代码示例：英文 `en`、简体中文 `zh-Hans`；以来源实际提供为准。
- 先尝试来源字幕；禁止在 exit 3 后静默下载音频或启动转写。
- 转写估时使用配置模型的粗估系数；当前 tiny/small/medium 均先用
  `ceil(视频秒数 × 0.3 ÷ 60)` 分钟作保守提示。
- 视频时长只能读来源页面或已有 `meta.json`；不确定时不要猜。

## 步骤

1. 先确定本流水线是否已经取得 `common.py prepare` 的 JSON。

- 本流水线已有 prepare JSON：直接复用其中全部七个字段，跳到第 2 步；
  不因跨午夜或来源标题变化再次 prepare。
- 只有独立启动、尚未 prepare 时，才设置真实链接和标题并调用统一配置入口：

```bash
URL='https://example.com/video'
TITLE='素材的真实标题'
python scripts/common.py prepare --title "$TITLE"
```

- exit 0：只解析 stdout JSON，必须读取 `item_dir`、`output_dir`、
  `native_lang`、`source_lang`、`whisper_model`、`video_quality` 和
  `subtitle_layout` 全部七个字段。本手册把 `item_dir`、`source_lang`
  和 `whisper_model` 分别记为 `ITEM_DIR`、`SOURCE_LANG` 和
  `WHISPER_MODEL`，其余配置原样保留给后续流水线。
- exit 1/2：修复配置、路径或参数后重试，不手工 `mkdir`。
- 默认使用当天；需要指定日期时加 `--date 2026-07-15`。
- 除非用户明确要求本次临时覆盖，下方命令必须使用这些真实配置值。

2. 优先抓取来源字幕（官方字幕优先，其次来源自动字幕）：

```bash
python scripts/fetch.py subs "$URL" --lang "$SOURCE_LANG" --out "$ITEM_DIR"
```

3. 必须按退出码分支，不能把失败当成功：

- exit 0：确认 `<ITEM_DIR>/subs.orig.srt` 存在，进入第 8 步校验。
- exit 1：报告网络、权限、路径等人话错误，停止并请用户处理。
- exit 2：参数有误；核对 `subs URL --lang LANG --out DIR` 后重试。
- exit 3：没有目标语言字幕；严格执行第 4 步，禁止自动降级。
- 其他 exit code：停止，把退出码和屏幕错误告诉用户。

4. exit 3 时，先从来源页面或已有元数据取得可靠视频时长并计算 X。

```bash
python -c 'import json,math,sys; sys.excepthook=lambda *_: print("无法从 meta.json 读取有效的正数 duration_s；请改用浏览器读取来源时长。",file=sys.stderr); d=json.load(open(sys.argv[1],encoding="utf-8"))["duration_s"]; d=d if type(d) in (int,float) and math.isfinite(d) and d>0 else (_ for _ in ()).throw(ValueError()); print(math.ceil(d*0.3/60))' "$ITEM_DIR/meta.json"
```

- 只有已有 `meta.json` 时才直接复制上面的命令。
- exit 0：输出正整数 X；再原样问「无官方字幕，转写约需 X 分钟，继续吗？」
- exit 1：缺字段、非数值、非有限值或非正数；中文报错且不泄露 traceback。
- exit 1 或没有元数据时，改用浏览器读取来源标注时长并按同一公式计算。
- 浏览器也无法取得时，问「无法可靠估时；仍要下载音频并本地转写吗？」
- 不得把 0、NaN、缺失字段或猜测值填进 X。
- 在用户明确回答继续之前，不能运行 audio 或 transcribe 命令。

5. 用户确认后才下载音频：

```bash
python scripts/fetch.py audio "$URL" --out "$ITEM_DIR"
```

- exit 0：确认 `audio.m4a` 存在，再开始本地转写。
- exit 1：报告下载错误并停止；不要转交外部转写服务。
- exit 2：修正命令参数；其他退出码同样停止并报告。

6. 用 JSON 中的本地模型和源语言转写；`SOURCE_LANG=auto` 时由
   faster-whisper 自动识别：

```bash
python scripts/transcribe.py "$ITEM_DIR/audio.m4a" --model "$WHISPER_MODEL" --lang "$SOURCE_LANG" --out "$ITEM_DIR/subs.orig.srt"
```

7. 本地转写的分支处理：

- exit 0：确认屏幕先给出预计耗时，随后生成 `subs.orig.srt`。
- exit 1：音频、ffprobe、模型或写入失败；按人话错误修复后再试。
- exit 2：参数错误；核对输入文件及 `--model`、`--lang`、`--out`。
- exit 4：当前 Python 没有可用的 faster-whisper；把屏幕中的 Python 3.13
  安装与 venv 指引告诉用户，停止；绝不伪造字幕或转交未获许可的服务。
- 其他 exit code：停止，并报告实际退出码。

8. 无论抓取还是转写，最终都校验 SRT：

```bash
python scripts/srt_tools.py validate "$ITEM_DIR/subs.orig.srt"
```

- exit 0：报告字幕条数，流程完成。
- exit 1：SRT 解析、空字幕或时间轴错误，不能进入翻译。
- exit 2：命令参数错误，先查看 `python scripts/srt_tools.py --help`。

## 产物路径

- 来源字幕或转写字幕：`<ITEM_DIR>/subs.orig.srt`
- 转写中间音频：`<ITEM_DIR>/audio.m4a`
- 已有完整下载时的时长依据：`<ITEM_DIR>/meta.json`
- exit 3 且用户未确认时，不应新增 `audio.m4a` 或转写字幕。

## 常见故障

- `未找到 LANG 字幕`：这是 exit 3；先估时并询问，不能静默转写。
- 可用语言与请求语言不同：请用户选择，不擅自换语言。
- `faster-whisper` 不可用：按 exit 4 的 Python 3.13 指引处理。
- `ffprobe` 无法读取：确认音频可播放并检查 ffmpeg/ffprobe。
- 首次模型加载失败：保持网络畅通以下载模型后重试。
- SRT 校验失败：保留原文件用于排查，不继续翻译或封装。
