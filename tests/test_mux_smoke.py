import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "mux.py"
FIXTURE_SRT = Path(__file__).resolve().parent / "fixtures" / "sample.srt"
sys.path.insert(0, str(SCRIPTS_DIR))

import doctor


def _load_mux():
    assert SCRIPT.is_file(), "尚未实现 scripts/mux.py"
    spec = importlib.util.spec_from_file_location("mux", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    video = tmp_path / "input video.mp4"
    subtitle = tmp_path / "input subtitle.srt"
    video.write_bytes(b"placeholder video")
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:00,900\n你好，字幕。\n",
        encoding="utf-8",
    )
    return video, subtitle


def _completed(
    command: list[str], *, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_soft_mux_adds_subtitle_stream(tmp_path):
    mux = _load_mux()
    ffmpeg, _source = doctor.find_ffmpeg()
    video = tmp_path / "t.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    output = tmp_path / "out.mp4"

    mux.soft(video, FIXTURE_SRT, output)

    probe = subprocess.run(
        [
            mux._find_ffprobe(ffmpeg),
            "-v",
            "error",
            "-select_streams",
            "s",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(output),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    assert "subtitle" in probe.stdout


def test_burn_uses_subtitles_filter_and_warns_before_ffmpeg(
    tmp_path, monkeypatch, capsys
):
    mux = _load_mux()
    video, subtitle = _inputs(tmp_path)
    output = tmp_path / "burned.mp4"
    state = {}
    monkeypatch.setattr(
        mux, "find_ffmpeg", lambda: ("/tools/ffmpeg", "system")
    )

    def fake_run(command, **kwargs):
        state["before_run"] = capsys.readouterr().out
        state["command"] = command
        state["kwargs"] = kwargs
        Path(command[-1]).write_bytes(b"burned video")
        return _completed(command)

    monkeypatch.setattr(subprocess, "run", fake_run)

    mux.burn(video, subtitle, output)

    assert "需重编码，耗时约与视频时长相当" in state["before_run"]
    command = state["command"]
    assert isinstance(command, list)
    assert command[command.index("-vf") + 1].startswith("subtitles=filename=")
    assert "charenc=UTF-8" in command[command.index("-vf") + 1]
    assert str(video) in command
    assert str(subtitle.resolve()) not in command
    assert state["kwargs"]["capture_output"] is True
    assert state["kwargs"]["text"] is True
    assert state["kwargs"]["encoding"] == "utf-8"
    assert state["kwargs"].get("shell") is not True
    assert output.read_bytes() == b"burned video"


def test_escape_subtitle_filter_path_handles_spaces_quote_colon_backslash():
    mux = _load_mux()
    source = r"C:\Course Files\Bob's: lesson,[1];.srt"

    escaped = mux._escape_subtitle_filter_path(source)

    assert escaped == (
        r"C\\:\\\\Course Files\\\\Bob\\\'s\\: lesson\,\[1\]\;.srt"
    )


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        ("video", "找不到输入视频"),
        ("subtitle", "找不到 SRT 字幕"),
    ],
)
def test_missing_input_exits_one_before_ffmpeg(
    tmp_path, monkeypatch, capsys, missing, expected
):
    mux = _load_mux()
    video, subtitle = _inputs(tmp_path)
    if missing == "video":
        video.unlink()
    else:
        subtitle.unlink()
    monkeypatch.setattr(
        mux,
        "find_ffmpeg",
        lambda: pytest.fail("输入校验失败时不应查找 ffmpeg"),
    )

    exit_code = mux.main(
        ["soft", str(video), str(subtitle), "--out", str(tmp_path / "out.mp4")]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert expected in captured.err
    assert "请" in captured.err
    assert "Traceback" not in captured.out + captured.err


@pytest.mark.parametrize("conflict", ["video", "subtitle"])
def test_output_cannot_overwrite_video_or_subtitle(
    tmp_path, monkeypatch, capsys, conflict
):
    mux = _load_mux()
    video, subtitle = _inputs(tmp_path)
    output = video if conflict == "video" else subtitle
    monkeypatch.setattr(
        mux,
        "find_ffmpeg",
        lambda: pytest.fail("路径冲突时不应查找 ffmpeg"),
    )

    exit_code = mux.main(
        ["soft", str(video), str(subtitle), "--out", str(output)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "输出路径" in captured.err
    assert "同一文件" in captured.err
    assert "不能覆盖" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_output_hardlink_to_input_is_rejected_via_samefile(
    tmp_path, monkeypatch, capsys
):
    mux = _load_mux()
    video, subtitle = _inputs(tmp_path)
    output = tmp_path / "hardlink output.mp4"
    try:
        output.hardlink_to(video)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"当前平台不能创建 hardlink：{error}")
    monkeypatch.setattr(
        mux,
        "find_ffmpeg",
        lambda: pytest.fail("samefile 冲突时不应查找 ffmpeg"),
    )

    exit_code = mux.main(
        ["burn", str(video), str(subtitle), "--out", str(output)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "输出路径" in captured.err
    assert "同一文件" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert output.samefile(video)


def test_output_parent_error_is_human_and_does_not_run_ffmpeg(
    tmp_path, monkeypatch, capsys
):
    mux = _load_mux()
    video, subtitle = _inputs(tmp_path)
    blocked_parent = tmp_path / "not a directory"
    blocked_parent.write_text("blocked", encoding="utf-8")
    output = blocked_parent / "out.mp4"
    monkeypatch.setattr(
        mux,
        "find_ffmpeg",
        lambda: pytest.fail("输出目录错误时不应查找 ffmpeg"),
    )

    exit_code = mux.main(
        ["burn", str(video), str(subtitle), "--out", str(output)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "无法创建视频输出目录" in captured.err
    assert "请检查" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_output_path_that_is_directory_is_rejected_before_ffmpeg(
    tmp_path, monkeypatch, capsys
):
    mux = _load_mux()
    video, subtitle = _inputs(tmp_path)
    output = tmp_path / "out.mp4"
    output.mkdir()
    monkeypatch.setattr(
        mux,
        "find_ffmpeg",
        lambda: pytest.fail("输出路径是目录时不应查找 ffmpeg"),
    )

    exit_code = mux.main(
        ["soft", str(video), str(subtitle), "--out", str(output)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "输出路径不是普通文件" in captured.err
    assert "请" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_soft_uses_copy_mov_text_argument_list_and_utf8_subprocess(
    tmp_path, monkeypatch
):
    mux = _load_mux()
    video, subtitle = _inputs(tmp_path)
    output = tmp_path / "nested" / "soft output.mp4"
    calls = []
    find_calls = []
    monkeypatch.setattr(
        mux,
        "find_ffmpeg",
        lambda: (find_calls.append(True) or "/tools/ffmpeg", "system"),
    )
    monkeypatch.setattr(
        mux,
        "_find_ffprobe",
        lambda _ffmpeg: "/tools/ffprobe",
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "/tools/ffprobe":
            return _completed(command, stdout="subtitle\n")
        Path(command[-1]).write_bytes(b"new muxed video")
        return _completed(command)

    monkeypatch.setattr(subprocess, "run", fake_run)

    mux.soft(video, subtitle, output)

    assert find_calls == [True]
    assert output.read_bytes() == b"new muxed video"
    ffmpeg_command, kwargs = calls[0]
    assert isinstance(ffmpeg_command, list)
    assert ffmpeg_command[0] == "/tools/ffmpeg"
    assert ffmpeg_command[ffmpeg_command.index("-c") + 1] == "copy"
    assert ffmpeg_command[ffmpeg_command.index("-c:s") + 1] == "mov_text"
    assert str(video) in ffmpeg_command
    assert str(subtitle) in ffmpeg_command
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["encoding"] == "utf-8"
    assert kwargs.get("shell") is not True
    probe_command, probe_kwargs = calls[1]
    assert probe_command[0] == "/tools/ffprobe"
    assert probe_kwargs["encoding"] == "utf-8"
    assert probe_kwargs.get("shell") is not True


def test_success_replaces_old_target_from_same_directory_mp4_temp(
    tmp_path, monkeypatch
):
    mux = _load_mux()
    video, subtitle = _inputs(tmp_path)
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"old target")
    state = {}
    monkeypatch.setattr(
        mux, "find_ffmpeg", lambda: ("/tools/ffmpeg", "system")
    )
    monkeypatch.setattr(
        mux, "_find_ffprobe", lambda _ffmpeg: "/tools/ffprobe"
    )

    def fake_run(command, **_kwargs):
        if command[0] == "/tools/ffprobe":
            return _completed(command, stdout="subtitle\n")
        temporary = Path(command[-1])
        state["temporary"] = temporary
        state["old_during_ffmpeg"] = output.read_bytes()
        temporary.write_bytes(b"new target")
        return _completed(command)

    monkeypatch.setattr(subprocess, "run", fake_run)

    mux.soft(video, subtitle, output)

    assert state["old_during_ffmpeg"] == b"old target"
    assert state["temporary"] != output
    assert state["temporary"].parent == output.parent
    assert state["temporary"].suffix == ".mp4"
    assert not state["temporary"].exists()
    assert output.read_bytes() == b"new target"
    assert set(tmp_path.iterdir()) == {video, subtitle, output}


def test_ffmpeg_failure_preserves_old_target_and_cleans_temp(
    tmp_path, monkeypatch, capsys
):
    mux = _load_mux()
    video, subtitle = _inputs(tmp_path)
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"old target must survive")
    state = {}
    monkeypatch.setattr(
        mux, "find_ffmpeg", lambda: ("/tools/ffmpeg", "system")
    )

    def fake_run(command, **_kwargs):
        state["temporary"] = Path(command[-1])
        state["temporary"].write_bytes(b"partial output")
        return _completed(
            command, returncode=1, stderr="secret low-level failure"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = mux.main(
        ["soft", str(video), str(subtitle), "--out", str(output)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "字幕封装失败" in captured.err
    assert "请" in captured.err
    assert "secret" not in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert output.read_bytes() == b"old target must survive"
    assert not state["temporary"].exists()
    assert set(tmp_path.iterdir()) == {video, subtitle, output}


def test_soft_rejects_output_without_subtitle_stream_and_preserves_target(
    tmp_path, monkeypatch, capsys
):
    mux = _load_mux()
    video, subtitle = _inputs(tmp_path)
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"old verified target")
    monkeypatch.setattr(
        mux, "find_ffmpeg", lambda: ("/tools/ffmpeg", "system")
    )
    monkeypatch.setattr(
        mux, "_find_ffprobe", lambda _ffmpeg: "/tools/ffprobe"
    )

    def fake_run(command, **_kwargs):
        if command[0] == "/tools/ffprobe":
            return _completed(command, stdout="")
        Path(command[-1]).write_bytes(b"new but invalid target")
        return _completed(command)

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = mux.main(
        ["soft", str(video), str(subtitle), "--out", str(output)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "未检测到字幕流" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert output.read_bytes() == b"old verified target"
    assert set(tmp_path.iterdir()) == {video, subtitle, output}


def test_missing_ffprobe_keeps_soft_target_untouched(
    tmp_path, monkeypatch, capsys
):
    mux = _load_mux()
    video, subtitle = _inputs(tmp_path)
    output = tmp_path / "old.mp4"
    output.write_bytes(b"old target")
    monkeypatch.setattr(
        mux, "find_ffmpeg", lambda: ("/tools/ffmpeg", "static")
    )
    monkeypatch.setattr(
        mux,
        "_find_ffprobe",
        lambda _ffmpeg: (_ for _ in ()).throw(
            FileNotFoundError("未找到 ffprobe")
        ),
    )

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"unverified output")
        return _completed(command)

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = mux.main(
        ["soft", str(video), str(subtitle), "--out", str(output)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "ffprobe" in captured.err
    assert "无法确认字幕流" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert output.read_bytes() == b"old target"
    assert set(tmp_path.iterdir()) == {video, subtitle, output}


def test_ffmpeg_missing_is_human_and_traceback_free(
    tmp_path, monkeypatch, capsys
):
    mux = _load_mux()
    video, subtitle = _inputs(tmp_path)
    monkeypatch.setattr(
        mux,
        "find_ffmpeg",
        lambda: (_ for _ in ()).throw(
            FileNotFoundError(
                "未找到可用的 ffmpeg。请安装 ffmpeg 后重试。"
            )
        ),
    )

    exit_code = mux.main(
        ["soft", str(video), str(subtitle), "--out", str(tmp_path / "out.mp4")]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "未找到可用的 ffmpeg" in captured.err
    assert "请安装" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_find_ffprobe_uses_exe_sibling_without_replacing_parent_text(
    tmp_path, monkeypatch
):
    mux = _load_mux()
    tools_dir = tmp_path / "ffmpeg-static-files"
    tools_dir.mkdir()
    ffmpeg = tools_dir / "ffmpeg.exe"
    ffprobe = tools_dir / "ffprobe.exe"
    ffmpeg.write_bytes(b"ffmpeg")
    ffprobe.write_bytes(b"ffprobe")
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    found = mux._find_ffprobe(str(ffmpeg))

    assert found == str(ffprobe)
    assert found.startswith(str(tools_dir))


def test_find_ffprobe_falls_back_to_system_binary(tmp_path, monkeypatch):
    mux = _load_mux()
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_bytes(b"ffmpeg")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/system/ffprobe" if name == "ffprobe" else None,
    )

    assert mux._find_ffprobe(str(ffmpeg)) == "/system/ffprobe"


def test_cli_argument_error_is_actionable_chinese():
    _load_mux()

    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), "soft"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 2
    assert "参数错误" in result.stderr
    assert "请检查命令参数后重试" in result.stderr
    assert "Traceback" not in result.stderr
