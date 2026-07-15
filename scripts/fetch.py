"""使用 yt-dlp 下载学习素材。"""

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re
import shutil
import socket
import ssl
import stat
import sys
import tempfile
from typing import Callable, Sequence

import srt
import yt_dlp


YdlFactory = Callable[[dict], object]

_NETWORK_ERROR_MARKERS = (
    "unable to download webpage",
    "unable to download api page",
    "timed out",
    "connection reset",
    "network is unreachable",
    "temporary failure in name resolution",
    "name or service not known",
    "nodename nor servname provided, or not known",
    "certificate_verify_failed",
    "certificate verify failed",
)


class FetchError(Exception):
    """可安全展示给命令行用户的下载错误。"""


class SubtitleUnavailable(FetchError):
    """目标语言没有可用字幕。"""

    def __init__(self, language: str, available: list[str]):
        super().__init__(language)
        self.language = language
        self.available = available


class _RecoveryRequired(FetchError):
    """事务回滚未完成，临时目录必须保留给人工恢复。"""


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
                f"best[height<={height}][ext=mp4]"
            ),
            "outtmpl": str(output_dir / "video.%(ext)s"),
            "merge_output_format": "mp4",
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
    if "requested format is not available" in message:
        return (
            "没有兼容 MP4 格式可供下载。"
            "请换一个提供 MP4 兼容流的公开链接或来源。"
        )
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
            "not available from your location",
            "geo-restricted",
            "geo restricted",
            "geo restriction",
            "geographic restriction",
            "uploader has not made this video available",
        )
    ):
        return (
            "该内容受地区限制，当前网络所在地区无法访问。"
            "请在内容允许的地区重试，或换一个可访问的链接。"
        )
    if _is_network_error(error):
        return (
            "网络连接失败，暂时无法访问视频站点。"
            "请检查网络连接后重试。"
        )
    return "下载失败。请确认链接有效且公开，然后重试。"


def _is_network_error(error: Exception) -> bool:
    return isinstance(
        error,
        (TimeoutError, ConnectionError, socket.gaierror, ssl.SSLError),
    ) or any(
        marker in str(error).lower() for marker in _NETWORK_ERROR_MARKERS
    )


