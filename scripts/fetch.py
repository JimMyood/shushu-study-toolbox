"""使用 yt-dlp 下载学习素材。"""

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
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


def _metadata_bytes(info: dict, url: str) -> bytes:
    """把 yt-dlp 元数据序列化为稳定、可直接落盘的 UTF-8。"""
    payload = {
        "url": url,
        "title": _metadata_text(info.get("title")),
        "uploader": _metadata_text(info.get("uploader")),
        "duration_s": _duration_seconds(info.get("duration")),
        "date": _readable_date(info.get("upload_date")),
    }
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ) + "\n"
        return serialized.encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise FetchError(
            "视频元数据包含无法写入的文本。"
            "请换一个有效来源后重试。"
        ) from None


def _unlink_if_owned(path: Path, identity: tuple[int, int]) -> None:
    """失败清理时仅删除本函数刚创建的 inode。"""
    try:
        current = path.lstat()
        if (current.st_dev, current.st_ino) == identity:
            path.unlink()
    except BaseException:
        # 原始写入异常更重要；无法确认身份时绝不继续删除。
        pass


def _write_exclusive_bytes(path: Path, content: bytes) -> None:
    """以 O_EXCL 创建普通文件，已有路径（含 symlink）一律拒绝。"""
    destination = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    owned_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(destination, flags, 0o600)
        created = os.fstat(descriptor)
        owned_identity = (created.st_dev, created.st_ino)
        stream = os.fdopen(descriptor, "wb")
        descriptor = None
        with stream:
            stream.write(content)
    except BaseException as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException:
                pass
        if owned_identity is not None:
            _unlink_if_owned(destination, owned_identity)
        if not isinstance(error, Exception):
            raise
        raise FetchError(
            f"无法安全创建元数据文件 {destination}。"
            "请移开同名项并检查目录权限后重试。"
        ) from None


def write_metadata(info: dict, url: str, path: Path) -> None:
    """以独占普通文件写入稳定 JSON，绝不跟随现有符号链接。"""
    _write_exclusive_bytes(Path(path), _metadata_bytes(info, url))


def _write_staged_metadata(info: dict, url: str, directory: Path) -> Path:
    """在 staging 中用不可预测的独占文件名写入元数据。"""
    temporary_path: Path | None = None
    owned_identity: tuple[int, int] | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".meta-",
            suffix=".json",
            dir=directory,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            created = os.fstat(stream.fileno())
            owned_identity = (created.st_dev, created.st_ino)
            stream.write(_metadata_bytes(info, url))
        return temporary_path
    except BaseException as error:
        if temporary_path is not None and owned_identity is not None:
            _unlink_if_owned(temporary_path, owned_identity)
        if not isinstance(error, Exception):
            raise
        if isinstance(error, FetchError):
            raise
        raise FetchError(
            "无法在临时区安全写入视频元数据。"
            "请检查输出目录权限和磁盘空间后重试。"
        ) from None


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
    if destination.is_symlink():
        raise FetchError(
            f"输出目录 {destination} 是符号链接，已拒绝使用。"
            "请改用真实目录后重试。"
        )
    try:
        destination.mkdir(parents=True, exist_ok=True)
        mode = destination.lstat().st_mode
    except OSError:
        raise FetchError(
            f"无法写入输出目录 {destination}。"
            "请检查路径、类型与权限后重试。"
        ) from None
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        kind = "符号链接" if stat.S_ISLNK(mode) else "非目录项"
        raise FetchError(
            f"输出目录 {destination} 是{kind}，已拒绝使用。"
            "请改用真实目录后重试。"
        )
    return destination


@dataclass(frozen=True)
class _EntryIdentity:
    device: int
    inode: int
    mode: int


@dataclass
class _ParkState:
    directory: Path
    directory_identity: _EntryIdentity
    known_identities: set[_EntryIdentity]
    parked_paths: set[Path]
    watched_sources: set[Path]
    tainted_paths: set[Path]


class _TransactionConflict(Exception):
    """发布槽位或文件身份在事务中发生了竞态变化。"""


class _ParkedSourceConflict(_TransactionConflict):
    """共享 source 在 link 后被换包，未知项已移入私有 parked。"""


def _entry_identity(path: Path) -> _EntryIdentity | None:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return None
    return _EntryIdentity(current.st_dev, current.st_ino, current.st_mode)


