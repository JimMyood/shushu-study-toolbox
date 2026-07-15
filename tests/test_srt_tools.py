import errno
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest
import srt


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "srt_tools.py"
SAMPLE = Path(__file__).resolve().parent / "fixtures" / "sample.srt"


def _load_srt_tools():
    spec = importlib.util.spec_from_file_location("srt_tools_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _read_manifest(chunks_dir: Path) -> dict:
    return json.loads(
        (chunks_dir / "manifest.json").read_text(encoding="utf-8")
    )


def _write_manifest(chunks_dir: Path, manifest: object) -> None:
    (chunks_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _convert_to_legacy_manifest(
    chunks_dir: Path, input_path: Path = SAMPLE
) -> dict:
    manifest = _read_manifest(chunks_dir)
    manifest.pop("schema_version")
    manifest.pop("input_sha256")
    manifest["input_file"] = str(input_path)
    for chunk in manifest["chunks"]:
        chunk.pop("source_sha256")
        translated_path = chunks_dir / chunk["translation_file"]
        legacy_path = translated_path.with_name(
            translated_path.name.replace(".translated.txt", ".zh.txt")
        )
        if translated_path.exists() or translated_path.is_symlink():
            translated_path.rename(legacy_path)
        chunk["translation_file"] = legacy_path.name
        chunk.pop("needs_translation", None)
    _write_manifest(chunks_dir, manifest)
    return manifest


def _regular_file_payloads(root: Path) -> list[bytes]:
    payloads = []
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            payloads.append(path.read_bytes())
    return payloads


def _assert_manifest_damaged(
    result: subprocess.CompletedProcess[str], output_path: Path
) -> None:
    assert result.returncode == 2
    assert "manifest 损坏" in result.stderr
    assert "请重新运行 chunk" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output_path.exists()


def _damage_manifest(manifest: dict, case: str) -> object:
    if case == "top_not_object":
        return []
    if case == "missing_chunk_size":
        del manifest["chunk_size"]
    elif case == "invalid_chunk_size":
        manifest["chunk_size"] = 0
    elif case == "chunks_not_list":
        manifest["chunks"] = {}
    elif case == "chunk_not_object":
        manifest["chunks"][0] = []
    elif case == "missing_required_field":
        del manifest["chunks"][0]["line_count"]
    elif case == "cue_count_mismatch":
        manifest["cue_count"] = 4
    elif case == "deleted_chunk":
        manifest["chunks"].pop(1)
    elif case == "duplicated_chunk":
        manifest["chunks"].insert(1, dict(manifest["chunks"][0]))
    elif case == "reordered_chunks":
        manifest["chunks"][0], manifest["chunks"][1] = (
            manifest["chunks"][1],
            manifest["chunks"][0],
        )
    elif case == "wrong_number":
        manifest["chunks"][0]["number"] = 9
    elif case == "wrong_line_count":
        manifest["chunks"][0]["line_count"] = 1
    elif case == "wrong_translation_file":
        manifest["chunks"][0]["translation_file"] = "../outside.translated.txt"
    elif case == "wrong_source_file":
        manifest["chunks"][0]["source_file"] = "../outside.txt"
    elif case == "wrong_start_index":
        manifest["chunks"][0]["cue_index_start"] = -1
    elif case == "wrong_end_index":
        manifest["chunks"][0]["cue_index_end"] = -1
    else:
        raise AssertionError(f"未知测试损坏类型：{case}")
    return manifest


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
    completed_translation = chunks_dir / "chunk_000.translated.txt"
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


def test_new_manifest_uses_neutral_names_hashes_and_no_absolute_input_path(
    tmp_path,
):
    chunks_dir = _chunk_sample(tmp_path)

    raw_manifest = (chunks_dir / "manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(raw_manifest)

    assert manifest["schema_version"] == 2
    assert manifest["input_file"] == SAMPLE.name
    assert str(SAMPLE) not in raw_manifest
    assert len(manifest["input_sha256"]) == 64
    assert all(
        character in "0123456789abcdef"
        for character in manifest["input_sha256"]
    )
    assert [chunk["translation_file"] for chunk in manifest["chunks"]] == [
        "chunk_000.translated.txt",
        "chunk_001.translated.txt",
        "chunk_002.translated.txt",
    ]
    assert all(len(chunk["source_sha256"]) == 64 for chunk in manifest["chunks"])


def test_chunk_source_change_with_same_count_and_indexes_stales_only_changed_translation(
    tmp_path,
):
    input_path = tmp_path / "source.srt"
    input_path.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    chunks_dir = tmp_path / "chunks"
    first = _run_cli("chunk", input_path, "--size", 2, "--out-dir", chunks_dir)
    assert first.returncode == 0, first.stderr
    _write_translations(chunks_dir)
    old_manifest = _read_manifest(chunks_dir)
    old_translation = (
        chunks_dir / "chunk_000.translated.txt"
    ).read_text(encoding="utf-8")

    subtitles = _read_subtitles(input_path)
    subtitles[0] = srt.Subtitle(
        index=subtitles[0].index,
        start=subtitles[0].start,
        end=subtitles[0].end,
        content="This source sentence changed.",
        proprietary=subtitles[0].proprietary,
    )
    input_path.write_text(
        srt.compose(subtitles, reindex=False), encoding="utf-8"
    )

    resumed = _run_cli(
        "chunk", input_path, "--size", 2, "--out-dir", chunks_dir
    )

    assert resumed.returncode == 0, resumed.stderr
    new_manifest = _read_manifest(chunks_dir)
    assert new_manifest["input_sha256"] != old_manifest["input_sha256"]
    assert [chunk["needs_translation"] for chunk in new_manifest["chunks"]] == [
        True,
        False,
        False,
    ]
    assert not (chunks_dir / "chunk_000.translated.txt").exists()
    stale_files = list(chunks_dir.glob("chunk_000.stale-*.txt"))
    assert len(stale_files) == 1
    assert stale_files[0].read_text(encoding="utf-8") == old_translation
    assert (chunks_dir / "chunk_001.translated.txt").is_file()

    output_path = tmp_path / "bilingual.srt"
    stale_merge = _run_cli(
        "merge",
        input_path,
        "--chunks-dir",
        chunks_dir,
        "--layout",
        "original-top",
        "--out",
        output_path,
    )
    assert stale_merge.returncode == 2
    assert "缺少第 1 块译文" in stale_merge.stderr
    assert not output_path.exists()

    (chunks_dir / "chunk_000.translated.txt").write_text(
        "新的译文一\n新的译文二\n", encoding="utf-8"
    )
    refreshed_merge = _run_cli(
        "merge",
        input_path,
        "--chunks-dir",
        chunks_dir,
        "--layout",
        "original-top",
        "--out",
        output_path,
    )
    assert refreshed_merge.returncode == 0, refreshed_merge.stderr


def test_chunk_v2_stale_target_race_uses_next_name_without_overwriting_competitor(
    tmp_path, monkeypatch, capsys
):
    srt_tools = _load_srt_tools()
    input_path = tmp_path / "source.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nAlpha\n",
        encoding="utf-8",
    )
    chunks_dir = tmp_path / "chunks"
    assert srt_tools.chunk_subtitles(input_path, 1, chunks_dir) == 0
    translation_path = chunks_dir / "chunk_000.translated.txt"
    translation_path.write_text("OLD_TRANSLATION\n", encoding="utf-8")
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nBeta\n",
        encoding="utf-8",
    )
    old_manifest = (chunks_dir / "manifest.json").read_bytes()
    real_link = os.link
    injected = {"path": None}

    def link_with_target_race(source, target, *args, **kwargs):
        target = Path(target)
        if (
            Path(source) == translation_path
            and target.name.startswith("chunk_000.stale-")
            and injected["path"] is None
        ):
            target.write_text("COMPETING_STALE\n", encoding="utf-8")
            injected["path"] = target
            raise FileExistsError(errno.EEXIST, "secret target race", target)
        return real_link(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "link", link_with_target_race)

    exit_code = srt_tools.chunk_subtitles(input_path, 1, chunks_dir)

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert injected["path"] is not None
    competitor_path = injected["path"]
    assert competitor_path.read_text(encoding="utf-8") == "COMPETING_STALE\n"
    stale_paths = sorted(chunks_dir.glob("chunk_000.stale-*.txt"))
    assert competitor_path in stale_paths
    own_stales = [path for path in stale_paths if path != competitor_path]
    assert len(own_stales) == 1
    assert own_stales[0].read_text(encoding="utf-8") == "OLD_TRANSLATION\n"
    assert not translation_path.exists()
    assert (chunks_dir / "manifest.json").read_bytes() != old_manifest
    assert not list(chunks_dir.glob(".srt-recovery-*"))
    assert "secret" not in captured.err


def test_chunk_v2_source_swap_is_preserved_in_recovery_and_never_reports_success(
    tmp_path, monkeypatch, capsys
):
    srt_tools = _load_srt_tools()
    input_path = tmp_path / "source.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nAlpha\n",
        encoding="utf-8",
    )
    chunks_dir = tmp_path / "chunks"
    assert srt_tools.chunk_subtitles(input_path, 1, chunks_dir) == 0
    translation_path = chunks_dir / "chunk_000.translated.txt"
    translation_path.write_text("OLD_TRANSLATION\n", encoding="utf-8")
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nBeta\n",
        encoding="utf-8",
    )
    manifest_before = (chunks_dir / "manifest.json").read_bytes()
    source_chunk_before = (chunks_dir / "chunk_000.txt").read_bytes()
    competitor = tmp_path / "competitor.tmp"
    competitor.write_text("COMPETING_TRANSLATION\n", encoding="utf-8")
    real_replace = os.replace
    injected = {"done": False}

    def replace_with_source_swap(source, target, *args, **kwargs):
        source = Path(source)
        target = Path(target)
        if (
            source == translation_path
            and target.parent.name.startswith(".srt-recovery-")
            and not injected["done"]
        ):
            injected["done"] = True
            real_replace(competitor, translation_path)
        return real_replace(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace_with_source_swap)

    exit_code = srt_tools.chunk_subtitles(input_path, 1, chunks_dir)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert injected["done"] is True
    assert "译文路径在隔离时发生变化" in captured.err
    assert "恢复目录" in captured.err
    assert "secret" not in captured.err
    assert (chunks_dir / "manifest.json").read_bytes() == manifest_before
    assert (chunks_dir / "chunk_000.txt").read_bytes() == source_chunk_before
    assert translation_path.read_text(encoding="utf-8") == "OLD_TRANSLATION\n"
    assert b"COMPETING_TRANSLATION\n" in _regular_file_payloads(chunks_dir)


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