def _usable_languages(info: dict, field: str) -> list[str]:
    tracks = info.get(field, {})
    if not isinstance(tracks, dict):
        return []
    return sorted(
        language
        for language, formats in tracks.items()
        if isinstance(language, str)
        and language.strip()
        and language.casefold().replace("-", "_") != "live_chat"
        and isinstance(formats, list)
        and any(isinstance(item, dict) and item for item in formats)
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
    if requested.casefold().replace("_", "-") == "auto":
        return _select_source_language(info)

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


def _metadata_source_languages(info: dict) -> list[str]:
    """读取 yt-dlp 可靠的原语言字段，并保持优先级。"""
    languages = []
    for field in (
        "language",
        "original_language",
        "original_language_code",
        "audio_language",
    ):
        value = info.get(field)
        if isinstance(value, str) and value.strip():
            normalized = value.strip()
            if normalized.casefold() != "auto" and normalized not in languages:
                languages.append(normalized)
    return languages


def _auto_language(languages: list[str], info: dict) -> str | None:
    if not languages:
        return None
    for metadata_language in _metadata_source_languages(info):
        matched = _matching_language(languages, metadata_language)
        if matched is not None:
            return matched
    if len(languages) == 1:
        return languages[0]
    original_tracks = sorted(
        (
            language
            for language in languages
            if language.casefold().replace("_", "-").endswith("-orig")
        ),
        key=lambda value: (value.casefold().replace("_", "-"), value),
    )
    if original_tracks:
        return original_tracks[0]
    # 元数据不足时仍要给 agent 稳定、可重现的结果。
    return min(
        languages,
        key=lambda value: (value.casefold().replace("_", "-"), value),
    )


def _select_source_language(info: dict) -> tuple[str, str] | None:
    for field, source in (
        ("subtitles", "official"),
        ("automatic_captions", "automatic"),
    ):
        language = _auto_language(_usable_languages(info, field), info)
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


def _slot_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _preflight_slot(path: Path) -> None:
    """只允许空槽位或现有普通文件，绝不跟随符号链接。"""
    if path.is_symlink():
        raise FetchError(
            f"输出槽位 {path} 是符号链接，已拒绝覆盖。"
            "请先移开该链接后重试。"
        )
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    except OSError:
        raise FetchError(
            f"无法检查输出槽位 {path}。"
            "请检查路径和权限后重试。"
        ) from None
    if stat.S_ISREG(mode):
        return
    kind = "目录" if stat.S_ISDIR(mode) else "特殊文件"
    raise FetchError(
        f"输出槽位 {path} 已是{kind}，已拒绝覆盖。"
        "请先移开冲突项后重试。"
    )


def _preflight_slots(paths: Sequence[Path]) -> None:
    for path in paths:
        _preflight_slot(path)


def _replace_file(source: Path, destination: Path) -> None:
    """为事务测试提供单一、可替换的原子 rename 边界。"""
    source.replace(destination)


def _new_staging_dir(destination: Path) -> Path:
    try:
        return Path(tempfile.mkdtemp(prefix=".fetch-", dir=destination))
    except OSError:
        raise FetchError(
            f"无法在输出目录 {destination} 创建临时区。"
            "请检查权限和磁盘空间后重试。"
        ) from None


def _write_recovery_guide(staging_dir: Path) -> None:
    guide = staging_dir / "RECOVERY.txt"
    try:
        guide.write_text(
            "视频与元数据发布失败，自动回滚未完成。\n"
            "1. 暂停重试下载，先备份本目录。\n"
            "2. backups/ 中是发布前的旧文件；failed-new/ 中是未发布完整的新文件。\n"
            "3. 核对输出目录中的 video.mp4 和 meta.json，"
            "再将 backups/ 中缺失的旧文件移回。\n",
            encoding="utf-8",
        )
    except OSError:
        # 恢复资料本身比说明文件更重要；保留 staging 路径供人工检查。
        pass


def _publish_video_pair(
    staged_video: Path,
    staged_metadata: Path,
    final_video: Path,
    final_metadata: Path,
    staging_dir: Path,
) -> None:
    """成对发布视频与元数据；任一步失败则恢复旧状态。"""
    finals = (final_video, final_metadata)
    staged = (staged_video, staged_metadata)
    backups_dir = staging_dir / "backups"
    failed_new_dir = staging_dir / "failed-new"
    backups_dir.mkdir()
    failed_new_dir.mkdir()
    backed_up: list[tuple[Path, Path]] = []
    published: list[Path] = []

    try:
        # 下载期间可能有外部程序改动槽位，发布前再检一次。
        _preflight_slots(finals)
        for final_path in finals:
            if _slot_exists(final_path):
                backup_path = backups_dir / final_path.name
                _replace_file(final_path, backup_path)
                backed_up.append((final_path, backup_path))
        for staged_path, final_path in zip(staged, finals, strict=True):
            _replace_file(staged_path, final_path)
            published.append(final_path)
    except FetchError:
        raise
    except OSError:
        rollback_failed = False
        for final_path in reversed(published):
            try:
                _replace_file(
                    final_path, failed_new_dir / final_path.name
                )
            except OSError:
                rollback_failed = True
        for final_path, backup_path in reversed(backed_up):
            try:
                _replace_file(backup_path, final_path)
            except OSError:
                rollback_failed = True

        if rollback_failed:
            _write_recovery_guide(staging_dir)
            raise _RecoveryRequired(
                "视频与元数据发布失败，自动回滚也未完成。"
                f"可恢复资料已保留在：{staging_dir}。"
                "请暂停重试并查看其中的 RECOVERY.txt。"
            ) from None
        raise FetchError(
            "视频与元数据发布失败，旧文件已恢复。"
            "请检查输出目录权限和磁盘空间后重试。"
        ) from None


def _clean_subtitle_file(path: Path) -> None:
    try:
        subtitles = list(srt.parse(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, srt.SRTParseError):
        raise FetchError(
            "下载的 SRT 字幕无法读取或解析。"
            "请更新 yt-dlp 后重试。"
        ) from None

    cleaned = [cue for cue in subtitles if cue.content.strip()]
    if not cleaned:
        raise FetchError(
            "字幕下载已结束，但字幕内容为空。"
            "请选择其他字幕语言或来源后重试。"
        )
    if len(cleaned) == len(subtitles):
        return
    try:
        path.write_text(
            srt.compose(cleaned, reindex=False), encoding="utf-8"
        )
    except (OSError, UnicodeError):
        raise FetchError(
            "下载的 SRT 字幕无法清理。"
            "请检查输出目录权限后重试。"
        ) from None


def _download_media(
    mode: str,
    url: str,
    out_dir: Path,
    quality: str | None,
    factory: YdlFactory,
) -> tuple[Path, Path | None]:
    destination = _make_output_dir(out_dir)
    filename = "audio.m4a" if mode == "audio" else "video.mp4"
    final_path = destination / filename
    metadata_path = destination / "meta.json" if mode == "video" else None
    final_slots = (
        (final_path, metadata_path)
        if metadata_path is not None
        else (final_path,)
    )
    _preflight_slots(final_slots)

    staging_dir = _new_staging_dir(destination)
    keep_staging = False
    try:
        new_dir = staging_dir / "new"
        new_dir.mkdir()
        options = build_opts(mode, quality, new_dir)
        info = _extract_info(factory, options, url, download=True)
        downloaded_path = new_dir / filename
        if not downloaded_path.is_file():
            raise FetchError(
                f"下载已结束，但没有生成 {filename}。"
                "请更新 yt-dlp 与 ffmpeg 后重试。"
            )

        if mode == "video":
            temporary_metadata = new_dir / "meta.json"
            write_metadata(info, url, temporary_metadata)
            _publish_video_pair(
                downloaded_path,
                temporary_metadata,
                final_path,
                metadata_path,
                staging_dir,
            )
        else:
            _preflight_slot(final_path)
            try:
                _replace_file(downloaded_path, final_path)
            except OSError:
                raise FetchError(
                    "音频发布失败，原文件未改动。"
                    "请检查输出目录权限和磁盘空间后重试。"
                ) from None
        return final_path, metadata_path
    except _RecoveryRequired:
        keep_staging = True
        raise
    finally:
        if not keep_staging:
            try:
                shutil.rmtree(staging_dir)
            except FileNotFoundError:
                pass
            except OSError:
                # 最终槽位不受清理失败影响；下次 preflight 也不会
                # 把 .fetch-* 误当成成品。
                pass


def _download_subtitles(
    url: str,
    language: str,
    out_dir: Path,
    factory: YdlFactory,
) -> tuple[Path, str, str]:
    destination = _make_output_dir(out_dir)
    final_path = destination / "subs.orig.srt"
    _preflight_slot(final_path)
    staging_dir = _new_staging_dir(destination)
    try:
        new_dir = staging_dir
        options = build_opts("subs", None, new_dir)
        with factory(_runtime_opts(options)) as downloader:
            info = downloader.extract_info(
                url, download=False, process=False
            )
            if not isinstance(info, dict):
                raise FetchError(
                    "视频站点没有返回有效信息。请更新 yt-dlp 后重试。"
                )
            selected = select_subtitle(info, language)
            if selected is None:
                raise SubtitleUnavailable(
                    language, available_subtitle_languages(info)
                )

            selected_language, source = selected
            downloader.params.update(
                {
                    "writesubtitles": source == "official",
                    "writeautomaticsub": source == "automatic",
                    "subtitleslangs": [re.escape(selected_language)],
                }
            )
            downloader.process_ie_result(info, download=True)

        candidates = sorted(
            path
            for path in new_dir.rglob("*.srt")
            if path.is_file() and not path.is_symlink()
        )
        if len(candidates) != 1:
            raise FetchError(
                "字幕下载已结束，但没有生成唯一的 SRT 文件。"
                "请更新 yt-dlp 与 ffmpeg 后重试。"
            )
        _clean_subtitle_file(candidates[0])
        _preflight_slot(final_path)
        try:
            _replace_file(candidates[0], final_path)
        except OSError:
            raise FetchError(
                "字幕发布失败，原文件未改动。"
                "请检查输出目录权限和磁盘空间后重试。"
            ) from None
        return final_path, selected_language, source
    finally:
        try:
            shutil.rmtree(staging_dir)
        except FileNotFoundError:
            pass
        except OSError:
            pass


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
            output_path, selected_language, source = _download_subtitles(
                args.url, args.lang, args.out, factory
            )
            source_label = (
                "官方字幕" if source == "official" else "自动字幕"
            )
            print(
                f"已选择来源字幕：{selected_language}（{source_label}）"
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
    except OSError as error:
        if _is_network_error(error):
            print(classify_error(error), file=sys.stderr)
        else:
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
