# 树树工具箱 - 生成学习笔记

## 触发语示例

- 中文：「根据这份素材整理一篇能复习的学习笔记。」
- 中文：「用我的母语总结这个视频，关键术语保留英文。」
- English: "Turn this material into structured study notes."
- English: "Summarize it in my native language with bilingual key terms."

## 前置

- 只根据已经读取的网页、字幕、文档或 `meta.json` 写作。
- 优先读取通过校验的 `subs.bi.srt`，否则读取 `subs.orig.srt`。
- 以下代码块以 Bash/zsh 展示；PowerShell 请用实际值替换变量后逐条运行。
- 从 `config.json` 读取 `native_lang`；正文以该语言为主。
- 关键术语使用「母语译名 (English Term)」双语对照。
- 诚实不画饼；素材没有支持的事实不补写、不猜测。
- 不把常识、个人推断或宣传语伪装成原素材的结论。
- 本个人版只产出 Markdown。
- 不输出 JSON，不添加 applyBySkill 技能树绑定，不执行 n-gram 原创性闸门。

## 步骤

1. 设置素材目录，并查看当前母语配置：

```bash
ITEM_DIR="$HOME/ShushuStudy/2026-07-15-example"
python3 -c 'import sys; sys.path.insert(0,"scripts"); from common import load_config; print(load_config()["native_lang"])'
```

- exit 0：使用打印出的 `native_lang` 写正文。
- exit 1：配置无法读取；修复 `config.json` 后再写，不自行假定语言。

2. 读取现有来源信息和字幕，不只看标题就开始总结：

```bash
python3 -m json.tool "$ITEM_DIR/meta.json"
python3 scripts/srt_tools.py validate "$ITEM_DIR/subs.bi.srt"
```

- `meta.json` 不存在时，从原始来源记录标题、链接、时长/字数和日期。
- 字幕 validate exit 0 才作为可靠输入；exit 1 时先修字幕。
- 若只有 `subs.orig.srt`，校验并读取它；不要伪造不存在的译文。

3. 完整阅读素材后，严格按以下模板写入 `<ITEM_DIR>/notes.md`：

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

4. 写作时逐节执行以下约束：

- 「一句话核心」只允许一个完整句子，30–80 字，不用分号拼两句。
- 「适合谁看」写具体前置基础，并给带数字的预计投入时长。
- 「核心要点」写 4–6 条；每条 40–120 字，交代上下文、因果或边界。
- 「核心概念」只收确需解释的 0–4 个概念，每项不超过 100 字。
- 「常见误区」只收素材明确纠正的 0–3 条。
- 素材未纠正误区时，必须原样写：`(素材未纠正常见误区)`。
- 「学以致用」写 1–3 个今天即可执行、能够观察结果的动作。
- 「原文金句」只选素材中可核对的 0–3 句英文，并给忠实母语翻译。
- 若来源没有英文金句，该节可以为空，但不能回译后冒充英文原句。
- 数量为 0 的概念或金句保留标题，不编内容凑数。

5. 完稿后检查固定标题完整且文件非空：

```bash
python3 -c 'from pathlib import Path; import sys; p=Path(sys.argv[1]); t=p.read_text(encoding="utf-8"); hs=["## 一句话核心","## 适合谁看","## 核心要点","## 核心概念","## 常见误区","## 学以致用","## 原文金句"]; missing=[h for h in hs if h not in t]; print("缺失:"+",".join(missing) if missing else "结构检查通过"); raise SystemExit(bool(missing) or not t.strip())' "$ITEM_DIR/notes.md"
```

- exit 0：固定结构齐全；继续人工检查字数、条数与事实依据。
- exit 1：补齐缺失标题或内容，再运行一次。
- 结构检查通过不等于事实正确，必须回到素材逐条核验。

## 产物路径

- 学习笔记：`<ITEM_DIR>/notes.md`
- 来源元数据（若有）：`<ITEM_DIR>/meta.json`
- 优先证据字幕：`<ITEM_DIR>/subs.bi.srt`
- 备用证据字幕：`<ITEM_DIR>/subs.orig.srt`

## 常见故障

- 一句话核心出现两个句号：重写成只保留一件最值得记住的事。
- 「适合谁看」写成泛泛人群：补具体前置基础与数字时长。
- 核心要点像目录：补上为什么、何时成立以及适用边界。
- 术语只有译名：首次出现补英文原词，后续保持译法一致。
- 素材未谈误区却生成误区：删除并写固定占位句。
- 金句无法回查：删除；不确定的原文或事实不补写、不猜测。
- 输出变成 JSON 或技能树字段：按个人版 Markdown 模板重做。
