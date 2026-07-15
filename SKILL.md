---
name: shushu-study-toolbox
description: "树树工具箱:下载视频、获取/翻译字幕、双语字幕嵌入、双语精华笔记、资料翻译总结。Use when the user wants to download/transcribe/translate videos or subtitles, embed bilingual subtitles, or turn videos/articles into bilingual study notes."
---

# 树树工具箱

把本文件所在的仓库根目录作为工作目录。先确定一份素材的 `item_dir`，再按用户意图调用工具；始终把同一任务的产物写入这个目录。

## 首次使用

1. 运行环境自检：

   ```bash
   python scripts/doctor.py
   ```

2. 根据本次任务判断自检结果。exit 0 表示全部检查通过；exit 1 表示至少一项未通过，不代表所有能力都不可用。仅 `faster-whisper` 失败时，只停止本地转写，下载、来源字幕、翻译、嵌字幕、笔记和资料精读仍可按各自依赖继续。完整阅读目标工具手册，逐项处理本次任务真正需要的依赖。
3. 检查仓库根目录是否已有 `config.json`。如果没有，完整读取 `config.example.json`，依次向用户确认 `output_dir`、`native_lang`、`source_lang`、`whisper_model`、`video_quality` 和 `subtitle_layout`，再以模板生成 `config.json`。不要擅自采用默认值，也不要覆盖已有配置。
4. 确定本次素材的真实标题后，必须调用统一入口（`--date`
   省略时使用当天）：

   ```bash
   python scripts/common.py prepare --title "$TITLE"
   ```

   需要指定非当天日期时，再加 `--date 2026-07-15`。

5. 只把 stdout 当作 JSON 解析，读取并记住 `item_dir`、`output_dir`、
   `native_lang`、`source_lang`、`whisper_model`、`video_quality` 和
   `subtitle_layout`。该命令已通过 `common.item_dir` 创建
   `<output_dir>/<YYYY-MM-DD>-<slug>/`；不要自己拼路径或再运行 `mkdir`。
   后续每份工具手册都复用这一份 JSON 和同一个 `item_dir`。
   除非用户明确要求本次临时覆盖，所有命令都必须传入返回的真实配置值。

## 工具路由

执行任何工具前，必须完整阅读对应手册；执行流水线时，按顺序完整阅读涉及的每一份手册。

| 用户意图 | 必须阅读的手册 |
| --- | --- |
| 下载完整视频或仅音频 | [`tools/download.md`](tools/download.md) |
| 获取来源字幕，或经确认后本地转写 | [`tools/subtitle.md`](tools/subtitle.md) |
| 翻译字幕并生成双语 SRT | [`tools/translate.md`](tools/translate.md) |
| 把字幕软封装或硬烧录进视频 | [`tools/embed.md`](tools/embed.md) |
| 把视频或字幕整理成双语学习笔记 | [`tools/notes.md`](tools/notes.md) |
| 精读、翻译或总结网页与 PDF | [`tools/digest.md`](tools/digest.md) |

## 流水线

### 学习流水线（默认）

用户说“我要学这个视频”或表达同类意图时，依次执行：

1. `subtitle`：获取来源字幕；没有目标字幕时，先估时并征得用户确认，才能本地转写。
2. `translate`：翻译并校验双语字幕。
3. `notes`：基于已校验素材生成学习笔记。

### 收藏流水线

用户还要保存可离线观看的成品时，依次执行：

1. `download`：下载视频到同一个 `item_dir`。
2. `subtitle`：获取或经确认后转写字幕。
3. `translate`：生成双语字幕。
4. `notes`：生成学习笔记。
5. `embed`：询问软字幕或硬字幕，再输出收藏视频。

不要为每一步另建互不关联的目录；所有输入、中间文件和最终产物都复用本次 `item_dir`。

## 总原则

1. 让脚本处理下载、切块、校验、封装等机械工作；让 agent 处理翻译、总结和表达等语言工作。
2. 不静默降级；需要改用转写、浏览器、其他语言或其他模式时，先说明原因并按对应手册取得确认。
3. 用人话报告错误：说明失败在哪一步、实际退出码、已产生的文件和下一步修复方式，不把失败包装成成功。