def test_chunk_rejects_empty_srt_and_preserves_existing_outputs(tmp_path):
    empty_input = tmp_path / "empty.srt"
    empty_input.write_text("", encoding="utf-8")
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    manifest_path = chunks_dir / "manifest.json"
    source_path = chunks_dir / "chunk_000.txt"
    manifest_path.write_text("不可丢失的旧清单\n", encoding="utf-8")
    source_path.write_text("不可丢失的旧分块\n", encoding="utf-8")
    entries_before = set(chunks_dir.iterdir())

    result = _run_cli(
        "chunk", empty_input, "--size", 2, "--out-dir", chunks_dir
    )

    assert result.returncode == 1
    assert "SRT 文件没有字幕条目" in result.stderr
    assert "Traceback" not in result.stderr
    assert manifest_path.read_text(encoding="utf-8") == "不可丢失的旧清单\n"
    assert source_path.read_text(encoding="utf-8") == "不可丢失的旧分块\n"
    assert set(chunks_dir.iterdir()) == entries_before


def test_merge_rejects_line_count_mismatch(tmp_path):
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    (chunks_dir / "chunk_000.translated.txt").write_text(
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
    (chunks_dir / "chunk_001.translated.txt").unlink()
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
        "缺少第 2 块译文：chunk_001.translated.txt，请翻译该块后重试"
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


def test_merge_rejects_manifest_with_invalid_json(tmp_path):
    chunks_dir = _chunk_sample(tmp_path)
    (chunks_dir / "manifest.json").write_text("{", encoding="utf-8")
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

    _assert_manifest_damaged(result, output_path)


def test_merge_rejects_manifest_with_invalid_utf8(tmp_path):
    chunks_dir = _chunk_sample(tmp_path)
    (chunks_dir / "manifest.json").write_bytes(b"\xff\xfe")
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

    _assert_manifest_damaged(result, output_path)


def test_merge_rejects_unreadable_manifest_path(tmp_path):
    chunks_dir = _chunk_sample(tmp_path)
    manifest_path = chunks_dir / "manifest.json"
    manifest_path.unlink()
    manifest_path.mkdir()
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

    _assert_manifest_damaged(result, output_path)


@pytest.mark.parametrize(
    "case",
    [
        "top_not_object",
        "missing_chunk_size",
        "invalid_chunk_size",
        "chunks_not_list",
        "chunk_not_object",
        "missing_required_field",
        "cue_count_mismatch",
        "deleted_chunk",
        "duplicated_chunk",
        "reordered_chunks",
        "wrong_number",
        "wrong_line_count",
        "wrong_translation_file",
        "wrong_source_file",
        "wrong_start_index",
        "wrong_end_index",
    ],
)
def test_merge_rejects_manifest_structure_corruption(tmp_path, case):
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    (tmp_path / "outside.translated.txt").write_text(
        "越权译文一\n越权译文二\n", encoding="utf-8"
    )
    manifest = _damage_manifest(_read_manifest(chunks_dir), case)
    _write_manifest(chunks_dir, manifest)
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

    _assert_manifest_damaged(result, output_path)


@pytest.mark.parametrize("field", ["input_sha256", "source_sha256"])
def test_merge_rejects_tampered_manifest_hash(tmp_path, field):
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    manifest = _read_manifest(chunks_dir)
    if field == "input_sha256":
        manifest[field] = "0" * 64
    else:
        manifest["chunks"][0][field] = "0" * 64
    _write_manifest(chunks_dir, manifest)
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

    _assert_manifest_damaged(result, output_path)


def test_merge_rejects_tampered_source_chunk(tmp_path):
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    (chunks_dir / "chunk_000.txt").write_text(
        "被篡改的源文\n仍然有两行\n", encoding="utf-8"
    )
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

    _assert_manifest_damaged(result, output_path)


def test_merge_rejects_legacy_manifest_even_when_source_chunks_match(tmp_path):
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    _convert_to_legacy_manifest(chunks_dir)
    legacy_contents = {}
    for legacy_path in chunks_dir.glob("chunk_*.zh.txt"):
        legacy_contents[legacy_path] = legacy_path.read_text(encoding="utf-8")
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
    assert "旧版 manifest 缺少源字幕 hash" in result.stderr
    assert "重新运行 chunk 并重翻" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output_path.exists()
    assert {
        path: path.read_text(encoding="utf-8") for path in legacy_contents
    } == legacy_contents


def test_merge_rejects_real_legacy_stale_translation_after_old_tool_refreshed_source_chunks(
    tmp_path,
):
    input_path = tmp_path / "source.srt"
    input_path.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    chunks_dir = tmp_path / "chunks"
    chunk_result = _run_cli(
        "chunk", input_path, "--size", 2, "--out-dir", chunks_dir
    )
    assert chunk_result.returncode == 0, chunk_result.stderr
    _write_translations(chunks_dir)
    _convert_to_legacy_manifest(chunks_dir, input_path)
    stale_translation = (chunks_dir / "chunk_000.zh.txt").read_text(
        encoding="utf-8"
    )

    subtitles = _read_subtitles(input_path)
    subtitles[0] = srt.Subtitle(
        index=subtitles[0].index,
        start=subtitles[0].start,
        end=subtitles[0].end,
        content="Changed after legacy chunking.",
        proprietary=subtitles[0].proprietary,
    )
    input_path.write_text(
        srt.compose(subtitles, reindex=False), encoding="utf-8"
    )
    refreshed_chunk = (
        "\n".join(" ".join(cue.content.splitlines()) for cue in subtitles[:2])
        + "\n"
    )
    (chunks_dir / "chunk_000.txt").write_text(
        refreshed_chunk, encoding="utf-8"
    )
    output_path = tmp_path / "bilingual.srt"

    result = _run_cli(
        "merge",
        input_path,
        "--chunks-dir",
        chunks_dir,
        "--layout",
        "original-top",
        "--out",
        output_path,
    )

    assert result.returncode == 2
    assert "旧版 manifest 缺少源字幕 hash" in result.stderr
    assert "重新运行 chunk 并重翻" in result.stderr
    assert not output_path.exists()
    assert (chunks_dir / "chunk_000.zh.txt").read_text(
        encoding="utf-8"
    ) == stale_translation


def test_chunk_legacy_stale_target_race_uses_next_name_without_overwrite(
    tmp_path, monkeypatch, capsys
):
    srt_tools = _load_srt_tools()
    input_path = tmp_path / "source.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nAlpha\n",
        encoding="utf-8",
    )
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    (chunks_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
    legacy_path = chunks_dir / "chunk_000.zh.txt"
    legacy_path.write_text("LEGACY_TRANSLATION\n", encoding="utf-8")
    real_link = os.link
    injected = {"path": None}

    def link_with_target_race(source, target, *args, **kwargs):
        target = Path(target)
        if (
            Path(source) == legacy_path
            and "stale-legacy" in target.name
            and injected["path"] is None
        ):
            target.write_text("COMPETING_LEGACY_STALE\n", encoding="utf-8")
            injected["path"] = target
            raise FileExistsError(errno.EEXIST, "secret target race", target)
        return real_link(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "link", link_with_target_race)

    exit_code = srt_tools.chunk_subtitles(input_path, 1, chunks_dir)

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert injected["path"] is not None
    competitor_path = injected["path"]
    assert competitor_path.read_text(encoding="utf-8") == (
        "COMPETING_LEGACY_STALE\n"
    )
    stale_paths = sorted(chunks_dir.glob("chunk_000.stale-legacy*.txt"))
    own_stales = [path for path in stale_paths if path != competitor_path]
    assert len(own_stales) == 1
    assert own_stales[0].read_text(encoding="utf-8") == (
        "LEGACY_TRANSLATION\n"
    )
    assert not legacy_path.exists()
    assert not list(chunks_dir.glob(".srt-recovery-*"))
    assert "secret" not in captured.err


def test_chunk_legacy_resume_isolates_every_zh_and_marks_all_for_retranslation(
    tmp_path,
):
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    _convert_to_legacy_manifest(chunks_dir)
    legacy_contents = {
        path.name: path.read_text(encoding="utf-8")
        for path in chunks_dir.glob("chunk_*.zh.txt")
    }
    existing_stale = chunks_dir / "chunk_000.stale-legacy.txt"
    existing_stale.write_text("更早的旧译文\n", encoding="utf-8")

    result = _run_cli("chunk", SAMPLE, "--size", 2, "--out-dir", chunks_dir)

    assert result.returncode == 0, result.stderr
    assert "旧版译文无法验证" in result.stderr
    manifest = _read_manifest(chunks_dir)
    assert manifest["schema_version"] == 2
    assert [chunk["needs_translation"] for chunk in manifest["chunks"]] == [
        True,
        True,
        True,
    ]
    assert not list(chunks_dir.glob("chunk_*.zh.txt"))
    assert not list(chunks_dir.glob("chunk_*.translated.txt"))
    assert existing_stale.read_text(encoding="utf-8") == "更早的旧译文\n"
    assert (chunks_dir / "chunk_000.stale-legacy-2.txt").read_text(
        encoding="utf-8"
    ) == legacy_contents["chunk_000.zh.txt"]
    assert (chunks_dir / "chunk_001.stale-legacy.txt").read_text(
        encoding="utf-8"
    ) == legacy_contents["chunk_001.zh.txt"]
    assert (chunks_dir / "chunk_002.stale-legacy.txt").read_text(
        encoding="utf-8"
    ) == legacy_contents["chunk_002.zh.txt"]


@pytest.mark.parametrize("legacy_kind", ["symlink", "directory"])
def test_chunk_legacy_resume_rejects_non_regular_entry_without_following_it(
    tmp_path, legacy_kind
):
    chunks_dir = _chunk_sample(tmp_path)
    manifest = _convert_to_legacy_manifest(chunks_dir)
    manifest_before = (chunks_dir / "manifest.json").read_bytes()
    source_before = {
        chunks_dir / chunk["source_file"]: (
            chunks_dir / chunk["source_file"]
        ).read_bytes()
        for chunk in manifest["chunks"]
    }
    legacy_path = chunks_dir / "chunk_000.zh.txt"
    external = tmp_path / "external.txt"
    external.write_text("外部内容不可改变\n", encoding="utf-8")
    if legacy_kind == "symlink":
        try:
            legacy_path.symlink_to(external)
        except OSError as error:
            pytest.skip(f"当前平台不能创建 symlink：{error}")
    else:
        legacy_path.mkdir()
        (legacy_path / "marker.txt").write_text("目录内容", encoding="utf-8")

    result = _run_cli("chunk", SAMPLE, "--size", 2, "--out-dir", chunks_dir)

    assert result.returncode == 1
    assert "旧版译文必须是普通文件" in result.stderr
    assert "Traceback" not in result.stderr
    assert (chunks_dir / "manifest.json").read_bytes() == manifest_before
    assert {
        path: path.read_bytes() for path in source_before
    } == source_before
    if legacy_kind == "symlink":
        assert legacy_path.is_symlink()
        assert legacy_path.resolve() == external.resolve()
        assert external.read_text(encoding="utf-8") == "外部内容不可改变\n"
    else:
        assert legacy_path.is_dir()
        assert (legacy_path / "marker.txt").read_text(encoding="utf-8") == (
            "目录内容"
        )
    assert not list(chunks_dir.glob("chunk_*.stale-legacy*.txt"))
    assert not (chunks_dir / "chunk_000.translated.txt").exists()
    assert not list(chunks_dir.glob(".srt-recovery-*"))


def test_chunk_legacy_isolation_failure_preserves_old_translation(
    tmp_path, monkeypatch, capsys
):
    srt_tools = _load_srt_tools()
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    _convert_to_legacy_manifest(chunks_dir)
    legacy_path = chunks_dir / "chunk_000.zh.txt"
    old_content = legacy_path.read_text(encoding="utf-8")
    real_link = os.link

    def fail_legacy_isolation(source, target, *args, **kwargs):
        if Path(source) == legacy_path and "stale-legacy" in Path(target).name:
            raise OSError(errno.EIO, "secret isolation failure")
        return real_link(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "link", fail_legacy_isolation)

    exit_code = srt_tools.chunk_subtitles(SAMPLE, 2, chunks_dir)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "无法隔离旧版译文" in captured.err
    assert "secret" not in captured.err
    assert legacy_path.read_text(encoding="utf-8") == old_content
    assert not (chunks_dir / "chunk_000.translated.txt").exists()


def test_chunk_second_legacy_isolation_failure_rolls_back_every_entry(
    tmp_path, monkeypatch, capsys
):
    srt_tools = _load_srt_tools()
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    manifest = _convert_to_legacy_manifest(chunks_dir)
    manifest_path = chunks_dir / "manifest.json"
    manifest_before = manifest_path.read_bytes()
    source_before = {
        chunks_dir / chunk["source_file"]: (
            chunks_dir / chunk["source_file"]
        ).read_bytes()
        for chunk in manifest["chunks"]
    }
    legacy_before = {
        chunks_dir / chunk["translation_file"]: (
            chunks_dir / chunk["translation_file"]
        ).read_bytes()
        for chunk in manifest["chunks"]
    }
    entries_before = set(chunks_dir.iterdir())
    second_legacy = chunks_dir / "chunk_001.zh.txt"
    real_link = os.link

    def fail_second_isolation(source, target, *args, **kwargs):
        if Path(source) == second_legacy and "stale-legacy" in Path(target).name:
            raise OSError(errno.EIO, "secret second isolation failure")
        return real_link(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "link", fail_second_isolation)

    exit_code = srt_tools.chunk_subtitles(SAMPLE, 2, chunks_dir)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "无法隔离旧版译文" in captured.err
    assert "secret" not in captured.err
    assert manifest_path.read_bytes() == manifest_before
    assert {
        path: path.read_bytes() for path in source_before
    } == source_before
    assert {
        path: path.read_bytes() for path in legacy_before
    } == legacy_before
    assert set(chunks_dir.iterdir()) == entries_before
    assert not list(chunks_dir.glob("chunk_*.stale-legacy*.txt"))
    assert not list(chunks_dir.glob("chunk_*.translated.txt"))
    assert not list(chunks_dir.glob(".*.tmp"))


def test_chunk_legacy_rollback_failure_reports_partial_state_and_stops_writes(
    tmp_path, monkeypatch, capsys
):
    srt_tools = _load_srt_tools()
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    manifest = _convert_to_legacy_manifest(chunks_dir)
    manifest_path = chunks_dir / "manifest.json"
    manifest_before = manifest_path.read_bytes()
    source_before = {
        chunks_dir / chunk["source_file"]: (
            chunks_dir / chunk["source_file"]
        ).read_bytes()
        for chunk in manifest["chunks"]
    }
    first_legacy = chunks_dir / "chunk_000.zh.txt"
    first_stale = chunks_dir / "chunk_000.stale-legacy.txt"
    first_content = first_legacy.read_bytes()
    second_legacy = chunks_dir / "chunk_001.zh.txt"
    real_replace = os.replace

    def fail_isolation_and_rollback(source, target, *args, **kwargs):
        source = Path(source)
        target = Path(target)
        if (
            source == second_legacy
            and target.parent.name.startswith(".srt-recovery-")
        ):
            raise OSError("secret second isolation failure")
        if (
            source == first_stale
            and target.parent.name.startswith(".srt-recovery-")
        ):
            raise OSError("secret rollback failure")
        return real_replace(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_isolation_and_rollback)

    exit_code = srt_tools.chunk_subtitles(SAMPLE, 2, chunks_dir)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "旧版译文只完成了部分隔离" in captured.err
    assert "手动恢复" in captured.err
    assert "secret" not in captured.err
    assert manifest_path.read_bytes() == manifest_before
    assert {
        path: path.read_bytes() for path in source_before
    } == source_before
    assert first_legacy.read_bytes() == first_content
    assert first_stale.read_bytes() == first_content
    assert second_legacy.is_file()
    assert (chunks_dir / "chunk_002.zh.txt").is_file()
    assert not list(chunks_dir.glob("chunk_*.translated.txt"))
    assert not list(chunks_dir.glob(".*.tmp"))


@pytest.mark.parametrize("race_kind", ["source", "stale_target"])
def test_chunk_legacy_rollback_race_preserves_competitor_and_recovery(
    tmp_path, monkeypatch, capsys, race_kind
):
    srt_tools = _load_srt_tools()
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    manifest = _convert_to_legacy_manifest(chunks_dir)
    manifest_path = chunks_dir / "manifest.json"
    manifest_before = manifest_path.read_bytes()
    source_before = {
        chunks_dir / chunk["source_file"]: (
            chunks_dir / chunk["source_file"]
        ).read_bytes()
        for chunk in manifest["chunks"]
    }
    first_legacy = chunks_dir / "chunk_000.zh.txt"
    first_old = first_legacy.read_bytes()
    second_legacy = chunks_dir / "chunk_001.zh.txt"
    second_old = second_legacy.read_bytes()
    first_stale = chunks_dir / "chunk_000.stale-legacy.txt"
    competitor_payload = f"COMPETING_{race_kind}\n".encode()
    competitor_file = tmp_path / "competitor.tmp"
    competitor_file.write_bytes(competitor_payload)
    real_replace = os.replace
    state = {"failure_started": False, "race_injected": False}

    def replace_with_rollback_race(source, target, *args, **kwargs):
        source = Path(source)
        target = Path(target)
        if source == second_legacy and not state["failure_started"]:
            state["failure_started"] = True
            if race_kind == "source":
                first_legacy.write_bytes(competitor_payload)
                state["race_injected"] = True
            raise OSError("secret second migration failure")
        if (
            race_kind == "stale_target"
            and state["failure_started"]
            and source == first_stale
            and not state["race_injected"]
        ):
            real_replace(competitor_file, first_stale)
            state["race_injected"] = True
        return real_replace(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace_with_rollback_race)

    exit_code = srt_tools.chunk_subtitles(SAMPLE, 2, chunks_dir)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert state == {"failure_started": True, "race_injected": True}
    assert "旧版译文只完成了部分隔离" in captured.err
    assert "恢复目录" in captured.err
    assert "secret" not in captured.err
    assert manifest_path.read_bytes() == manifest_before
    assert {
        path: path.read_bytes() for path in source_before
    } == source_before
    assert second_legacy.read_bytes() == second_old
    if race_kind == "source":
        assert first_legacy.read_bytes() == competitor_payload
        assert first_old in _regular_file_payloads(chunks_dir)
    else:
        assert first_legacy.read_bytes() == first_old
        assert competitor_payload in _regular_file_payloads(chunks_dir)
    assert not list(chunks_dir.glob("chunk_*.translated.txt"))


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

    _assert_manifest_damaged(result, output_path)


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


@pytest.mark.parametrize("blank_line", ["", " \t "])
def test_merge_rejects_blank_translation_line(tmp_path, blank_line):
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    (chunks_dir / "chunk_000.translated.txt").write_text(
        f"正常译文\n{blank_line}\n", encoding="utf-8"
    )
    output_path = tmp_path / "bilingual.srt"

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

    assert result.returncode == 2
    assert "第 1 块第 2 行译文为空，请重翻该块" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output_path.exists()


def test_merge_preserves_non_contiguous_cue_indexes(tmp_path):
    input_path = tmp_path / "non-contiguous.srt"
    input_path.write_text(
        "7\n00:00:00,000 --> 00:00:01,000\nFirst\n\n"
        "42\n00:00:01,500 --> 00:00:02,500\nSecond\n",
        encoding="utf-8",
    )
    chunks_dir = tmp_path / "chunks"
    chunk_result = _run_cli(
        "chunk", input_path, "--size", 1, "--out-dir", chunks_dir
    )
    assert chunk_result.returncode == 0, chunk_result.stderr
    _write_translations(chunks_dir)
    output_path = tmp_path / "bilingual.srt"

    merge_result = _run_cli(
        "merge",
        input_path,
        "--chunks-dir",
        chunks_dir,
        "--layout",
        "original-top",
        "--out",
        output_path,
    )

    assert merge_result.returncode == 0, merge_result.stderr
    assert [cue.index for cue in _read_subtitles(output_path)] == [7, 42]


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


@pytest.mark.parametrize("alias_kind", ["same_path", "symlink", "hardlink"])
def test_merge_rejects_output_alias_to_input_without_modifying_input(
    tmp_path, alias_kind
):
    input_path = tmp_path / "source.srt"
    original_content = SAMPLE.read_text(encoding="utf-8")
    input_path.write_text(original_content, encoding="utf-8")
    chunks_dir = tmp_path / "chunks"
    chunk_result = _run_cli(
        "chunk", input_path, "--size", 2, "--out-dir", chunks_dir
    )
    assert chunk_result.returncode == 0, chunk_result.stderr
    _write_translations(chunks_dir)

    if alias_kind == "same_path":
        output_path = input_path
    else:
        output_path = tmp_path / f"{alias_kind}.srt"
        try:
            if alias_kind == "symlink":
                output_path.symlink_to(input_path)
            else:
                output_path.hardlink_to(input_path)
        except OSError as error:
            pytest.skip(f"当前平台不能创建 {alias_kind}：{error}")

    result = _run_cli(
        "merge",
        input_path,
        "--chunks-dir",
        chunks_dir,
        "--layout",
        "original-top",
        "--out",
        output_path,
    )

    assert result.returncode == 2
    assert "输出路径不能与输入 SRT 指向同一文件" in result.stderr
    assert "Traceback" not in result.stderr
    assert input_path.read_text(encoding="utf-8") == original_content


def test_merge_atomic_replace_failure_preserves_old_output(
    tmp_path, monkeypatch, capsys
):
    srt_tools = _load_srt_tools()
    chunks_dir = _chunk_sample(tmp_path)
    _write_translations(chunks_dir)
    output_path = tmp_path / "bilingual.srt"
    output_path.write_text("不可丢失的旧字幕", encoding="utf-8")
    real_replace = Path.replace

    def fail_output_replace(path, target):
        if Path(target) == output_path:
            raise OSError("secret replace failure")
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_output_replace)

    exit_code = srt_tools.merge_subtitles(
        SAMPLE, chunks_dir, "original-top", output_path
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "无法写入合并字幕" in captured.err
    assert "secret" not in captured.err
    assert output_path.read_text(encoding="utf-8") == "不可丢失的旧字幕"
    assert set(tmp_path.iterdir()) == {chunks_dir, output_path}


def test_chunk_atomic_source_replace_failure_preserves_old_chunk(
    tmp_path, monkeypatch, capsys
):
    srt_tools = _load_srt_tools()
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir()
    source_path = chunks_dir / "chunk_000.txt"
    source_path.write_text("不可丢失的旧源分块\n", encoding="utf-8")
    real_replace = Path.replace

    def fail_source_replace(path, target):
        if Path(target) == source_path:
            raise OSError("secret replace failure")
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_source_replace)

    exit_code = srt_tools.chunk_subtitles(SAMPLE, 2, chunks_dir)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "无法写入字幕分块" in captured.err
    assert "secret" not in captured.err
    assert source_path.read_text(encoding="utf-8") == "不可丢失的旧源分块\n"
    assert set(chunks_dir.iterdir()) == {source_path}


