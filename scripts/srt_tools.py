"""SRT 字幕分块、双语合并与时间轴校验工具。"""

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Sequence

import srt


class LegacyIsolationError(Exception):
    """旧版译文条目无法安全隔离。"""

    def __init__(
        self,
        *,
        partial_state: bool = False,
        recovery_dir: Path | None = None,
        unsafe_entry: bool = False,
    ):
        super().__init__()
        self.partial_state = partial_state
        self.recovery_dir = recovery_dir
        self.unsafe_entry = unsafe_entry


class StaleIsolationError(Exception):
    """新版译文无法安全隔离。"""

    def __init__(
        self,
        *,
        reason: str,
        recovery_dir: Path | None = None,
    ):
        super().__init__()
        self.reason = reason
        self.recovery_dir = recovery_dir


def _read_subtitles(path: Path) -> list[srt.Subtitle]:
    return list(srt.parse(path.read_text(encoding="utf-8")))


def _one_line(content: str) -> str:
    return " ".join(content.splitlines())


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _input_sha256(subtitles: list[srt.Subtitle]) -> str:
    return _sha256_text(srt.compose(subtitles, reindex=False))


def _chunk_text(subtitles: list[srt.Subtitle]) -> str:
    if not subtitles:
        return ""
    return "\n".join(_one_line(cue.content) for cue in subtitles) + "\n"


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _atomic_write_text(path: Path, content: str) -> None:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
        temporary_path.replace(path)
    except BaseException:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


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


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--size 必须是正整数") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("--size 必须是正整数")
    return number


def _layout(value: str) -> str:
    if value not in {"original-top", "translation-top"}:
        raise argparse.ArgumentTypeError(
            "--layout 只支持 original-top 或 translation-top"
        )
    return value


