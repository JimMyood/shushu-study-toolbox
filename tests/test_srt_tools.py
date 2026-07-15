import json
from pathlib import Path
import subprocess
import sys

import srt


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "srt_tools.py"
SAMPLE = Path(__file__).resolve().parent / "fixtures" / "sample.srt"


def _run_cli(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), *(str(arg) for arg in args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _read_subtitles(path: Path) -> list[srt.Subtitle]:
    return list(srt.parse(path.read_text(encoding="utf-8")))


def _chunk_sample(tmp_path: Path) -> Path:
    chunks_dir = tmp_path / "chunks"
    result = _run_cli("chunk", SAMPLE, "--size", 2, "--out-dir", chunks_dir)
    assert result.returncode == 0, result.stderr
    return chunks_dir


def _write_translations(chunks_dir: Path) -> None:
    manifest = json.loads(
        (chunks_dir / "manifest.json").read_text(encoding="utf-8")
    )
    for chunk in manifest["chunks"]:
        source_path = chunks_dir / chunk["source_file"]
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        translation_path = chunks_dir / chunk["translation_file"]
        translation_path.write_text(
            "\n".join(f"译:{line}" for line in source_lines) + "\n",
            encoding="utf-8",
        )


def test_chunk_then_merge_roundtrip(tmp_path):
    chunks_dir = _chunk_sample(tmp_path)

    assert (chunks_dir / "chunk_000.txt").read_text(encoding="utf-8") == (
        "Welcome to the course. Let's begin.\n"
        "Today we will learn about atoms.\n"
    )
    manifest = json.loads(
        (chunks_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert [chunk["line_count"] for chunk in manifest["chunks"]] == [2, 2, 1]
    assert [
        (chunk["cue_index_start"], chunk["cue_index_end"])
        for chunk in manifest["chunks"]
    ] == [(1, 2), (3, 4), (5, 5)]

    _write_translations(chunks_dir)
    output_path = tmp_path / "bilingual.srt"
    result = _run_cli(
        "merge",
        SAMPLE,
        "--chunks-dir",
        chunks_dir,
        "--layout",
        "original-top",
        "--out",
        output_path,
    )
    assert result.returncode == 0, result.stderr

    original = _read_subtitles(SAMPLE)
    merged = _read_subtitles(output_path)
    assert len(merged) == len(original)
    assert [
        (cue.index, cue.start, cue.end) for cue in merged
    ] == [
        (cue.index, cue.start, cue.end) for cue in original
    ]
    assert [cue.content for cue in merged] == [
        f"{cue.content}\n译:{' '.join(cue.content.splitlines())}"
        for cue in original
    ]


def test_chunk_manifest_marks_only_missing_translations_on_resume(tmp_path):
    chunks_dir = _chunk_sample(tmp_path)
    completed_translation = chunks_dir / "chunk_000.zh.txt"
    completed_translation.write_text("已翻译一\n已翻译二\n", encoding="utf-8")

    result = _run_cli("chunk", SAMPLE, "--size", 2, "--out-dir", chunks_dir)

    assert result.returncode == 0, result.stderr
    assert completed_translation.read_text(encoding="utf-8") == (
        "已翻译一\n已翻译二\n"
    )
    manifest = json.loads(
        (chunks_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert [chunk["needs_translation"] for chunk in manifest["chunks"]] == [
        False,
        True,
        True,
    ]


def test_chunk_rejects_non_positive_size_without_traceback(tmp_path):
    result = _run_cli(
        "chunk", SAMPLE, "--size", 0, "--out-dir", tmp_path / "chunks"
    )

    assert result.returncode == 2
    assert "--size 必须是正整数" in result.stderr
    assert "Traceback" not in result.stderr


def test_chunk_rejects_non_numeric_size_in_chinese(tmp_path):
    result = _run_cli(
        "chunk",
        SAMPLE,
        "--size",
        "很多",
        "--out-dir",
        tmp_path / "chunks",
    )

    assert result.returncode == 2
    assert "--size 必须是正整数" in result.stderr
    assert "Traceback" not in result.stderr


def test_chunk_reports_missing_input_without_traceback(tmp_path):
    missing_input = tmp_path / "missing.srt"

    result = _run_cli(
        "chunk", missing_input, "--size", 2, "--out-dir", tmp_path / "chunks"
    )

    assert result.returncode == 1
    assert f"无法读取或解析 SRT 文件：{missing_input}" in result.stderr
    assert "Traceback" not in result.stderr


def test_merge_rejects_line_count_mismatch(tmp_path):
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    (chunks_dir / "chunk_000.zh.txt").write_text(
        "只有一行\n", encoding="utf-8"
    )

    result = _run_cli(
        "merge",
        SAMPLE,
        "--chunks-dir",
        chunks_dir,
        "--layout",
        "original-top",
        "--out",
        tmp_path / "bilingual.srt",
    )

    assert result.returncode == 2
    assert "第 1 块行数不符：期望 2，实际 1，请重翻该块" in result.stderr
    assert "Traceback" not in result.stderr


def test_merge_reports_missing_translation_block_without_traceback(tmp_path):
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    (chunks_dir / "chunk_001.zh.txt").unlink()
    output_path = tmp_path / "bilingual.srt"

    result = _run_cli(
        "merge",
        SAMPLE,
        "--chunks-dir",
        chunks_dir,
        "--layout",
        "original-top",
        "--out",
        output_path,
    )

    assert result.returncode == 2
    assert (
        "缺少第 2 块译文：chunk_001.zh.txt，请翻译该块后重试"
        in result.stderr
    )
    assert "Traceback" not in result.stderr
    assert not output_path.exists()


def test_merge_rejects_unknown_layout_in_chinese(tmp_path):
    chunks_dir = _chunk_sample(tmp_path)
    output_path = tmp_path / "bilingual.srt"

    result = _run_cli(
        "merge",
        SAMPLE,
        "--chunks-dir",
        chunks_dir,
        "--layout",
        "side-by-side",
        "--out",
        output_path,
    )

    assert result.returncode == 2
    assert "--layout 只支持 original-top 或 translation-top" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output_path.exists()


def test_merge_reports_missing_manifest_without_traceback(tmp_path):
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()

    result = _run_cli(
        "merge",
        SAMPLE,
        "--chunks-dir",
        chunks_dir,
        "--layout",
        "original-top",
        "--out",
        tmp_path / "bilingual.srt",
    )

    assert result.returncode == 2
    assert "缺少 manifest.json，请先运行 chunk" in result.stderr
    assert "Traceback" not in result.stderr


def test_merge_rejects_source_with_different_cue_count(tmp_path):
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    shorter_input = tmp_path / "shorter.srt"
    shorter_input.write_text(
        srt.compose(_read_subtitles(SAMPLE)[:-1], reindex=False),
        encoding="utf-8",
    )
    output_path = tmp_path / "bilingual.srt"

    result = _run_cli(
        "merge",
        shorter_input,
        "--chunks-dir",
        chunks_dir,
        "--layout",
        "original-top",
        "--out",
        output_path,
    )

    assert result.returncode == 2
    assert (
        "原文字幕条数与 manifest 不一致：原文 4，manifest 5，"
        "请重新运行 chunk"
    ) in result.stderr
    assert "Traceback" not in result.stderr
    assert not output_path.exists()


def test_merge_reports_missing_input_without_traceback(tmp_path):
    missing_input = tmp_path / "missing.srt"

    result = _run_cli(
        "merge",
        missing_input,
        "--chunks-dir",
        tmp_path / "chunks",
        "--layout",
        "original-top",
        "--out",
        tmp_path / "bilingual.srt",
    )

    assert result.returncode == 2
    assert f"无法读取或解析 SRT 文件：{missing_input}" in result.stderr
    assert "Traceback" not in result.stderr


def test_layout_translation_top(tmp_path):
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    output_path = tmp_path / "translation-top.srt"

    result = _run_cli(
        "merge",
        SAMPLE,
        "--chunks-dir",
        chunks_dir,
        "--layout",
        "translation-top",
        "--out",
        output_path,
    )

    assert result.returncode == 0, result.stderr
    original = _read_subtitles(SAMPLE)
    merged = _read_subtitles(output_path)
    assert [cue.content for cue in merged] == [
        f"译:{' '.join(cue.content.splitlines())}\n{cue.content}"
        for cue in original
    ]


def test_validate_catches_empty_cue(tmp_path):
    empty_cue = tmp_path / "empty-cue.srt"
    empty_cue.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n\n",
        encoding="utf-8",
    )

    result = _run_cli("validate", empty_cue)

    assert result.returncode == 1
    assert "第 1 条字幕内容为空" in result.stderr
    assert "Traceback" not in result.stderr


def test_validate_rejects_unparseable_srt_without_traceback(tmp_path):
    malformed = tmp_path / "malformed.srt"
    malformed.write_text("这不是 SRT 字幕\n", encoding="utf-8")

    result = _run_cli("validate", malformed)

    assert result.returncode == 1
    assert "SRT 解析失败" in result.stderr
    assert "Traceback" not in result.stderr


def test_validate_rejects_non_monotonic_timeline(tmp_path):
    non_monotonic = tmp_path / "non-monotonic.srt"
    non_monotonic.write_text(
        "1\n00:00:02,000 --> 00:00:03,000\nLater\n\n"
        "2\n00:00:01,000 --> 00:00:01,500\nEarlier\n\n",
        encoding="utf-8",
    )

    result = _run_cli("validate", non_monotonic)

    assert result.returncode == 1
    assert "第 2 条字幕时间轴非单调" in result.stderr
    assert "Traceback" not in result.stderr


def test_validate_rejects_end_before_start(tmp_path):
    inverted = tmp_path / "inverted.srt"
    inverted.write_text(
        "1\n00:00:02,000 --> 00:00:01,000\nBackwards\n\n",
        encoding="utf-8",
    )

    result = _run_cli("validate", inverted)

    assert result.returncode == 1
    assert "第 1 条字幕结束时间早于开始时间" in result.stderr
    assert "Traceback" not in result.stderr


def test_validate_warns_for_overlap_but_succeeds(tmp_path):
    overlapping = tmp_path / "overlapping.srt"
    overlapping.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nFirst\n\n"
        "2\n00:00:01,500 --> 00:00:03,000\nSecond\n\n",
        encoding="utf-8",
    )

    result = _run_cli("validate", overlapping)

    assert result.returncode == 0
    assert "警告：第 1、2 条字幕时间轴重叠" in result.stderr
    assert "校验通过：2 条字幕" in result.stdout
    assert "Traceback" not in result.stderr