def _preflight_slot(path: Path) -> _EntryIdentity | None:
    """只允许空槽位或现有普通文件，绝不跟随符号链接。"""
    try:
        identity = _entry_identity(path)
    except OSError:
        raise FetchError(
            f"无法检查输出槽位 {path}。"
            "请检查路径和权限后重试。"
        ) from None
    if identity is None:
        return None
    if stat.S_ISREG(identity.mode):
        return identity
    if stat.S_ISLNK(identity.mode):
        kind = "符号链接"
    else:
        kind = "目录" if stat.S_ISDIR(identity.mode) else "特殊文件"
    raise FetchError(
        f"输出槽位 {path} 已是{kind}，已拒绝覆盖。"
        "请先移开冲突项后重试。"
    )


def _capture_slot_states(
    paths: Sequence[Path],
) -> dict[Path, _EntryIdentity | None]:
    return {path: _preflight_slot(path) for path in paths}


def _verify_slot_states(
    expected: dict[Path, _EntryIdentity | None],
) -> None:
    try:
        unchanged = all(
            _entry_identity(path) == identity
            for path, identity in expected.items()
        )
    except OSError:
        unchanged = False
    if not unchanged:
        raise FetchError(
            "下载期间输出槽位发生了变化，新文件未发布。"
            "请检查输出目录后重试。"
        )


def _require_staged_regular(path: Path, label: str) -> _EntryIdentity:
    try:
        identity = _entry_identity(path)
    except OSError:
        identity = None
    if identity is None:
        raise FetchError(
            f"下载已结束，但没有生成 {label}。"
            "请更新 yt-dlp 与 ffmpeg 后重试。"
        )
    if not stat.S_ISREG(identity.mode):
        raise FetchError(
            f"下载已结束，但临时产物 {label} 不是普通文件。"
            "请更新 yt-dlp 与 ffmpeg 后重试。"
        )
    return identity


def _link_file(source: Path, destination: Path) -> None:
    """原子创建硬链接；目标已存在时绝不覆盖。"""
    os.link(source, destination, follow_symlinks=False)


def _replace_source_with_parked(source: Path, parked: Path) -> None:
    """只覆盖私有目录中本进程独占创建的 placeholder。"""
    os.replace(source, parked)


def _new_park_state(
    staging_dir: Path,
    known_identities: set[_EntryIdentity],
) -> _ParkState:
    parked_dir = staging_dir / "parked"
    try:
        parked_dir.mkdir(mode=0o700)
        identity = _entry_identity(parked_dir)
    except OSError:
        identity = None
    if (
        identity is None
        or not stat.S_ISDIR(identity.mode)
        or (
            os.name != "nt"
            and stat.S_IMODE(identity.mode) != 0o700
        )
    ):
        raise FetchError(
            "无法创建仅当前用户可访问的 parked 临时区。"
            "请检查输出目录权限后重试。"
        )
    return _ParkState(
        directory=parked_dir,
        directory_identity=identity,
        known_identities=set(known_identities),
        parked_paths=set(),
        watched_sources=set(),
        tainted_paths=set(),
    )


def _new_parked_placeholder(
    state: _ParkState,
) -> tuple[Path, _EntryIdentity]:
    if _entry_identity(state.directory) != state.directory_identity:
        raise _TransactionConflict("parked 私有目录身份已变化")

    descriptor, raw_path = tempfile.mkstemp(
        prefix=".entry-",
        dir=state.directory,
    )
    placeholder = Path(raw_path)
    try:
        created = os.fstat(descriptor)
        identity = _EntryIdentity(
            created.st_dev,
            created.st_ino,
            created.st_mode,
        )
        if not stat.S_ISREG(identity.mode):
            raise _TransactionConflict("parked placeholder 不是普通文件")
        state.parked_paths.add(placeholder)
        state.known_identities.add(identity)
    finally:
        os.close(descriptor)
    return placeholder, identity


def _find_unknown_transaction_entries(state: _ParkState) -> list[Path]:
    """识别 parked 内容和曾用 source 路径上的未知 inode。"""
    if _entry_identity(state.directory) != state.directory_identity:
        raise _TransactionConflict("parked 私有目录身份已变化")
    candidates = set(state.directory.iterdir())
    candidates.update(state.parked_paths)
    candidates.update(state.watched_sources)
    unknown = []
    for path in sorted(candidates, key=str):
        identity = _entry_identity(path)
        if identity is not None and identity not in state.known_identities:
            unknown.append(path)
    return unknown


