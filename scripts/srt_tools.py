"""SRT 字幕分块、双语合并与时间轴校验工具。"""

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

import srt


def _read_subtitles(path: Path) -> list[srt.Subtitle]:
    return list(srt.parse(path.read_text(encoding="utf-8")))


def _one_line(content: str) -> str:
    return " ".join(content.splitlines())


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
    manifest: object, subtitles: list[srt.Subtitle]
) -> bool:
    if not isinstance(manifest, dict):
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
        expected_translation_file = f"chunk_{number:03d}.zh.txt"

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
        cursor += expected_line_count

    return cursor == cue_count


def chunk_subtitles(input_path: Path, size: int, out_dir: Path) -> int:
    try:
        subtitles = _read_subtitles(input_path)
    except (OSError, UnicodeError, srt.SRTParseError):
        print(f"无法读取或解析 SRT 文件：{input_path}", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks = []

    for number, offset in enumerate(range(0, len(subtitles), size)):
        chunk = subtitles[offset : offset + size]
        source_file = f"chunk_{number:03d}.txt"
        translation_file = f"chunk_{number:03d}.zh.txt"
        lines = [_one_line(cue.content) for cue in chunk]
        (out_dir / source_file).write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        chunks.append(
            {
                "number": number,
                "source_file": source_file,
                "translation_file": translation_file,
                "needs_translation": not (
                    out_dir / translation_file
                ).is_file(),
                "line_count": len(chunk),
                "cue_index_start": chunk[0].index,
                "cue_index_end": chunk[-1].index,
            }
        )

    manifest = {
        "input_file": str(input_path),
        "chunk_size": size,
        "cue_count": len(subtitles),
        "chunks": chunks,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


def merge_subtitles(
    input_path: Path, chunks_dir: Path, layout: str, output_path: Path
) -> int:
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
    if not _manifest_is_valid(manifest, subtitles):
        print("manifest 损坏，请重新运行 chunk", file=sys.stderr)
        return 2
    translations = []

    for position, chunk in enumerate(manifest["chunks"], start=1):
        translation_path = chunks_dir / chunk["translation_file"]
        if not translation_path.is_file():
            print(
                f"缺少第 {position} 块译文：{translation_path.name}，"
                "请翻译该块后重试",
                file=sys.stderr,
            )
            return 2
        translated_lines = translation_path.read_text(
            encoding="utf-8"
        ).splitlines()
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

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        srt.compose(merged, reindex=False), encoding="utf-8"
    )
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
