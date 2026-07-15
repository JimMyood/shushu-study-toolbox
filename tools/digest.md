# 树树工具箱 - 精读网页与 PDF

## 触发语示例

- 中文：「精读这个网页，给我一份双语摘要。」
- 中文：「把这份 PDF 全文翻译成中文。」
- English: "Read this article closely and give me a bilingual digest."
- English: "Translate this PDF in full into my native language."

## 前置

- 只处理用户提供或有权访问的网页、PDF 与个人学习用途内容。
- 遵守来源版权与访问条款；不要绕过登录、付费墙或访问控制。
- 以下代码块以 Bash/zsh 展示；PowerShell 请用实际值替换变量后逐条运行。
- 从 `config.json` 读取 `native_lang`，正文以该语言为主。
- 网页使用 agent 的 WebFetch 或浏览器读取；动态页面优先浏览器。
- PDF 使用 Read 读取正文和相关页面；不要只根据文件名或摘要作答。
- 不确定的事实不补写、不猜测；读不到的内容明确标注限制。
- 开始产出前必须先让用户选择模式，不替用户默选全文翻译。

## 步骤

1. 收到链接或 PDF 后，先原样询问：

> 全文翻译还是双语摘要？

- 用户选择前不开始大段翻译或总结。
- 若用户明确说「摘要」但未指定细度，使用第 5 步的精简模板。
- 若用户已经在同一句明确选择，可直接执行，不重复提问。

2. 设置本次素材目录并读取母语配置：

```bash
ITEM_DIR="$HOME/ShushuStudy/2026-07-15-example"
python3 -c 'from pathlib import Path; import sys; Path(sys.argv[1]).mkdir(parents=True, exist_ok=True)' "$ITEM_DIR"
python3 -c 'import sys; sys.path.insert(0,"scripts"); from common import load_config; print(load_config()["native_lang"])'
```

- exit 0：使用打印出的 `native_lang`。
- exit 1：修复 `config.json` 后继续；不擅自假定目标语言。

3. 网页输入的读取规则：

- 先用 WebFetch 获取正文、标题、作者、发布日期与原始 URL。
- WebFetch 只拿到壳页面、内容残缺或需要交互时，改用浏览器读取。
- 浏览器仍遇到登录或付费墙时停止，说明哪些内容未能访问。
- 排除导航、广告、推荐列表和评论，保留正文层级与必要图注。
- 只使用当前 agent 已有的读取和写作能力，不引入额外服务。

4. PDF 输入的读取规则：

- 用 Read 阅读正文，并覆盖完成任务所需的全部页面。
- 保留章节、列表、表格标题、图注及脚注与正文的对应关系。
- 扫描件无法提取文字时，报告需 OCR 或更清晰版本，不猜内容。
- 页数很多时先报告范围，再分段推进并记录已经完成的页码。
- 引文附页码；无法确认页码时明确写「页码未确认」。

5. 用户选择「双语摘要」时，按 notes 模板精简版写：

```markdown
# {标题}
> 来源:{url 或 PDF 文件名} · {字数/页数} · {日期}
## 一句话核心
{以 native_lang 写一个 30-80 字单句，只保留最值得记住的一件事}
## 核心要点
{4-6 条母语要点，每条保留必要的 English Term 双语对照}
## 原文金句
{0-3 条可核对原句 + native_lang 忠实翻译；PDF 引文附页码}
```

- 摘要只使用「一句话核心 + 核心要点 + 原文金句」三节。
- 关键术语首次出现用母语译名加英文原词，之后保持一致。
- 来源没有合适金句时保留标题但不凑句子。

6. 用户选择「全文翻译」时：

- 保留原文标题、段落、列表与章节顺序，不删掉限定词或反例。
- 以 `native_lang` 给出完整译文；关键术语首次出现保留英文原词。
- 表格按行列关系重建；无法可靠重建时逐项说明，不调换数值。
- 长文分段写入同一文件，并标记翻译到的章节或 PDF 页码。
- 翻译口吻自然但忠实，不把摘要、点评或新事实混进正文。

7. 将结果保存为 Markdown 后检查文件：

```bash
python3 -c 'from pathlib import Path; import sys; p=Path(sys.argv[1]); ok=p.is_file() and p.stat().st_size>0; print("文件存在且非空" if ok else "文件缺失或为空"); raise SystemExit(not ok)' "$ITEM_DIR/digest.md"
```

- exit 0：文件存在且非空，再向用户报告读取范围与模式。
- exit 1：未生成有效文件；继续写入或说明读取阻塞，不能报完成。

8. 双语摘要额外检查三个固定标题：

```bash
python3 -c 'from pathlib import Path; import sys; lines=Path(sys.argv[1]).read_text(encoding="utf-8").splitlines(); hs=["## 一句话核心","## 核心要点","## 原文金句"]; ok=all(lines.count(h)==1 for h in hs); print("摘要结构通过" if ok else "摘要结构缺失或重复"); raise SystemExit(not ok)' "$ITEM_DIR/digest.md"
```

- exit 0：仍需人工确认三个标题各出现一次并核对引用。
- exit 1：缺少固定结构，按第 5 步补齐后再交付。

## 产物路径

- 网页或 PDF 精读结果：`<ITEM_DIR>/digest.md`
- 全文翻译与双语摘要使用同一路径，但文件开头应标明所选模式。
- 原始 PDF 保留在用户给定位置；未经允许不移动或删除。

## 常见故障

- WebFetch 内容残缺：改用浏览器；仍失败就说明访问边界。
- 网页需要登录或付费：请用户提供有权访问的内容，不绕过限制。
- PDF 是扫描件：请求可检索文本或 OCR 版本，不根据模糊图像猜字。
- 长文中断：记录完成的章节/页码，续跑只补未完成部分。
- 术语前后不一致：以首次确认的双语术语表统一全文。
- 金句找不到原文：删除或标为无法核对，不能伪造引文。
- 用户未选模式：先问「全文翻译还是双语摘要？」再继续。
