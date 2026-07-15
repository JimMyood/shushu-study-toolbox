# 树树工具箱 - 翻译字幕

## 触发语示例

- 中文：「把这份英文 SRT 翻成中英双语字幕。」
- 中文：「继续上次没做完的字幕翻译，只补缺失分块。」
- English: "Translate this SRT into bilingual Chinese subtitles."
- English: "Resume the unfinished subtitle translation without redoing blocks."

## 前置

- 在仓库根目录运行，并确认 `scripts/srt_tools.py` 可用。
- 输入必须是 UTF-8 SRT，例如 `<ITEM_DIR>/subs.orig.srt`。
- 以下代码块以 Bash/zsh 展示；PowerShell 请用实际值替换变量后逐条运行。
- 复用本次 `common.py prepare` JSON 中的 `item_dir`、`native_lang` 与
  `subtitle_layout`；不假定用户一定使用中文，不硬编码字幕布局。
- 翻译由当前 agent 按块完成，不调用未获用户许可的外部服务。
- 质量口径：忠实原意、术语准确、口语字幕不逐字硬译、保留说话人语气。
- 一条原文必须对应一行译文；不能合并、拆分、加序号或留空行。

## 步骤

1. 流水线中复用已解析的 prepare JSON，不重复准备目录。只有独立启动、
   尚未 prepare 时才先运行：

```bash
TITLE='素材的真实标题'
python scripts/common.py prepare --title "$TITLE"
```

解析 stdout JSON，把 `item_dir`、`native_lang`、`subtitle_layout` 记为
`ITEM_DIR`、`NATIVE_LANG`、`SUBTITLE_LAYOUT`，并保留其余配置。下方路径
均基于同一个 `ITEM_DIR`：

```bash
SOURCE_SRT="$ITEM_DIR/subs.orig.srt"
CHUNKS_DIR="$ITEM_DIR/translation-chunks"
```

- 译文使用 `NATIVE_LANG`；只有用户明确要求本次临时覆盖时才换语言或布局。

2. 先校验原文时间轴：

```bash
python scripts/srt_tools.py validate "$SOURCE_SRT"
```

- exit 0：继续分块。
- exit 1：原文无法解析、内容为空、字幕为零时长或时间轴错误；修复前停止。
- exit 2：参数错误；核对文件路径后重试。

3. 按 40 条字幕一块生成源文本和 manifest：

```bash
python scripts/srt_tools.py chunk "$SOURCE_SRT" --size 40 --out-dir "$CHUNKS_DIR"
```

- exit 0：读取 `manifest.json`，逐块处理。
- exit 1：输入 SRT 无法读取、无法解析或没有字幕条目；停止并报告。空 SRT 不会
  覆盖已有 manifest 或分块。
- exit 2：命令参数错误；`--size` 必须为正整数。
- manifest 会记录原 SRT 与每个源分块的 SHA-256；合并时会重新核验，不能用旧
  manifest 配新源文。
- 重跑 chunk 会刷新源文本和 manifest。源文未变时保留已有译文；某一块源文变化
  时，只把该块旧译文改名保留为 `chunk_NNN.stale-<hash>.txt`，等待重翻。
- stale 名称使用原子“不覆盖”占位；若同名文件在并发窗口出现，竞争文件保持原样，
  工具自动改用 `-2`、`-3`。每个成功隔离的旧译文还会在命令打印的
  `.srt-safety-archive-*` 中保留一个私有硬链接归档；这是正常成功产物和公开 stale
  的并发保护载体，不代表命令失败，也不是可以自动清理的临时目录。
- 工具会在提交前复核公开 stale。隔离过程中若路径换包，命令停止且不更新清单；
  竞争文件保持原样，旧译文仍可从提示的私有安全归档恢复，必须人工核对。安全归档
  与公开 stale 指向同一文件内容，不要原地编辑任一方；如需修改，先复制到新文件。
- 上一版 manifest 没有 hash，无法证明 `.zh.txt` 与哪版源文绑定；重跑时会把所有
  旧译文条目以整批事务隔离为 `chunk_NNN.stale-legacy[-N].txt`，全部标记待重翻。
  任一条目失败会逆序恢复已经移动的条目，并保留私有安全归档防止 rollback 最后
  校验后的并发换包；即使源文看似未变也不得自动复用。

4. 对每个 `chunk_NNN.txt` 生成同名 `chunk_NNN.translated.txt`。

- 每个源文件一行对应一条字幕；目标文件保持完全相同的行数与顺序。
- 忠实翻译说话者表达，不添加原文没有的结论、事实或解释。
- 术语使用稳定译法；首次需要时可保留英文原词帮助辨认。
- 口语按目标语言自然表达，不逐词机械映射，也不改掉情绪与语气。
- 不翻译时间码，因为分块文件中只有字幕正文。
- 不在译文前加项目符号、行号、引号或 Markdown 标记。
- 每完成一块，立刻做行数自查：