def _park_linked_source(
    source: Path,
    expected_source: _EntryIdentity,
    state: _ParkState,
) -> Path:
    """原子搬走 link 后瞬间实际位于 source 的目录项。"""
    state.watched_sources.add(source)
    placeholder, placeholder_identity = _new_parked_placeholder(state)
    if _entry_identity(placeholder) != placeholder_identity:
        state.tainted_paths.add(placeholder)
        raise _ParkedSourceConflict("parked placeholder 身份已变化")
    _replace_source_with_parked(source, placeholder)

    parked_identity = _entry_identity(placeholder)
    source_after = _entry_identity(source)
    unknown = []
    if (
        parked_identity is not None
        and parked_identity not in state.known_identities
    ):
        unknown.append(placeholder)
    if source_after is not None and source_after not in state.known_identities:
        unknown.append(source)
    if unknown:
        state.tainted_paths.update(unknown)
        raise _ParkedSourceConflict("source 已被未知目录项替换")
    if parked_identity != expected_source:
        raise _TransactionConflict("parked 内容身份不符合预期")
    if source_after is not None:
        raise _TransactionConflict("park 后 source 路径又出现目录项")
    return placeholder


def _move_noreplace(
    source: Path,
    destination: Path,
    expected_source: _EntryIdentity,
    park_state: _ParkState,
) -> None:
    """以 link + 私有 park 完成同卷 NOREPLACE 移动。"""
    park_state.watched_sources.add(source)
    if _entry_identity(source) != expected_source:
        raise _TransactionConflict("源文件身份已变化")
    if _entry_identity(destination) is not None:
        raise _TransactionConflict("目标槽位不再为空")

    # os.link 是唯一发布原语：EEXIST、EXDEV 或不支持时直接失败，
    # 绝不退回任何会覆盖竞争者目标项的 rename 操作。
    _link_file(source, destination)
    if _entry_identity(destination) != expected_source:
        raise _TransactionConflict("硬链接返回后目标身份不符合预期")

    _park_linked_source(source, expected_source, park_state)
    if _entry_identity(destination) != expected_source:
        raise _TransactionConflict("park source 后目标身份发生变化")


def _new_staging_dir(destination: Path) -> Path:
    try:
        return Path(tempfile.mkdtemp(prefix=".fetch-", dir=destination))
    except OSError:
        raise FetchError(
            f"无法在输出目录 {destination} 创建临时区。"
            "请检查权限和磁盘空间后重试。"
        ) from None


def _write_recovery_guide(
    staging_dir: Path,
    label: str,
    preserved_unknown: Sequence[Path] = (),
) -> bool:
    guide = staging_dir / "RECOVERY.txt"
    try:
        if preserved_unknown:
            headline = (
                f"{label}发布期间检测到 source 换包；"
                "未知目录项已保留，事务未标记为成功。\n"
            )
            unknown_line = "保留路径：" + "；".join(
                str(path) for path in preserved_unknown
            ) + "。\n"
        else:
            headline = f"{label}发布失败，自动回滚未完成。\n"
            unknown_line = ""
        content = (
            headline
            + unknown_line
            + "1. 暂停重试下载，先备份本目录。\n"
            "2. parked/ 中可能含竞争写入的未知内容，"
            "绝不能直接删除。\n"
            "3. backups/ 中是发布前的旧文件；new/（若存在）和 "
            "failed-new/ 中是未发布完整的新文件。\n"
            "4. 核对输出目录中对应的最终文件，"
            "再将 backups/ 中缺失的旧文件移回。\n"
        )
        _write_exclusive_bytes(guide, content.encode("utf-8"))
        return True
    except Exception:
        # 恢复资料更重要；保留 staging 路径供人工检查。
        return False


def _rollback_transaction(
    staged_and_final: Sequence[tuple[Path, Path]],
    staged_identities: dict[Path, _EntryIdentity],
    initial_states: dict[Path, _EntryIdentity | None],
    staging_dir: Path,
    park_state: _ParkState,
) -> bool:
    """只依据实时 lstat 身份恢复事务前状态，不信任进度列表。"""
    backups_dir = staging_dir / "backups"
    failed_new_dir = staging_dir / "failed-new"
    for staged_path, final_path in reversed(staged_and_final):
        initial_identity = initial_states[final_path]
        staged_identity = staged_identities[staged_path]
        current_final = _entry_identity(final_path)

        if current_final == initial_identity:
            continue
        if current_final == staged_identity:
            failed_path = failed_new_dir / final_path.name
            # 优先用完整 link+park 原语保留 failed-new；任何 after-op
            # 异常都靠随后的 lstat 决定是否还需单独 park source。
            if _entry_identity(failed_path) is None:
                try:
                    _move_noreplace(
                        final_path,
                        failed_path,
                        staged_identity,
                        park_state,
                    )
                except BaseException:
                    pass
            # 若 link 已完成但 park 未完成，或 failed-new 槽位冲突，
            # 仍只把 source 原子搬进私有 placeholder，绝不 unlink。
            if _entry_identity(final_path) == staged_identity:
                try:
                    _park_linked_source(
                        final_path,
                        staged_identity,
                        park_state,
                    )
                except BaseException:
                    pass
            current_final = _entry_identity(final_path)

        if current_final == initial_identity:
            continue
        if current_final is not None:
            # 不覆盖外部竞态产生的未知文件。
            continue
        if initial_identity is None:
            continue

        backup_path = backups_dir / final_path.name
        if _entry_identity(backup_path) != initial_identity:
            continue
        try:
            # 回滚也用 NOREPLACE；保留 backup 到最终状态核验完成。
            _link_file(backup_path, final_path)
        except BaseException:
            pass
        _entry_identity(final_path)

    return all(
        _entry_identity(path) == identity
        for path, identity in initial_states.items()
    )