def test_chunk_atomic_manifest_replace_failure_preserves_old_manifest(
    tmp_path, monkeypatch, capsys
):
    srt_tools = _load_srt_tools()
    chunks_dir = _chunk_sample(tmp_path)
    manifest_path = chunks_dir / "manifest.json"
    old_manifest = manifest_path.read_text(encoding="utf-8")
    real_replace = Path.replace

    def fail_manifest_replace(path, target):
        if Path(target) == manifest_path:
            raise OSError("secret replace failure")
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_manifest_replace)

    exit_code = srt_tools.chunk_subtitles(SAMPLE, 1, chunks_dir)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "无法写入字幕分块" in captured.err
    assert "secret" not in captured.err
    assert manifest_path.read_text(encoding="utf-8") == old_manifest
    assert not list(chunks_dir.glob(".*.tmp"))


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


def test_validate_rejects_empty_srt_without_traceback(tmp_path):
    empty_srt = tmp_path / "empty.srt"
    empty_srt.write_text("", encoding="utf-8")

    result = _run_cli("validate", empty_srt)

    assert result.returncode == 1
    assert "SRT 文件没有字幕条目" in result.stderr
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


def test_validate_rejects_zero_duration_cue(tmp_path):
    zero_duration = tmp_path / "zero-duration.srt"
    zero_duration.write_text(
        "1\n00:00:02,000 --> 00:00:02,000\nFrozen\n\n",
        encoding="utf-8",
    )

    result = _run_cli("validate", zero_duration)

    assert result.returncode == 1
    assert "第 1 条字幕结束时间必须晚于开始时间" in result.stderr
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