def _load_manifest(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _manifest_is_valid(
    manifest: object,
    subtitles: list[srt.Subtitle],
    chunks_dir: Path,
) -> bool:
    if not isinstance(manifest, dict):
        return False

    if manifest.get("schema_version") != 2:
        return False

    input_file = manifest.get("input_file")
    if (
        not isinstance(input_file, str)
        or not input_file
        or Path(input_file).name != input_file
        or "/" in input_file
        or "\\" in input_file
    ):
        return False
    input_hash = manifest.get("input_sha256")
    if not _is_sha256(input_hash) or input_hash != _input_sha256(subtitles):
        return False

    cue_count = manifest.get("cue_count")
    chunk_size = manifest.get("chunk_size")
    chunks = manifest.get("chunks")
    if type(cue_count) is not int or cue_count != len(subtitles):
        return False
    if type(chunk_size) is not int or chunk_size <= 0:
        return False
    if not isinstance(chunks, list):
        return False

    expected_chunk_count = (cue_count + chunk_size - 1) // chunk_size
    if len(chunks) != expected_chunk_count:
        return False

    required_fields = {
        "number",
        "source_file",
        "translation_file",
        "needs_translation",
        "source_sha256",
        "line_count",
        "cue_index_start",
        "cue_index_end",
    }
    cursor = 0
    for number, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            return False
        if not required_fields.issubset(chunk):
            return False

        expected_line_count = min(chunk_size, cue_count - cursor)
        end_cursor = cursor + expected_line_count - 1
        expected_start_index = subtitles[cursor].index
        expected_end_index = subtitles[end_cursor].index
        expected_source_file = f"chunk_{number:03d}.txt"
        expected_translation_file = f"chunk_{number:03d}.translated.txt"
        expected_source_text = _chunk_text(subtitles[cursor : end_cursor + 1])

        if type(chunk["number"]) is not int or chunk["number"] != number:
            return False
        if (
            type(chunk["line_count"]) is not int
            or chunk["line_count"] != expected_line_count
        ):
            return False
        if chunk["source_file"] != expected_source_file:
            return False
        if chunk["translation_file"] != expected_translation_file:
            return False
        if type(chunk["needs_translation"]) is not bool:
            return False
        source_hash = chunk["source_sha256"]
        if (
            not _is_sha256(source_hash)
            or source_hash != _sha256_text(expected_source_text)
        ):
            return False
        if (
            type(chunk["cue_index_start"]) is not type(expected_start_index)
            or chunk["cue_index_start"] != expected_start_index
        ):
            return False
        if (
            type(chunk["cue_index_end"]) is not type(expected_end_index)
            or chunk["cue_index_end"] != expected_end_index
        ):
            return False

        source_path = chunks_dir / expected_source_file
        try:
            if source_path.is_symlink() or not source_path.is_file():
                return False
            actual_source_text = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return False
        if actual_source_text != expected_source_text:
            return False
        cursor += expected_line_count

    return cursor == cue_count


def _previous_chunk_binding(
    manifest: object,
    number: int,
    chunks_dir: Path,
) -> tuple[str | None, Path | None]:
    """返回旧分块绑定的源 hash 与译文路径；格式异常时不信任。"""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 2:
        return None, None
    chunks = manifest.get("chunks")
    if not isinstance(chunks, list) or number >= len(chunks):
        return None, None
    chunk = chunks[number]
    if not isinstance(chunk, dict) or chunk.get("number") != number:
        return None, None

    source_name = f"chunk_{number:03d}.txt"
    if chunk.get("source_file") != source_name:
        return None, None
    source_path = chunks_dir / source_name
    try:
        if source_path.is_symlink() or not source_path.is_file():
            return None, None
        source_text = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None, None
    actual_source_hash = _sha256_text(source_text)

    recorded_hash = chunk.get("source_sha256")
    expected_translation = f"chunk_{number:03d}.translated.txt"
    if (
        not _is_sha256(recorded_hash)
        or recorded_hash != actual_source_hash
        or chunk.get("translation_file") != expected_translation
    ):
        return None, None
    return recorded_hash, chunks_dir / expected_translation


def _entry_identity(path: Path) -> tuple[int, int, int]:
    entry_stat = path.lstat()
    return (entry_stat.st_dev, entry_stat.st_ino, entry_stat.st_mode)


def _try_entry_identity(path: Path) -> tuple[int, int, int] | None:
    try:
        return _entry_identity(path)
    except OSError:
        return None


def _create_safety_archive(out_dir: Path) -> Path:
    archive_dir = Path(
        tempfile.mkdtemp(prefix=".srt-safety-archive-", dir=out_dir)
    )
    archive_dir.chmod(0o700)
    return archive_dir


def _create_owned_placeholder(
    archive_dir: Path, label: str
) -> tuple[Path, tuple[int, int, int]]:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{label}.", suffix=".preserved", dir=archive_dir
    )
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        entry_stat = os.fstat(descriptor)
        identity = (entry_stat.st_dev, entry_stat.st_ino, entry_stat.st_mode)
    finally:
        os.close(descriptor)
    return Path(raw_path), identity


def _remove_private_entry(
    path: Path, expected_identity: tuple[int, int, int]
) -> bool:
    try:
        if _entry_identity(path) != expected_identity:
            return False
        os.unlink(path)
    except OSError:
        return False
    return True


def _park_entry(
    source: Path, archive_dir: Path
) -> tuple[Path, tuple[int, int, int]]:
    park_path, placeholder_identity = _create_owned_placeholder(
        archive_dir, source.name
    )
    try:
        os.replace(source, park_path)
    except OSError:
        _remove_private_entry(park_path, placeholder_identity)
        raise
    return park_path, _entry_identity(park_path)


def _stale_candidate(source: Path, base_name: str, attempt: int) -> Path:
    suffix = "" if attempt == 1 else f"-{attempt}"
    return source.with_name(f"{base_name}{suffix}.txt")


def _link_unique_stale(
    source: Path,
    base_name: str,
) -> tuple[Path, tuple[int, int, int]]:
    attempt = 1
    while True:
        candidate = _stale_candidate(source, base_name, attempt)
        try:
            os.link(source, candidate, follow_symlinks=False)
        except OSError as error:
            if error.errno == errno.EEXIST:
                attempt += 1
                continue
            raise
        return candidate, _entry_identity(candidate)