def _publish_transaction(
    staged_and_final: Sequence[tuple[Path, Path]],
    initial_states: dict[Path, _EntryIdentity | None],
    staging_dir: Path,
    label: str,
) -> None:
    """用同卷 NOREPLACE 发布，并按文件身份恢复事务前状态。"""
    _verify_slot_states(initial_states)
    staged_identities: dict[Path, _EntryIdentity] = {}
    for staged_path, _final_path in staged_and_final:
        try:
            identity = _entry_identity(staged_path)
        except OSError:
            identity = None
        if identity is None or not stat.S_ISREG(identity.mode):
            raise FetchError(
                "临时产物在发布前消失或不再是普通文件。"
                "请更新 yt-dlp 与 ffmpeg 后重试。"
            )
        staged_identities[staged_path] = identity

    backups_dir = staging_dir / "backups"
    failed_new_dir = staging_dir / "failed-new"
    backups_dir.mkdir()
    failed_new_dir.mkdir()
    known_identities = set(staged_identities.values())
    known_identities.update(
        identity
        for identity in initial_states.values()
        if identity is not None
    )
    park_state = _new_park_state(staging_dir, known_identities)

    try:
        for _staged_path, final_path in staged_and_final:
            identity = initial_states[final_path]
            if identity is None:
                continue
            _move_noreplace(
                final_path,
                backups_dir / final_path.name,
                identity,
                park_state,
            )

        for staged_path, final_path in staged_and_final:
            _move_noreplace(
                staged_path,
                final_path,
                staged_identities[staged_path],
                park_state,
            )
        unknown = _find_unknown_transaction_entries(park_state)
        if unknown:
            park_state.tainted_paths.update(unknown)
            raise _ParkedSourceConflict(
                "发布结束时 source 或 parked 出现未知目录项"
            )
        # 这是成功返回前的最后一轮观测；final 必须仍逐一对应本次
        # staged inode，不能仅凭 source/parked 中没有未知项判定成功。
        for staged_path, final_path in staged_and_final:
            if (
                _entry_identity(final_path)
                != staged_identities[staged_path]
            ):
                raise _TransactionConflict(
                    "发布结束时 final 槽位身份发生变化"
                )
        return
    except BaseException as error:
        # 这个 guard 覆盖 link/park 以及所有 mutation 后的身份观测；
        # “操作完成、进度未记录”的缝隙也能恢复。
        failure = error

    try:
        rollback_complete = _rollback_transaction(
            staged_and_final,
            staged_identities,
            initial_states,
            staging_dir,
            park_state,
        )
    except BaseException:
        rollback_complete = False

    inspection_uncertain = False
    try:
        unknown = _find_unknown_transaction_entries(park_state)
        park_state.tainted_paths.update(unknown)
    except BaseException:
        inspection_uncertain = True

    source_swap_detected = (
        inspection_uncertain
        or isinstance(failure, _ParkedSourceConflict)
        or any(
            path == staging_dir or staging_dir in path.parents
            for path in park_state.tainted_paths
        )
    )
    if not rollback_complete or source_swap_detected:
        preserved_unknown = sorted(park_state.tainted_paths, key=str)
        if inspection_uncertain and not preserved_unknown:
            preserved_unknown = [park_state.directory]
        try:
            guide_written = _write_recovery_guide(
                staging_dir,
                label,
                preserved_unknown if source_swap_detected else (),
            )
        except BaseException:
            # 无论说明文件写入遇到什么中断，都先确保唯一备份不被
            # 外层 finally 清理。后续统一通过 RecoveryRequired 报告路径。
            guide_written = False
        if guide_written:
            recovery_action = "请暂停重试并查看其中的 RECOVERY.txt。"
        else:
            recovery_action = (
                "恢复说明写入失败；请暂停重试，直接检查 "
                "parked/、backups/、new/、failed-new/ 与最终槽位。"
            )
        if source_swap_detected:
            preserved_text = "；".join(
                str(path) for path in preserved_unknown
            )
            raise _RecoveryRequired(
                f"{label}发布期间检测到 source 换包，"
                "或无法确认 parked 状态，"
                "事务未标记为成功。"
                f"未知内容已保留：{preserved_text}。"
                f"可恢复资料已保留在：{staging_dir}。"
                f"{recovery_action}"
            ) from None
        raise _RecoveryRequired(
            f"{label}发布失败，自动回滚也未完成。"
            f"可恢复资料已保留在：{staging_dir}。"
            f"{recovery_action}"
        ) from None

    if not isinstance(failure, Exception):
        raise failure
    restored = (
        "旧文件已恢复，原输出状态已恢复"
        if any(identity is not None for identity in initial_states.values())
        else "原输出状态已恢复"
    )
    raise FetchError(
        f"{label}发布失败，{restored}。"
        "请检查输出目录权限、磁盘空间，"
        "以及文件系统是否支持同卷硬链接后重试。"
    ) from None


