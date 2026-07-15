# 树树工具箱 - 翻译字幕

## 触发语示例

- 中文：「把这份英文 SRT 翻成中英双语字幕。」
- 中文：「继续上次没做完的字幕翻译，只补缺失分块。」
- English: "Translate this SRT into bilingual Chinese subtitles."
- English: "Resume the unfinished subtitle translation without redoing blocks."

## 前置

- 在仓库根目录运行，并确认 `scripts/srt_tools.py` 可用。
- 输入必须是 UTF-8 SRT，例如 `<ITEM_DIR>/subs.orig.srt`。
- 从 `config.json` 读取 `native_lang` 与 `subtitle_layout`；默认分别为 `zh`
  和 `original-top`，不得假定用户一定使用中文。
- 翻译由当前 agent 按块完成，不调用未获用户许可的外部服务。
- 质量口径：忠实原意、术语准确、口语字幕不逐字硬译、保留说话人语气。
- 一条原文必须对应一行译文；不能合并、拆分、加序号或留空行。

## 步骤

1. 设置本次路径；把示例目录替换为真实素材目录：

```bash
ITEM_DIR="$HOME/ShushuStudy/2026-07-15-example"
SOURCE_SRT="$ITEM_DIR/subs.orig.srt"
CHUNKS_DIR="$ITEM_DIR/translation-chunks"
```

2. 先校验原文时间轴：

```bash
python3 scripts/srt_tools.py validate "$SOURCE_SRT"
```

- exit 0：继续分块。
- exit 1：原文无法解析、内容为空或时间轴错误；修复前停止。
- exit 2：参数错误；核对文件路径后重试。

3. 按 40 条字幕一块生成源文本和 manifest：

```bash
python3 scripts/srt_tools.py chunk "$SOURCE_SRT" --size 40 --out-dir "$CHUNKS_DIR"
```

- exit 0：读取 `manifest.json`，逐块处理。
- exit 1：输入 SRT 无法读取或解析；停止并报告。
- exit 2：命令参数错误；`--size` 必须为正整数。
- 重跑 chunk 会刷新源文本和 manifest，但不会删除已有 `.zh.txt`。

4. 对每个 `chunk_NNN.txt` 生成同名 `chunk_NNN.zh.txt`。

- 每个源文件一行对应一条字幕；目标文件保持完全相同的行数与顺序。
- 忠实翻译说话者表达，不添加原文没有的结论、事实或解释。
- 术语使用稳定译法；首次需要时可保留英文原词帮助辨认。
- 口语按目标语言自然表达，不逐词机械映射，也不改掉情绪与语气。
- 不翻译时间码，因为分块文件中只有字幕正文。
- 不在译文前加项目符号、行号、引号或 Markdown 标记。
- 每完成一块，立刻做行数自查：

```bash
wc -l "$CHUNKS_DIR/chunk_000.txt" "$CHUNKS_DIR/chunk_000.zh.txt"
```

- 两个数字必须相同；再人工确认译文每行都非空。
- 若行数不符，只修正当前 `.zh.txt`，不要改源分块。

5. 中断后续跑时，先重新运行第 3 步，再读取 manifest。

- 只翻译缺少对应 `chunk_NNN.zh.txt` 的源块。
- 已存在且行数、非空检查通过的 `.zh.txt` 不重翻、不覆盖。
- 若源 SRT 已改变，旧译文可能错位；应另建 chunks 目录重新开始。
- 不能用“看起来都翻了”代替逐个文件核对。

6. 所有块完成后合并双语字幕；以下为原文在上：

```bash
python3 scripts/srt_tools.py merge "$SOURCE_SRT" --chunks-dir "$CHUNKS_DIR" --layout original-top --out "$ITEM_DIR/subs.bi.srt"
```

- 若配置为 `translation-top`，只把 `--layout` 改为 `translation-top`。
- exit 0：生成双语 SRT，继续最终校验。
- exit 2：manifest 损坏、缺块、行数不符或译文有空行；按提示只修故障块。
- exit 1 或其他退出码：停止并报告，不声称合并成功。

7. 最终校验双语字幕：

```bash
python3 scripts/srt_tools.py validate "$ITEM_DIR/subs.bi.srt"
```

- exit 0：报告字幕条数和布局，翻译流程完成。
- exit 1：解析、内容或时间轴未通过；不要进入视频封装。
- exit 2：参数错误；查看 `python3 scripts/srt_tools.py validate --help`。

## 产物路径

- 原文字幕：`<ITEM_DIR>/subs.orig.srt`
- 分块清单：`<ITEM_DIR>/translation-chunks/manifest.json`
- 源分块：`<ITEM_DIR>/translation-chunks/chunk_NNN.txt`
- 译文分块：`<ITEM_DIR>/translation-chunks/chunk_NNN.zh.txt`
- 最终双语字幕：`<ITEM_DIR>/subs.bi.srt`

## 常见故障

- `manifest 损坏`：重新运行 chunk；保留已验证的译文块再核对。
- `缺少第 N 块译文`：只补对应 `.zh.txt`，不要整批重翻。
- `行数不符`：一条原文对应一行译文，删除多余换行或补回缺行。
- `译文为空`：补译该行；空白占位不算有效翻译。
- 时间轴警告：翻译不应改时间码；检查是否使用了正确的原文 SRT。
- 术语前后不一致：统一目标语言译法，同时保留必要英文对照。