```bash
python -c 'from pathlib import Path; import sys; a=Path(sys.argv[1]).read_text(encoding="utf-8").splitlines(); b=Path(sys.argv[2]).read_text(encoding="utf-8").splitlines(); ok=len(a)==len(b) and all(x.strip() for x in b); print(f"源文 {len(a)} 行，译文 {len(b)} 行，译文均非空：{ok}"); raise SystemExit(not ok)' "$CHUNKS_DIR/chunk_000.txt" "$CHUNKS_DIR/chunk_000.translated.txt"
```

- exit 0：行数相同且译文每行非空；继续下一块。
- exit 1：只修正当前 `.translated.txt` 的缺行、多余换行或空行，不改源分块。

5. 中断后续跑时，先重新运行第 3 步，再读取 manifest。

- 以 manifest 的 `needs_translation` 和实际文件为准，只翻译缺少对应
  `chunk_NNN.translated.txt` 的源块。
- 新版 manifest 中源 hash 未变且已有译文时，工具会安全续跑，不重翻、不覆盖。
- 若源 SRT 改变，工具只隔离受影响块的旧译文；`stale-<hash>.txt` 仅供人工参考，
  不得改回正式文件名冒充新译文。
- 翻完被隔离的块后写入新的 `.translated.txt`；可再跑一次 chunk，让 manifest
  的 `needs_translation` 状态更新为 `false`，然后合并。
- 不能用“看起来都翻了”代替逐个文件核对。

6. 所有块完成后，按 prepare JSON 中的布局合并双语字幕：

```bash
python scripts/srt_tools.py merge "$SOURCE_SRT" --chunks-dir "$CHUNKS_DIR" --layout "$SUBTITLE_LAYOUT" --out "$ITEM_DIR/subs.bi.srt"
```

- 不手工改写 `--layout`；它必须是本次 JSON 的真实值。
- exit 0：生成双语 SRT，继续最终校验。
- exit 2：源 hash/manifest 不匹配、缺块、行数不符、译文有空行，或输出与输入
  指向同一文件；按提示修复，不要绕过校验。
- exit 1 或其他退出码：停止并报告，不声称合并成功。

7. 最终校验双语字幕：

```bash
python scripts/srt_tools.py validate "$ITEM_DIR/subs.bi.srt"
```

- exit 0：报告字幕条数和布局，翻译流程完成。
- exit 1：解析、内容或时间轴未通过；不要进入视频封装。
- exit 2：参数错误；查看 `python scripts/srt_tools.py validate --help`。

## 产物路径

- 原文字幕：`<ITEM_DIR>/subs.orig.srt`
- 分块清单：`<ITEM_DIR>/translation-chunks/manifest.json`
- 源分块：`<ITEM_DIR>/translation-chunks/chunk_NNN.txt`
- 译文分块：`<ITEM_DIR>/translation-chunks/chunk_NNN.translated.txt`
- 被隔离旧译文：`<ITEM_DIR>/translation-chunks/chunk_NNN.stale-<hash>.txt`
- 旧译文私有安全归档：
  `<ITEM_DIR>/translation-chunks/.srt-safety-archive-*/*.preserved`
- 最终双语字幕：`<ITEM_DIR>/subs.bi.srt`

## 常见故障

- `manifest 损坏`：源 SRT、源分块或 hash 与清单不一致；重新运行 chunk，再按
  `needs_translation` 补译。不要手改 hash。
- `缺少第 N 块译文`：只补对应 `.translated.txt`，不要整批重翻。
- 出现 `stale-<hash>.txt`：源文已改变；旧译文已保留但不会参与合并，重翻正式
  `.translated.txt` 后继续。命令同时打印私有安全归档路径；它是防止公开 stale 被
  并发替换后丢失旧译文的长期保护载体，请勿写入、移动或自动删除。
- 旧版 `manifest.json` 与 `.zh.txt`：禁止直接合并，因为没有 hash 就无法证明译文
  对应当前源文。重新运行 chunk；普通文件会进入整批事务隔离，随后重翻全部正式
  `.translated.txt`。符号链接或目录会被明确拒绝并原样保留，不跟随、不搬动；先
  人工移走这些路径条目再重试。
- `无法隔离旧版译文`：隔离在建立首个 stale 前失败，manifest、源分块和旧
  `.zh.txt` 保持原样；修复目录权限后重试。
- `旧版译文只完成了部分隔离`：隔离失败且自动回滚也受阻；manifest 与源分块仍未
  更新；可见旧译文路径已尽力回滚，私有归档会保留以消除 rollback 的末次清理
  竞态。不要继续翻译或合并；逐项核对 `.zh.txt`、`stale-legacy` 与提示的
  `.srt-safety-archive-*`。竞争文件不得删除或覆盖；确认内容归属、修复权限后再运行
  chunk。
- `行数不符`：一条原文对应一行译文，删除多余换行或补回缺行。
- `译文为空`：补译该行；空白占位不算有效翻译。
- 时间轴警告：翻译不应改时间码；检查是否使用了正确的原文 SRT。
- 术语前后不一致：统一目标语言译法，同时保留必要英文对照。
