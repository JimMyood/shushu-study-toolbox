# 树树工具箱 - 视频嵌字幕

## 触发语示例

- 中文：「把双语字幕封装进视频，保留可开关字幕。」
- 中文：「把字幕直接烧进画面，导出一个到处都能看的视频。」
- English: "Mux these bilingual subtitles into the video as a soft track."
- English: "Burn the subtitles permanently into the video."

## 前置

- 在仓库根目录运行，并先执行 `python3 scripts/doctor.py`。
- 准备 `<ITEM_DIR>/video.mp4` 与通过校验的 SRT 字幕。
- 以下代码块以 Bash/zsh 展示；PowerShell 请用实际值替换变量后逐条运行。
- 优先使用 `<ITEM_DIR>/subs.bi.srt`；只有用户明确要单语时才换文件。
- 先问用户选择软字幕还是硬字幕，不替用户决定最终观看方式。
- 软字幕可开关、处理快；播放器必须支持 MP4 的 mov_text 字幕流。
- 硬字幕不可关闭、需重编码，耗时约与视频时长相当。
- 硬字幕会自动选择当前系统中存在且可读的常见中文字体，并打印名称与路径。
- 输出文件必须与输入视频和字幕不同名，避免覆盖源文件。

## 步骤

1. 设置素材目录与输入路径：

```bash
ITEM_DIR="$HOME/ShushuStudy/2026-07-15-example"
VIDEO="$ITEM_DIR/video.mp4"
SUBTITLE="$ITEM_DIR/subs.bi.srt"
```

2. 先校验字幕，校验失败时禁止封装：

```bash
python3 scripts/srt_tools.py validate "$SUBTITLE"
```

- exit 0：字幕可解析，继续。
- exit 1：空内容、时间轴或 SRT 格式有误；返回翻译流程修复。
- exit 2：参数错误；核对字幕路径后重试。
- 其他 exit code：停止并报告实际退出码。

3. 用户选择可开关的软字幕时运行：

```bash
python3 scripts/mux.py soft "$VIDEO" "$SUBTITLE" --out "$ITEM_DIR/video.soft.mp4"
```

4. 软字幕命令的分支处理：

- exit 0：生成并验证了含字幕流的 MP4，报告播放器中需手动开启字幕。
- exit 1：输入缺失、ffmpeg/ffprobe 不可用、封装或验证失败；按提示修复。
- exit 2：参数错误；核对顺序为 `soft VIDEO SUBTITLE --out OUTPUT`。
- 其他 exit code：停止，不把旧的同名输出当作本次结果。

5. 用户明确选择永久显示的硬字幕时，先提示会重编码，再运行：

```bash
python3 scripts/mux.py burn "$VIDEO" "$SUBTITLE" --out "$ITEM_DIR/video.burn.mp4"
```

- 默认字体不合适时，可显式覆盖已安装的中文字体：

```bash
python3 scripts/mux.py burn "$VIDEO" "$SUBTITLE" --font "Noto Sans CJK SC" --out "$ITEM_DIR/video.burn.mp4"
```

- `--font` 必须是系统能精确匹配、当前用户可读且含中文字形的字体；名称不能包含逗号、等号或引号。

6. 硬字幕命令的分支处理：

- exit 0：报告 `video.burn.mp4`；抽查画面确认字幕确实可见。
- exit 1：检查视频、UTF-8 SRT、ffmpeg、subtitles/libass 滤镜和屏幕字体提示。
- 报告没有可读中文字体时，安装提示中的字体，或用 `--font` 指定另一款已安装字体。
- exit 2：参数错误；核对顺序为 `burn VIDEO SUBTITLE --out OUTPUT`。
- 其他 exit code：停止并报告，不声称重编码完成。

7. 检查最终文件存在且非空：

```bash
python3 -c 'from pathlib import Path; import sys; p=Path(sys.argv[1]); ok=p.is_file() and p.stat().st_size>0; print("文件存在且非空" if ok else "文件缺失或为空"); raise SystemExit(not ok)' "$ITEM_DIR/video.soft.mp4"
```

- 上面用于软字幕产物；硬字幕时把文件名改为 `video.burn.mp4`。
- exit 0：文件存在且非空。
- exit 1：验收失败；回看 mux 命令输出，不复用残留文件。

8. 若系统有 ffprobe，可查看软字幕流：

```bash
ffprobe -v error -select_streams s -show_entries stream=codec_name -of default=nw=1 "$ITEM_DIR/video.soft.mp4"
```

- exit 0 且输出字幕 codec：软字幕流可见。
- 非 0：报告检查失败；`mux.py soft` 本身也会执行必要验证。

## 产物路径

- 输入视频：`<ITEM_DIR>/video.mp4`
- 输入双语字幕：`<ITEM_DIR>/subs.bi.srt`
- 软字幕视频：`<ITEM_DIR>/video.soft.mp4`
- 硬字幕视频：`<ITEM_DIR>/video.burn.mp4`
- 保留输入文件，除非用户另行明确要求清理。

## 常见故障

- `找不到输入视频/SRT`：核对素材目录与文件名，不创建空占位文件。
- `未找到 ffmpeg`：按 doctor 的当前平台修复指引安装或启用静态兜底。
- `未找到 ffprobe`：需要带 ffprobe 的完整 ffmpeg 安装。
- 软字幕看不见：先在播放器中开启字幕轨；必要时换支持 mov_text 的播放器。
- 硬烧录失败：确认 ffmpeg 含 subtitles/libass、SRT 为 UTF-8，并查看自动选择的字体。
- 中文显示方框：用 `--font "Arial Unicode MS"`、`Microsoft YaHei` 或 `Noto Sans CJK SC` 重试；工具会拒绝不存在、不可读或缺少中文字形的名称，并在 ffmpeg 报告缺字时停止发布输出。
- 路径含空格或特殊字符：始终保留命令中的双引号，脚本会继续转义滤镜路径。
- 输出与输入同名：换独立输出名；工具会拒绝覆盖输入文件。