def _cleanup_staging(staging_dir: Path) -> None:
    """先清除可能含旧数据的 backups，失败时明确告知残留路径。"""
    def warn() -> None:
        print(
            f"警告：临时目录清理失败，残留路径：{staging_dir}。"
            "请确认最终产物后手动删除该目录。",
            file=sys.stderr,
        )

    backups_dir = staging_dir / "backups"
    try:
        if _entry_identity(backups_dir) is not None:
            shutil.rmtree(backups_dir)
    except OSError:
        # 不再整体删除 staging，避免在 backups 部分清理后
        # 又遇故障时把仅存的旧数据状态进一步模糊化。
        warn()
        return
    try:
        shutil.rmtree(staging_dir)
    except FileNotFoundError:
        pass
    except OSError:
        warn()


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
    initial_states = _capture_slot_states(final_slots)

    staging_dir = _new_staging_dir(destination)
    keep_staging = False
    try:
        new_dir = staging_dir / "new"
        new_dir.mkdir()
        options = build_opts(mode, quality, new_dir)
        info = _extract_info(factory, options, url, download=True)
        downloaded_path = new_dir / filename
        _require_staged_regular(downloaded_path, filename)

        if mode == "video":
            temporary_metadata = _write_staged_metadata(info, url, new_dir)
            _require_staged_regular(temporary_metadata, "meta.json")
            _publish_transaction(
                (
                    (downloaded_path, final_path),
                    (temporary_metadata, metadata_path),
                ),
                initial_states,
                staging_dir,
                "视频与元数据",
            )
        else:
            _publish_transaction(
                ((downloaded_path, final_path),),
                initial_states,
                staging_dir,
                "音频",
            )
        return final_path, metadata_path
    except _RecoveryRequired:
        keep_staging = True
        raise
    finally:
        if not keep_staging:
            _cleanup_staging(staging_dir)


def _download_subtitles(
    url: str,
    language: str,
    out_dir: Path,
    factory: YdlFactory,
) -> tuple[Path, str, str]:
    destination = _make_output_dir(out_dir)
    final_path = destination / "subs.orig.srt"
    initial_states = _capture_slot_states((final_path,))
    staging_dir = _new_staging_dir(destination)
    keep_staging = False
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

        candidates = []
        for path in new_dir.rglob("*.srt"):
            try:
                identity = _entry_identity(path)
            except OSError:
                continue
            if identity is not None and stat.S_ISREG(identity.mode):
                candidates.append(path)
        candidates.sort()
        if len(candidates) != 1:
            raise FetchError(
                "字幕下载已结束，但没有生成唯一的 SRT 文件。"
                "请更新 yt-dlp 与 ffmpeg 后重试。"
            )
        _require_staged_regular(candidates[0], candidates[0].name)
        _clean_subtitle_file(candidates[0])
        _publish_transaction(
            ((candidates[0], final_path),),
            initial_states,
            staging_dir,
            "字幕",
        )
        return final_path, selected_language, source
    except _RecoveryRequired:
        keep_staging = True
        raise
    finally:
        if not keep_staging:
            _cleanup_staging(staging_dir)


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
