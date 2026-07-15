"""使用 yt-dlp 下载学习素材。"""

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import tempfile
from typing import Callable, Sequence

import yt_dlp


YdlFactory = Callable[[dict], object]


class FetchError(Exception):
    """可安全展示给命令行用户的下载错误。"""


class SubtitleUnavailable(FetchError):
    """目标语言没有可用字幕。"""

    def __init__(self, language: str, available: list[str]):
        super().__init__(language)
        self.language = language
        self.available = available


class _SilentLogger:
    def debug(self, _message: str) -> None:
        pass

    def warning(self, _message: str) -> None:
        pass

    def error(self, _message: str) -> None:
        pass


class _ChineseArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(
            2,
            f"{self.prog}: 参数错误：{message}。"
            "请检查命令参数后重试。\n",
        )


def build_opts(mode: str, quality: str | None, out_dir: Path) -> dict:
    """构造 yt-dlp 选项，不访问网络或文件系统。"""
    output_dir = Path(out_dir)
    if mode == "video":
        try:
            height = int(quality) if quality is not None else 0
        except (TypeError, ValueError):
            height = 0
        if height <= 0:
            raise ValueError("视频清晰度必须是正整数")
        return {
            "format": (
                f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
                f"best[height<={height}][ext=mp4]/"
                f"bestvideo[height<={height}]+bestaudio/"
                f"best[height<={height}]"
            ),
            "outtmpl": str(output_dir / "video.%(ext)s"),
            "merge_output_format": "mp4",
            "postprocessors": [
                {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
            ],
        }
    if mode == "audio":
        return {
            "format": "bestaudio[ext=m4a]/bestaudio",
            "outtmpl": str(output_dir / "audio.%(ext)s"),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "m4a",
                    "preferredquality": "0",
                }
            ],
        }
    if mode == "subs":
        return {
            "skip_download": True,
            "subtitlesformat": "srt/best",
            "outtmpl": str(output_dir / "subs.orig.%(ext)s"),
            "postprocessors": [
                {"key": "FFmpegSubtitlesConvertor", "format": "srt"}
            ],
        }
    raise ValueError(f"未知下载模式：{mode}")


def classify_error(error: Exception) -> str:
    """把 yt-dlp 异常归类为不泄露内部堆栈的人话提示。"""
    message = str(error).lower()
    if any(
        marker in message
        for marker in (
            "available to members",
            "members-only",
            "join this channel",
            "members on level",
        )
    ):
        return (
            "这是会员专享内容，当前账号无权访问。"
            "请登录有权限的账号，或换一个公开链接。"
        )
    if any(
        marker in message
        for marker in (
            "not available in your country",
            "not available in your region",
            "geo-restricted",
            "geo restricted",
            "geographic restriction",
        )
    ):
        return (
            "该内容受地区限制，当前网络所在地区无法访问。"
            "请在内容允许的地区重试，或换一个可访问的链接。"
        )
    if any(
        marker in message
        for marker in (
            "unable to download webpage",
            "timed out",
            "connection reset",
            "network is unreachable",
            "temporary failure in name resolution",
            "name or service not known",
        )
    ):
        return (
            "网络连接失败，暂时无法访问视频站点。"
            "请检查网络连接后重试。"
        )
    return "下载失败。请确认链接有效且公开，然后重试。"


def _usable_languages(info: dict, field: str) -> list[str]:
    tracks = info.get(field, {})
    if not isinstance(tracks, dict):
        return []
    return sorted(
        language
        for language, formats in tracks.items()
        if isinstance(language, str) and isinstance(formats, list) and formats
    )


def _matching_language(languages: list[str], requested: str) -> str | None:
    target = requested.casefold().replace("_", "-")
    ranked = []
    for language in languages:
        normalized = language.casefold().replace("_", "-")
        if normalized == target:
            rank = 0
        elif normalized.startswith(f"{target}-"):
            rank = 1
        elif target.startswith(f"{normalized}-"):
            rank = 2
        else:
            continue
        ranked.append((rank, normalized, language))
    return min(ranked)[2] if ranked else None


def select_subtitle(info: dict, requested: str) -> tuple[str, str] | None:
    """按官方优先、自动字幕次之选择目标语言字幕。"""
    for field, source in (
        ("subtitles", "official"),
        ("automatic_captions", "automatic"),
    ):
        language = _matching_language(
            _usable_languages(info, field), requested
        )
        if language is not None:
            return language, source
    return None


def available_subtitle_languages(info: dict) -> list[str]:
    """列出官方与自动字幕中所有可用语言。"""
    return sorted(
        set(_usable_languages(info, "subtitles"))
        | set(_usable_languages(info, "automatic_captions"))
    )