def _rollback_isolations(
    records: list[dict[str, object]], archive_dir: Path
) -> tuple[bool, Path]:

    for record in records:
        source = record["source"]
        expected = record["identity"]
        assert isinstance(source, Path) and isinstance(expected, tuple)
        current_identity = _try_entry_identity(source)
        if current_identity is None:
            carriers = [record["stale"]]
            carriers.extend(
                park_path for park_path, _identity in record["parks"]
            )
            for carrier in carriers:
                assert isinstance(carrier, Path)
                if _try_entry_identity(carrier) != expected:
                    continue
                try:
                    os.link(carrier, source, follow_symlinks=False)
                except OSError as error:
                    if error.errno != errno.EEXIST:
                        break
                break
            current_identity = _try_entry_identity(source)
        restored = current_identity == expected
        record["restored"] = restored

    for record in reversed(records):
        source = record["source"]
        stale_path = record["stale"]
        expected = record["identity"]
        restored = record.get("restored") is True
        assert (
            isinstance(source, Path)
            and isinstance(stale_path, Path)
            and isinstance(expected, tuple)
        )
        stale_identity = _try_entry_identity(stale_path)
        if stale_identity is None:
            continue
        if not restored and stale_identity == expected:
            continue
        try:
            parked_path, parked_identity = _park_entry(
                stale_path, archive_dir
            )
        except OSError:
            continue
        record["parks"].append((parked_path, parked_identity))

    for record in records:
        source = record["source"]
        expected = record["identity"]
        assert isinstance(source, Path) and isinstance(expected, tuple)
        restored = _try_entry_identity(source) == expected
        record["restored"] = restored

    # 可移植文件 API 没有“仅当另一路径仍指向同一 inode 时才删除”的
    # compare-and-unlink。因此回滚不自动删除已建立的私有载体；它可能
    # 是旧译文，也可能是并发变化后被保留的目录项，失败时必须人工核对。
    return True, archive_dir


def _isolate_regular_entries(
    entries: list[tuple[Path, str]], out_dir: Path
) -> tuple[list[Path], Path | None]:
    records: list[dict[str, object]] = []
    archive_dir: Path | None = None
    reason = "operation"
    preserve_archive = False

    try:
        validated = []
        for source, base_name in entries:
            source_stat = source.lstat()
            if source.is_symlink() or not stat.S_ISREG(source_stat.st_mode):
                raise StaleIsolationError(reason="unsafe")
            identity = (
                source_stat.st_dev,
                source_stat.st_ino,
                source_stat.st_mode,
            )
            validated.append((source, base_name, identity))

        for source, base_name, identity in validated:
            stale_path, linked_identity = _link_unique_stale(
                source, base_name
            )
            record: dict[str, object] = {
                "source": source,
                "stale": stale_path,
                "identity": identity,
                "parks": [],
            }
            records.append(record)
            if linked_identity != identity:
                reason = "stale_changed"
                raise OSError("stale identity changed")

        if not records:
            return [], None
        archive_dir = _create_safety_archive(out_dir)
        for record in records:
            source = record["source"]
            expected = record["identity"]
            assert isinstance(source, Path) and isinstance(expected, tuple)
            parked_path, parked_identity = _park_entry(source, archive_dir)
            record["parks"].append((parked_path, parked_identity))
            if parked_identity != expected:
                reason = "source_changed"
                raise OSError("source identity changed")

        preserve_archive = True
        for record in records:
            stale_path = record["stale"]
            expected = record["identity"]
            assert isinstance(stale_path, Path) and isinstance(expected, tuple)
            if _try_entry_identity(stale_path) != expected:
                reason = "stale_changed"
                raise OSError("stale identity changed")

        for record in records:
            source = record["source"]
            assert isinstance(source, Path)
            if _try_entry_identity(source) is not None:
                reason = "source_changed"
                raise OSError("source path reappeared")

        for record in records:
            expected = record["identity"]
            assert isinstance(expected, tuple)
            for parked_path, parked_identity in record["parks"]:
                if (
                    parked_identity != expected
                    or _try_entry_identity(parked_path) != expected
                ):
                    reason = "recovery_changed"
                    raise OSError("recovery identity changed")

        # 第一次公开 stale 校验返回后仍可能被并发换包；私有安全归档不删除，
        # 并在提交前再核对一次，既能报告已发生的竞争，也不会丢掉旧译文。
        for record in records:
            stale_path = record["stale"]
            expected = record["identity"]
            assert isinstance(stale_path, Path) and isinstance(expected, tuple)
            if _try_entry_identity(stale_path) != expected:
                reason = "stale_changed"
                raise OSError("stale identity changed before commit")

        return [record["stale"] for record in records], archive_dir
    except StaleIsolationError:
        raise
    except OSError as error:
        if records:
            if preserve_archive and archive_dir is not None:
                raise StaleIsolationError(
                    reason=reason,
                    recovery_dir=archive_dir,
                ) from error
            try:
                archive_dir = archive_dir or _create_safety_archive(out_dir)
                partial_state, remaining_recovery = _rollback_isolations(
                    records, archive_dir
                )
            except OSError:
                partial_state = True
                remaining_recovery = archive_dir
            if partial_state and reason == "operation":
                reason = "rollback_changed"
            raise StaleIsolationError(
                reason=reason,
                recovery_dir=remaining_recovery,
            ) from error
        raise StaleIsolationError(reason=reason) from error


