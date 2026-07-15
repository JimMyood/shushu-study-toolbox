"""使用 ffmpeg 把 SRT 字幕封装进视频或烧录到画面。"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Callable
from typing import Sequence

from doctor import find_ffmpeg


class MuxError(Exception):
    """可安全展示给命令行用户的视频字幕处理错误。"""


class _ChineseArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(
            2,
            f"{self.prog}: 参数错误：{message}。"
            "请检查命令参数后重试。\n",
        )


def _paths_refer_to_same_file(first: Path, second: Path) -> bool:
    try:
        if first.resolve() == second.resolve():
            return True
    except (OSError, RuntimeError, ValueError):
        pass
    try:
        return first.samefile(second)
    except (OSError, ValueError):
        return False


def _prepare_paths(
    video: Path, subtitle: Path, output: Path
) -> tuple[Path, Path, Path]:
    video = Path(video)
    subtitle = Path(subtitle)
    output = Path(output)
    try:
        video_exists = video.is_file()
    except OSError:
        video_exists = False
    if not video_exists:
        raise MuxError(
            f"找不到输入视频：{video}。"
            "请确认路径指向现有的视频文件后重试。"
        )
    try:
        subtitle_exists = subtitle.is_file()
    except OSError:
        subtitle_exists = False
    if not subtitle_exists:
        raise MuxError(
            f"找不到 SRT 字幕：{subtitle}。"
            "请确认路径指向现有的 SRT 文件后重试。"
        )
    if (
        _paths_refer_to_same_file(video, output)
        or _paths_refer_to_same_file(subtitle, output)
    ):
        raise MuxError(
            "输出路径与输入视频或 SRT 字幕是同一文件，不能覆盖源文件。"
            "请为 --out 指定另一个 MP4 文件。"
        )
    try:
        output_exists = output.exists()
        output_is_file = output.is_file()
    except OSError as error:
        raise MuxError(
            f"无法检查视频输出路径：{output}。"
            "请检查路径和访问权限后重试。"
        ) from error
    if output_exists and not output_is_file:
        raise MuxError(
            f"视频输出路径不是普通文件：{output}。"
            "请为 --out 指定一个 MP4 文件路径。"
        )
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as error:
        raise MuxError(
            f"无法创建视频输出目录：{output.parent}。"
            "请检查目录路径、权限和可用空间后重试。"
        ) from error
    return video, subtitle, output


def _find_ffprobe(ffmpeg_path: str) -> str:
    ffmpeg = Path(ffmpeg_path)
    probe_name = "ffprobe.exe" if ffmpeg.name.lower().endswith(".exe") else "ffprobe"
    sibling = ffmpeg.with_name(probe_name)
    if sibling.is_file():
        return str(sibling)
    system_ffprobe = shutil.which("ffprobe")
    if system_ffprobe:
        return system_ffprobe
    raise FileNotFoundError("未找到 ffprobe")


def _run_atomic_ffmpeg(
    output: Path,
    command_prefix: list[str],
    failure_message: str,
    verify: Callable[[Path], None] | None = None,
    stderr_failure_markers: Sequence[str] = (),
    stderr_failure_message: str | None = None,
) -> None:
    temporary_path = None
    try:
        try:
            with tempfile.NamedTemporaryFile(
                dir=output.parent,
                prefix=f".{output.stem}.",
                suffix=".mp4",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
        except (OSError, ValueError) as error:
            raise MuxError(
                f"无法在视频输出目录创建临时 MP4：{output.parent}。"
                "请检查目录权限和可用空间后重试。"
            ) from error

        command = [*command_prefix, "-y", str(temporary_path)]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as error:
            raise MuxError(failure_message) from error
        stderr = (result.stderr or "").casefold()
        if any(
            marker.casefold() in stderr
            for marker in stderr_failure_markers
        ):
            raise MuxError(stderr_failure_message or failure_message)
        if result.returncode != 0:
            raise MuxError(failure_message)

        try:
            output_size = temporary_path.stat().st_size
        except OSError as error:
            raise MuxError(failure_message) from error
        if output_size <= 0:
            raise MuxError(failure_message)
        if verify is not None:
            verify(temporary_path)

        try:
            temporary_path.replace(output)
        except OSError as error:
            raise MuxError(
                f"无法写入视频输出文件：{output}。"
                "请检查路径、权限和可用空间后重试。"
            ) from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _verify_subtitle_stream(ffmpeg_path: str, video_path: Path) -> None:
    try:
        ffprobe = _find_ffprobe(ffmpeg_path)
    except FileNotFoundError as error:
        raise MuxError(
            "未找到 ffprobe，无法确认字幕流。"
            "请安装带 ffprobe 的 ffmpeg 后重试。"
        ) from error
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as error:
        raise MuxError(
            "ffprobe 无法运行，无法确认字幕流。"
            "请检查 ffmpeg 安装后重试。"
        ) from error
    if result.returncode != 0:
        raise MuxError(
            "ffprobe 无法读取生成的视频，无法确认字幕流。"
            "请检查 ffmpeg 安装和输入文件后重试。"
        )
    if "subtitle" not in result.stdout.split():
        raise MuxError(
            "生成的视频中未检测到字幕流。"
            "请确认 SRT 字幕格式正确后重试。"
        )


def soft(video: Path, subtitle: Path, output: Path) -> None:
    """无重编码地把 SRT 作为 mov_text 字幕流写入 MP4。"""
    video, subtitle, output = _prepare_paths(video, subtitle, output)
    ffmpeg, _source = find_ffmpeg()
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(video),
        "-i",
        str(subtitle),
        "-map",
        "0",
        "-map",
        "1:0",
        "-c",
        "copy",
        "-c:s",
        "mov_text",
    ]
    _run_atomic_ffmpeg(
        output,
        command,
        "软字幕封装失败：ffmpeg 未能生成有效的 MP4。"
        "请确认输入视频与 SRT 字幕可用后重试。",
        verify=lambda path: _verify_subtitle_stream(ffmpeg, path),
    )


def _escape_subtitle_filter_path(path: str | Path) -> str:
    """按 FFmpeg option + filtergraph 两层语法转义字幕路径。"""
    option_escaped = "".join(
        f"\\{character}" if character in "\\':" else character
        for character in str(path)
    )
    return "".join(
        f"\\{character}" if character in "\\'[],;" else character
        for character in option_escaped
    )


_UNSAFE_FONT_NAME_CHARS = frozenset("\\':,[]=;")


def _validated_font_name(value: str) -> str:
    name = value.strip()
    if not name or any(
        character in _UNSAFE_FONT_NAME_CHARS
        or ord(character) < 32
        or ord(character) == 127
        for character in name
    ):
        raise ValueError("unsafe font name")
    return name


def _font_name(value: str) -> str:
    try:
        return _validated_font_name(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "字体名称不能为空，且不能包含逗号、等号、引号、"
            "反斜杠、方括号、分号或控制字符"
        ) from None


def _font_candidates() -> list[tuple[str, Path]]:
    windows_fonts = (
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    )
    return [
        (
            "Arial Unicode MS",
            Path("/Library/Fonts/Arial Unicode.ttf"),
        ),
        (
            "Arial Unicode MS",
            Path(
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
            ),
        ),
        (
            "Hiragino Sans GB",
            Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        ),
        ("Microsoft YaHei", windows_fonts / "msyh.ttc"),
        ("Microsoft YaHei", windows_fonts / "msyhbd.ttc"),
        ("SimHei", windows_fonts / "simhei.ttf"),
        ("Arial Unicode MS", windows_fonts / "arialuni.ttf"),
        (
            "Noto Sans CJK SC",
            Path(
                "/usr/share/fonts/opentype/noto/"
                "NotoSansCJK-Regular.ttc"
            ),
        ),
        (
            "Noto Sans CJK SC",
            Path(
                "/usr/share/fonts/opentype/noto/"
                "NotoSansCJKsc-Regular.otf"
            ),
        ),
        (
            "Noto Sans CJK SC",
            Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"),
        ),
        (
            "WenQuanYi Zen Hei",
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        ),
    ]


def _font_file_is_readable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.R_OK)
    except OSError:
        return False


def _fontconfig_match(font_name: str) -> tuple[str, Path] | None:
    executable = shutil.which("fc-match")
    if executable is None:
        return None
    try:
        with tempfile.TemporaryDirectory(
            prefix=".mux-font-cache-"
        ) as cache_dir:
            environment = os.environ.copy()
            environment["XDG_CACHE_HOME"] = cache_dir
            result = subprocess.run(
                [
                    executable,
                    "--format",
                    "%{family}\t%{file}\n",
                    f"{font_name}:charset=4e2d",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                env=environment,
            )
    except (OSError, ValueError):
        return None
    if result.returncode != 0:
        return None
    first_line = next(
        (line for line in result.stdout.splitlines() if "\t" in line),
        None,
    )
    if first_line is None:
        return None
    families_text, path_text = first_line.split("\t", 1)
    families = {
        family.strip().casefold()
        for family in families_text.split(",")
        if family.strip()
    }
    if font_name.casefold() not in families:
        return None
    font_path = Path(path_text.strip()).expanduser()
    if not _font_file_is_readable(font_path):
        return None
    return font_name, font_path


def _select_burn_font(font: str | None) -> tuple[str, Path]:
    requested = None
    if font is not None:
        try:
            requested = _validated_font_name(font)
        except ValueError:
            raise MuxError(
                "字体名称包含不安全字符。"
                "请用 --font 指定不含逗号、等号或引号的字体名称。"
            ) from None

    candidates = _font_candidates()
    if requested is not None:
        for candidate_name, candidate_path in candidates:
            if (
                candidate_name.casefold() == requested.casefold()
                and _font_file_is_readable(candidate_path)
            ):
                return candidate_name, candidate_path
        matched = _fontconfig_match(requested)
        if matched is not None:
            return matched
        raise MuxError(
            f"找不到或无法读取指定字体“{requested}”，"
            "该字体也可能不包含中文字形。"
            "请确认字体已安装、当前用户可读且可显示中文，"
            "或用 --font 指定另一款已安装的中文字体。"
        )

    for candidate_name, candidate_path in candidates:
        if _font_file_is_readable(candidate_path):
            return candidate_name, candidate_path

    checked_names = list(dict.fromkeys(name for name, _path in candidates))
    for candidate_name in checked_names:
        matched = _fontconfig_match(candidate_name)
        if matched is not None:
            return matched
    raise MuxError(
        "未找到可读的中文字体，无法可靠烧录中文字幕。"
        "请安装 Arial Unicode MS、Microsoft YaHei 或 "
        "Noto Sans CJK SC，或用 --font 指定已安装的中文字体。"
    )


def burn(
    video: Path,
    subtitle: Path,
    output: Path,
    font: str | None = None,
) -> None:
    """把 UTF-8 SRT 字幕烧录到视频画面。"""
    video, subtitle, output = _prepare_paths(video, subtitle, output)
    font_name, font_file = _select_burn_font(font)
    font_file = font_file.resolve()
    ffmpeg, _source = find_ffmpeg()
    subtitle_filter = (
        "subtitles=filename="
        f"{_escape_subtitle_filter_path(subtitle.resolve())}"
        ":charenc=UTF-8"
        f":fontsdir={_escape_subtitle_filter_path(font_file.parent)}"
        ":force_style='"
        f"FontName={font_name},FontSize=26,Outline=2'"
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-i",
        str(video),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        subtitle_filter,
        "-c:a",
        "copy",
    ]
    selection = "使用指定" if font is not None else "自动选择"
    print(
        f"{selection}硬字幕字体：{font_name}（{font_file}）",
        flush=True,
    )
    print("硬烧录需重编码，耗时约与视频时长相当。", flush=True)
    _run_atomic_ffmpeg(
        output,
        command,
        "硬字幕烧录失败：ffmpeg 未能生成有效的 MP4。"
        "请确认输入视频、SRT 字幕及 subtitles 滤镜可用；"
        "字体问题可用 --font 指定已安装的中文字体。",
        stderr_failure_markers=(
            "failed to find any fallback with glyph",
        ),
        stderr_failure_message=(
            "硬字幕字体无法显示部分中文，已停止发布视频。"
            "请安装可用的中文字体，或用 --font 指定另一款"
            "已安装的中文字体。"
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = _ChineseArgumentParser(description="把 SRT 字幕加入 MP4 视频")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    soft_parser = subparsers.add_parser(
        "soft", help="软字幕封装（不重编码）"
    )
    soft_parser.add_argument("video", type=Path)
    soft_parser.add_argument("subtitle", type=Path)
    soft_parser.add_argument("--out", type=Path, required=True)

    burn_parser = subparsers.add_parser(
        "burn", help="硬字幕烧录（需重编码）"
    )
    burn_parser.add_argument("video", type=Path)
    burn_parser.add_argument("subtitle", type=Path)
    burn_parser.add_argument("--out", type=Path, required=True)
    burn_parser.add_argument(
        "--font",
        type=_font_name,
        help="覆盖自动选择的中文字体名称",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.mode == "soft":
            soft(args.video, args.subtitle, args.out)
        else:
            burn(args.video, args.subtitle, args.out, font=args.font)
    except (MuxError, FileNotFoundError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