def _duration_seconds(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        duration = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return duration if math.isfinite(duration) and duration >= 0 else 0.0


def _readable_date(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError:
        return ""


def _metadata_text(value: object) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def write_metadata(info: dict, url: str, path: Path) -> None:
    """把 yt-dlp 元数据写成流水线约定的稳定 JSON 结构。"""
    payload = {
        "url": url,
        "title": _metadata_text(info.get("title")),
        "uploader": _metadata_text(info.get("uploader")),
        "duration_s": _duration_seconds(info.get("duration")),
        "date": _readable_date(info.get("upload_date")),
    }
    Path(path).write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _runtime_opts(options: dict) -> dict:
    prepared = dict(options)
    prepared.update(
        {
            "logger": _SilentLogger(),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "overwrites": True,
        }
    )
    return prepared


def _extract_info(
    factory: YdlFactory, options: dict, url: str, *, download: bool
) -> dict:
    with factory(_runtime_opts(options)) as downloader:
        info = downloader.extract_info(url, download=download)
    if not isinstance(info, dict):
        raise FetchError(
            "视频站点没有返回有效信息。请更新 yt-dlp 后重试。"
        )
    return info


def _make_output_dir(out_dir: Path) -> Path:
    destination = Path(out_dir).expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _download_media(
    mode: str,
    url: str,
    out_dir: Path,
    quality: str | None,
    factory: YdlFactory,
) -> tuple[Path, Path | None]:
    destination = _make_output_dir(out_dir)
    filename = "audio.m4a" if mode == "audio" else "video.mp4"
    with tempfile.TemporaryDirectory(
        prefix=".fetch-", dir=destination
    ) as temporary_name:
        temporary_dir = Path(temporary_name)
        options = build_opts(mode, quality, temporary_dir)
        info = _extract_info(factory, options, url, download=True)
        downloaded_path = temporary_dir / filename
        if not downloaded_path.is_file():
            raise FetchError(
                f"下载已结束，但没有生成 {filename}。"
                "请更新 yt-dlp 与 ffmpeg 后重试。"
            )

        metadata_path = None
        temporary_metadata = None
        if mode == "video":
            temporary_metadata = temporary_dir / "meta.json"
            write_metadata(info, url, temporary_metadata)

        final_path = destination / filename
        downloaded_path.replace(final_path)
        if temporary_metadata is not None:
            metadata_path = destination / "meta.json"
            temporary_metadata.replace(metadata_path)
        return final_path, metadata_path


def _download_subtitles(
    url: str,
    language: str,
    out_dir: Path,
    factory: YdlFactory,
) -> Path:
    destination = _make_output_dir(out_dir)
    inspection_options = build_opts("subs", None, destination)
    info = _extract_info(
        factory, inspection_options, url, download=False
    )
    selected = select_subtitle(info, language)
    if selected is None:
        raise SubtitleUnavailable(
            language, available_subtitle_languages(info)
        )

    selected_language, source = selected
    with tempfile.TemporaryDirectory(
        prefix=".fetch-", dir=destination
    ) as temporary_name:
        temporary_dir = Path(temporary_name)
        options = build_opts("subs", None, temporary_dir)
        options.update(
            {
                "writesubtitles": source == "official",
                "writeautomaticsub": source == "automatic",
                "subtitleslangs": [selected_language],
            }
        )
        _extract_info(factory, options, url, download=True)
        candidates = sorted(temporary_dir.rglob("*.srt"))
        if len(candidates) != 1:
            raise FetchError(
                "字幕下载已结束，但没有生成唯一的 SRT 文件。"
                "请更新 yt-dlp 与 ffmpeg 后重试。"
            )
        final_path = destination / "subs.orig.srt"
        candidates[0].replace(final_path)
        return final_path


def _quality(value: str) -> str:
    try:
        height = int(value)
    except ValueError:
        height = 0
    if height <= 0:
        raise argparse.ArgumentTypeError("视频清晰度必须是正整数")
    return str(height)


def _build_parser() -> argparse.ArgumentParser:
    parser = _ChineseArgumentParser(description="下载视频、音频或字幕")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subtitle_parser = subparsers.add_parser("subs", help="下载字幕")
    subtitle_parser.add_argument("url")
    subtitle_parser.add_argument("--lang", required=True)
    subtitle_parser.add_argument("--out", type=Path, required=True)

    audio_parser = subparsers.add_parser("audio", help="仅下载音频")
    audio_parser.add_argument("url")
    audio_parser.add_argument("--out", type=Path, required=True)

    video_parser = subparsers.add_parser("video", help="下载视频")
    video_parser.add_argument("url")
    video_parser.add_argument("--quality", type=_quality, default="1080")
    video_parser.add_argument("--out", type=Path, required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    ydl_factory: YdlFactory | None = None,
) -> int:
    args = _build_parser().parse_args(argv)
    factory = ydl_factory or yt_dlp.YoutubeDL
    try:
        if args.command == "subs":
            output_path = _download_subtitles(
                args.url, args.lang, args.out, factory
            )
            print(f"字幕已保存：{output_path}")
            return 0
        if args.command == "audio":
            output_path, _metadata_path = _download_media(
                "audio", args.url, args.out, None, factory
            )
            print(f"音频已保存：{output_path}")
            return 0

        output_path, metadata_path = _download_media(
            "video", args.url, args.out, args.quality, factory
        )
        print(f"视频已保存：{output_path}")
        print(f"元数据已保存：{metadata_path}")
        return 0
    except SubtitleUnavailable as error:
        available = ", ".join(error.available) or "无"
        print(
            f"未找到 {error.language} 字幕；可用字幕语言：{available}。"
            "请改用 --lang 选择可用语言，或改走本地转写。",
            file=sys.stderr,
        )
        return 3
    except FetchError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print(
            f"无法写入输出目录：{args.out}。"
            "请检查路径是否存在冲突以及当前用户是否有写入权限。",
            file=sys.stderr,
        )
        return 1
    except Exception as error:
        print(classify_error(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