def _is_legacy_translation_name(name: str) -> bool:
    prefix = "chunk_"
    suffix = ".zh.txt"
    return (
        name.startswith(prefix)
        and name.endswith(suffix)
        and name[len(prefix) : -len(suffix)].isdigit()
    )


def _isolate_legacy_translations(out_dir: Path) -> None:
    try:
        legacy_paths = sorted(
            (
                path
                for path in out_dir.iterdir()
                if _is_legacy_translation_name(path.name)
            ),
            key=lambda path: path.name,
        )
    except OSError as error:
        raise LegacyIsolationError from error
    try:
        stale_paths, archive_dir = _isolate_regular_entries(
            [
                (
                    legacy_path,
                    f"{legacy_path.name[:-len('.zh.txt')]}.stale-legacy",
                )
                for legacy_path in legacy_paths
            ],
            out_dir,
        )
    except StaleIsolationError as error:
        raise LegacyIsolationError(
            partial_state=error.recovery_dir is not None
            or error.reason
            in {"source_changed", "stale_changed", "rollback_changed"},
            recovery_dir=error.recovery_dir,
            unsafe_entry=error.reason == "unsafe",
        ) from error

    for stale_path in stale_paths:
        print(
            f"旧版译文无法验证，已隔离为 {stale_path.name}；"
            "请按当前源文重翻该块",
            file=sys.stderr,
        )
    if archive_dir is not None:
        print(
            f"旧版译文私有安全归档：{archive_dir}。"
            "它是公开 stale 的并发保护副本，请勿自动删除",
            file=sys.stderr,
        )


def chunk_subtitles(input_path: Path, size: int, out_dir: Path) -> int:
    try:
        subtitles = _read_subtitles(input_path)
    except (OSError, UnicodeError, srt.SRTParseError):
        print(f"无法读取或解析 SRT 文件：{input_path}", file=sys.stderr)
        return 1
    if not subtitles:
        print(
            f"SRT 文件没有字幕条目：{input_path}。不会改动现有分块",
            file=sys.stderr,
        )
        return 1
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        previous_manifest = _load_manifest(out_dir / "manifest.json")
        if (
            isinstance(previous_manifest, dict)
            and previous_manifest.get("schema_version") is None
        ):
            try:
                _isolate_legacy_translations(out_dir)
            except LegacyIsolationError as error:
                if error.unsafe_entry:
                    print(
                        "旧版译文必须是普通文件；符号链接或目录会保持原样。"
                        "请移走该路径条目后重试",
                        file=sys.stderr,
                    )
                elif error.partial_state:
                    recovery_message = (
                        f"安全归档（恢复目录）：{error.recovery_dir}。"
                        if error.recovery_dir is not None
                        else "未能创建恢复目录。"
                    )
                    print(
                        "旧版译文只完成了部分隔离，清单与源分块尚未更新；"
                        f"{recovery_message}请手动恢复后重试。"
                        "原译文不会被自动复用",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"无法隔离旧版译文：{out_dir}。"
                        "请检查目录权限后重试；原译文不会被自动复用",
                        file=sys.stderr,
                    )
                return 1
        chunks = []

        for number, offset in enumerate(range(0, len(subtitles), size)):
            chunk = subtitles[offset : offset + size]
            source_file = f"chunk_{number:03d}.txt"
            translation_file = f"chunk_{number:03d}.translated.txt"
            source_text = _chunk_text(chunk)
            source_hash = _sha256_text(source_text)
            translation_path = out_dir / translation_file
            old_hash, old_translation_path = _previous_chunk_binding(
                previous_manifest, number, out_dir
            )

            translation_exists = (
                translation_path.exists() or translation_path.is_symlink()
            )
            safely_reusable = (
                translation_exists
                and not translation_path.is_symlink()
                and translation_path.is_file()
                and old_translation_path == translation_path
                and old_hash == source_hash
            )
            if translation_exists and not safely_reusable:
                hash_label = (
                    old_hash[:12] if _is_sha256(old_hash) else "unknown"
                )
                try:
                    stale_paths, archive_dir = _isolate_regular_entries(
                        [
                            (
                                translation_path,
                                f"chunk_{number:03d}.stale-{hash_label}",
                            )
                        ],
                        out_dir,
                    )
                    stale_path = stale_paths[0]
                    assert archive_dir is not None
                except StaleIsolationError as error:
                    if error.reason == "unsafe":
                        print(
                            f"第 {number + 1} 块译文必须是普通文件；"
                            "符号链接或目录会保持原样",
                            file=sys.stderr,
                        )
                    elif error.reason in {
                        "source_changed",
                        "stale_changed",
                        "rollback_changed",
                        "recovery_changed",
                    }:
                        recovery_message = (
                            f"安全归档（恢复目录）：{error.recovery_dir}。"
                            if error.recovery_dir is not None
                            else "未能创建恢复目录。"
                        )
                        print(
                            "译文路径在隔离时发生变化；"
                            f"{recovery_message}清单与源分块未更新，请人工核对",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"无法安全隔离第 {number + 1} 块旧译文；"
                            "清单与源分块未更新，请检查目录权限后重试",
                            file=sys.stderr,
                        )
                    return 1
                print(
                    f"第 {number + 1} 块源文已改变；旧译文已保留为 "
                    f"{stale_path.name}。私有安全归档：{archive_dir}；"
                    "它是公开 stale 的并发保护副本，请勿自动删除",
                    file=sys.stderr,
                )

            _atomic_write_text(out_dir / source_file, source_text)
            chunks.append(
                {
                    "number": number,
                    "source_file": source_file,
                    "translation_file": translation_file,
                    "needs_translation": not safely_reusable,
                    "source_sha256": source_hash,
                    "line_count": len(chunk),
                    "cue_index_start": chunk[0].index,
                    "cue_index_end": chunk[-1].index,
                }
            )

        manifest = {
            "schema_version": 2,
            "input_file": input_path.name,
            "input_sha256": _input_sha256(subtitles),
            "chunk_size": size,
            "cue_count": len(subtitles),
            "chunks": chunks,
        }
        _atomic_write_text(
            out_dir / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
    except (OSError, UnicodeError):
        print(
            f"无法写入字幕分块：{out_dir}。请检查目录权限与磁盘空间后重试",
            file=sys.stderr,
        )
        return 1
    return 0


def merge_subtitles(
    input_path: Path, chunks_dir: Path, layout: str, output_path: Path
) -> int:
    if _paths_refer_to_same_file(input_path, output_path):
        print(
            "输出路径不能与输入 SRT 指向同一文件，请改用其他 --out 路径",
            file=sys.stderr,
        )
        return 2
    try:
        subtitles = _read_subtitles(input_path)
    except (OSError, UnicodeError, srt.SRTParseError):
        print(f"无法读取或解析 SRT 文件：{input_path}", file=sys.stderr)
        return 2
    manifest_path = chunks_dir / "manifest.json"
    if not manifest_path.exists():
        print("缺少 manifest.json，请先运行 chunk", file=sys.stderr)
        return 2
    manifest = _load_manifest(manifest_path)
    if isinstance(manifest, dict) and manifest.get("schema_version") is None:
        print(
            "旧版 manifest 缺少源字幕 hash，无法证明译文对应当前源文；"
            "请重新运行 chunk 并重翻旧版译文",
            file=sys.stderr,
        )
        return 2
    if not _manifest_is_valid(manifest, subtitles, chunks_dir):
        print("manifest 损坏，请重新运行 chunk", file=sys.stderr)
        return 2
    translations = []

    for position, chunk in enumerate(manifest["chunks"], start=1):
        translation_path = chunks_dir / chunk["translation_file"]
        try:
            translation_is_file = (
                translation_path.is_file()
                and not translation_path.is_symlink()
            )
        except OSError:
            translation_is_file = False
        if not translation_is_file:
            print(
                f"缺少第 {position} 块译文：{translation_path.name}，"
                "请翻译该块后重试",
                file=sys.stderr,
            )
            return 2
        try:
            translated_lines = translation_path.read_text(
                encoding="utf-8"
            ).splitlines()
        except (OSError, UnicodeError):
            print(
                f"无法读取第 {position} 块译文：{translation_path.name}，"
                "请检查文件编码与权限后重试",
                file=sys.stderr,
            )
            return 2
        expected = chunk["line_count"]
        actual = len(translated_lines)
        if actual != expected:
            print(
                f"第 {position} 块行数不符：期望 {expected}，实际 {actual}，"
                "请重翻该块",
                file=sys.stderr,
            )
            return 2
        for line_number, translated_line in enumerate(
            translated_lines, start=1
        ):
            if not translated_line.strip():
                print(
                    f"第 {position} 块第 {line_number} 行译文为空，"
                    "请重翻该块",
                    file=sys.stderr,
                )
                return 2
        translations.extend(translated_lines)

    merged = []
    for cue, translation in zip(subtitles, translations, strict=True):
        if layout == "original-top":
            content = f"{cue.content}\n{translation}"
        else:
            content = f"{translation}\n{cue.content}"
        merged.append(
            srt.Subtitle(
                index=cue.index,
                start=cue.start,
                end=cue.end,
                content=content,
                proprietary=cue.proprietary,
            )
        )

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(
            output_path,
            srt.compose(merged, reindex=False),
        )
    except OSError:
        print(
            f"无法写入合并字幕：{output_path}。"
            "请检查目录权限与磁盘空间后重试",
            file=sys.stderr,
        )
        return 2
    return 0


def validate_subtitles(path: Path) -> int:
    try:
        subtitles = _read_subtitles(path)
    except (OSError, UnicodeError, srt.SRTParseError) as error:
        print(f"SRT 解析失败：{path}（{error}）", file=sys.stderr)
        return 1

    if not subtitles:
        print(f"SRT 文件没有字幕条目：{path}", file=sys.stderr)
        return 1

    previous = None
    for position, cue in enumerate(subtitles, start=1):
        if not cue.content.strip():
            print(f"第 {position} 条字幕内容为空", file=sys.stderr)
            return 1
        if cue.end < cue.start:
            print(
                f"第 {position} 条字幕结束时间早于开始时间",
                file=sys.stderr,
            )
            return 1
        if cue.end == cue.start:
            print(
                f"第 {position} 条字幕结束时间必须晚于开始时间",
                file=sys.stderr,
            )
            return 1
        if previous is not None and cue.start < previous.start:
            print(f"第 {position} 条字幕时间轴非单调", file=sys.stderr)
            return 1
        if previous is not None and cue.start < previous.end:
            print(
                f"警告：第 {position - 1}、{position} 条字幕时间轴重叠",
                file=sys.stderr,
            )
        previous = cue
    print(f"校验通过：{len(subtitles)} 条字幕")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SRT 字幕分块、合并与校验")
    subparsers = parser.add_subparsers(dest="command", required=True)

    chunk_parser = subparsers.add_parser("chunk", help="把字幕拆成翻译文本块")
    chunk_parser.add_argument("input", type=Path)
    chunk_parser.add_argument("--size", type=_positive_int, default=40)
    chunk_parser.add_argument("--out-dir", type=Path, required=True)

    merge_parser = subparsers.add_parser("merge", help="合并原文与逐块译文")
    merge_parser.add_argument("input", type=Path)
    merge_parser.add_argument("--chunks-dir", type=Path, required=True)
    merge_parser.add_argument(
        "--layout",
        type=_layout,
        required=True,
    )
    merge_parser.add_argument("--out", type=Path, required=True)

    validate_parser = subparsers.add_parser("validate", help="校验 SRT 字幕")
    validate_parser.add_argument("file", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "chunk":
        return chunk_subtitles(args.input, args.size, args.out_dir)
    if args.command == "merge":
        return merge_subtitles(
            args.input, args.chunks_dir, args.layout, args.out
        )
    return validate_subtitles(args.file)


if __name__ == "__main__":
    raise SystemExit(main())
