import importlib.util
from datetime import timedelta
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest
import srt


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT = SCRIPTS_DIR / "transcribe.py"
sys.path.insert(0, str(SCRIPTS_DIR))


def _load_transcribe():
    assert SCRIPT.is_file(), "尚未实现 scripts/transcribe.py"
    spec = importlib.util.spec_from_file_location("transcribe", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _install_fake_whisper(
    monkeypatch,
    segments,
    *,
    before_transcribe=None,
    init_error=None,
    transcribe_error=None,
):
    state = {"models": [], "calls": []}
    fake_module = ModuleType("faster_whisper")

    class FakeWhisperModel:
        def __init__(self, model):
            state["models"].append(model)
            if init_error is not None:
                raise init_error

        def transcribe(self, audio, language):
            if before_transcribe is not None:
                state["before_transcribe"] = before_transcribe()
            state["calls"].append(
                {"audio": audio, "language": language}
            )
            if transcribe_error is not None:
                raise transcribe_error
            return iter(segments), SimpleNamespace(language="en")

    fake_module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    return state


def _success_paths(tmp_path):
    audio_path = tmp_path / "input audio.m4a"
    audio_path.write_bytes(b"fake audio")
    output_path = tmp_path / "nested" / "subtitles" / "output.srt"
    return audio_path, output_path


def test_estimate_small_model_takes_thirty_percent_of_realtime():
    transcribe = _load_transcribe()

    assert transcribe.estimate_minutes(600, "small") == 3


def test_missing_faster_whisper_cli_exits_four_with_python_313_guidance(
    tmp_path,
):
    blocked_dependency = tmp_path / "blocked-dependency"
    blocked_dependency.mkdir()
    (blocked_dependency / "faster_whisper.py").write_text(
        "raise ImportError('simulated missing dependency')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            [str(blocked_dependency), environment.get("PYTHONPATH")],
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(SCRIPT),
            str(tmp_path / "not-even-a-real-audio.wav"),
            "--model",
            "small",
            "--lang",
            "auto",
            "--out",
            str(tmp_path / "output.srt"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=environment,
    )

    assert result.returncode == 4
    assert "转写功能不可用" in result.stderr
    assert "Python 3.13" in result.stderr
    assert "venv" in result.stderr
    if sys.platform == "darwin":
        assert "brew install python@3.13" in result.stderr
    elif sys.platform.startswith("win"):
        assert "winget install Python.Python.3.13" in result.stderr
    else:
        assert "sudo apt install python3.13 python3.13-venv" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_argument_error_is_actionable_chinese(tmp_path):
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), str(tmp_path / "a.wav")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 2
    assert "参数错误" in result.stderr
    assert "请检查命令参数后重试" in result.stderr
    assert "Traceback" not in result.stderr


def test_find_ffprobe_prefers_system_executable(monkeypatch):
    transcribe = _load_transcribe()
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/tools/ffprobe" if name == "ffprobe" else None,
    )

    assert transcribe._find_ffprobe() == "/tools/ffprobe"


def test_find_ffprobe_uses_sibling_of_static_ffmpeg(tmp_path, monkeypatch):
    transcribe = _load_transcribe()
    fake_ffmpeg = tmp_path / "ffmpeg"
    fake_ffprobe = tmp_path / "ffprobe"
    fake_ffmpeg.write_bytes(b"ffmpeg")
    fake_ffprobe.write_bytes(b"ffprobe")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        transcribe,
        "find_ffmpeg",
        lambda: (str(fake_ffmpeg), "static"),
        raising=False,
    )

    assert transcribe._find_ffprobe() == str(fake_ffprobe)


def test_probe_duration_calls_ffprobe_with_argument_list(
    tmp_path, monkeypatch
):
    transcribe = _load_transcribe()
    audio_path = tmp_path / "input with spaces.m4a"
    audio_path.write_bytes(b"fake audio")
    calls = []
    monkeypatch.setattr(
        transcribe,
        "_find_ffprobe",
        lambda: "/tools/ffprobe",
        raising=False,
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout="600.25\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    duration = transcribe.probe_duration(audio_path)

    assert duration == pytest.approx(600.25)
    command, kwargs = calls[0]
    assert isinstance(command, list)
    assert command[0] == "/tools/ffprobe"
    assert "format=duration" in command
    assert command[-1] == str(audio_path)
    assert kwargs.get("shell") is not True


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess(
            ["ffprobe"], returncode=1, stdout="", stderr="bad media"
        ),
        subprocess.CompletedProcess(
            ["ffprobe"], returncode=0, stdout="not-a-number", stderr=""
        ),
    ],
)
def test_probe_duration_translates_ffprobe_failures(
    tmp_path, monkeypatch, result
):
    transcribe = _load_transcribe()
    audio_path = tmp_path / "broken.m4a"
    audio_path.write_bytes(b"broken")
    monkeypatch.setattr(
        transcribe,
        "_find_ffprobe",
        lambda: "/tools/ffprobe",
        raising=False,
    )
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: result)

    with pytest.raises(Exception) as error:
        transcribe.probe_duration(audio_path)

    assert type(error.value).__name__ == "TranscribeError"
    assert "无法读取音频时长" in str(error.value)
    assert "请" in str(error.value)
    assert "bad media" not in str(error.value)


def test_auto_language_becomes_none_and_estimate_prints_before_model(
    tmp_path, monkeypatch, capsys
):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    monkeypatch.setattr(transcribe, "probe_duration", lambda _path: 600)
    state = _install_fake_whisper(
        monkeypatch,
        [],
        before_transcribe=lambda: capsys.readouterr().out,
    )

    exit_code = transcribe.main(
        [
            str(audio_path),
            "--model",
            "small",
            "--lang",
            "auto",
            "--out",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert state["models"] == ["small"]
    assert state["calls"] == [
        {"audio": str(audio_path), "language": None}
    ]
    assert "预计转写耗时约 3 分钟" in state["before_transcribe"]


def test_explicit_language_is_passed_to_model(tmp_path, monkeypatch):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    monkeypatch.setattr(transcribe, "probe_duration", lambda _path: 60)
    state = _install_fake_whisper(monkeypatch, [])

    exit_code = transcribe.main(
        [
            str(audio_path),
            "--lang",
            "zh",
            "--out",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert state["calls"][0]["language"] == "zh"


def test_segments_are_written_as_utf8_srt_with_timeline_and_text(
    tmp_path, monkeypatch
):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    monkeypatch.setattr(transcribe, "probe_duration", lambda _path: 20)
    _install_fake_whisper(
        monkeypatch,
        [
            SimpleNamespace(start=1.25, end=3.5, text=" 你好，world! "),
            SimpleNamespace(start=4.0, end=5.125, text="第二句"),
        ],
    )

    exit_code = transcribe.main(
        [str(audio_path), "--out", str(output_path)]
    )

    assert exit_code == 0
    raw_srt = output_path.read_text(encoding="utf-8")
    subtitles = list(srt.parse(raw_srt))
    assert [subtitle.index for subtitle in subtitles] == [1, 2]
    assert [subtitle.start for subtitle in subtitles] == [
        timedelta(seconds=1.25),
        timedelta(seconds=4.0),
    ]
    assert [subtitle.end for subtitle in subtitles] == [
        timedelta(seconds=3.5),
        timedelta(seconds=5.125),
    ]
    assert [subtitle.content for subtitle in subtitles] == [
        "你好，world!",
        "第二句",
    ]
    assert "你好，world!" in raw_srt


def test_output_parent_directories_are_created(tmp_path, monkeypatch):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    monkeypatch.setattr(transcribe, "probe_duration", lambda _path: 5)
    _install_fake_whisper(monkeypatch, [])

    exit_code = transcribe.main(
        [str(audio_path), "--out", str(output_path)]
    )

    assert exit_code == 0
    assert output_path.is_file()


def test_empty_segments_write_an_empty_srt(tmp_path, monkeypatch):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    monkeypatch.setattr(transcribe, "probe_duration", lambda _path: 5)
    _install_fake_whisper(monkeypatch, [])

    exit_code = transcribe.main(
        [str(audio_path), "--out", str(output_path)]
    )

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8") == ""


def test_missing_input_exits_one_before_probe_or_model(
    tmp_path, monkeypatch, capsys
):
    transcribe = _load_transcribe()
    input_path = tmp_path / "missing.m4a"
    output_path = tmp_path / "output.srt"
    probe_calls = []
    monkeypatch.setattr(
        transcribe,
        "probe_duration",
        lambda path: probe_calls.append(path) or 10,
    )
    state = _install_fake_whisper(monkeypatch, [])

    exit_code = transcribe.main(
        [str(input_path), "--out", str(output_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "找不到输入音频" in captured.err
    assert "请确认" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert probe_calls == []
    assert state["models"] == []
    assert not output_path.exists()


def test_ffprobe_error_exits_one_before_model(tmp_path, monkeypatch, capsys):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)

    def fail_probe(_path):
        raise transcribe.TranscribeError(
            "无法读取音频时长。请确认输入文件可以播放后重试。"
        )

    monkeypatch.setattr(transcribe, "probe_duration", fail_probe)
    state = _install_fake_whisper(monkeypatch, [])

    exit_code = transcribe.main(
        [str(audio_path), "--out", str(output_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "无法读取音频时长" in captured.err
    assert "请确认" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert state["models"] == []
    assert not output_path.exists()


def _segments_that_fail():
    raise RuntimeError("secret iterator failure")
    yield


@pytest.mark.parametrize("failure_stage", ["init", "transcribe", "iterate"])
def test_model_errors_exit_one_without_internal_details(
    tmp_path, monkeypatch, capsys, failure_stage
):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    monkeypatch.setattr(transcribe, "probe_duration", lambda _path: 12)
    error = RuntimeError(f"secret {failure_stage} failure")
    _install_fake_whisper(
        monkeypatch,
        _segments_that_fail() if failure_stage == "iterate" else [],
        init_error=error if failure_stage == "init" else None,
        transcribe_error=(
            error if failure_stage == "transcribe" else None
        ),
    )

    exit_code = transcribe.main(
        [str(audio_path), "--out", str(output_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "本地转写失败" in captured.err
    assert "请" in captured.err
    assert "secret" not in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert not output_path.exists()


def test_output_directory_error_exits_one_without_starting_model(
    tmp_path, monkeypatch, capsys
):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    monkeypatch.setattr(transcribe, "probe_duration", lambda _path: 12)
    state = _install_fake_whisper(monkeypatch, [])
    original_mkdir = Path.mkdir

    def fail_output_directory(path, *args, **kwargs):
        if path == output_path.parent:
            raise OSError("secret mkdir failure")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_output_directory)

    exit_code = transcribe.main(
        [str(audio_path), "--out", str(output_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "无法创建字幕输出目录" in captured.err
    assert "请检查" in captured.err
    assert "secret" not in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert state["models"] == []
    assert not output_path.exists()


def test_srt_write_error_exits_one_without_internal_details(
    tmp_path, monkeypatch, capsys
):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    monkeypatch.setattr(transcribe, "probe_duration", lambda _path: 12)
    _install_fake_whisper(monkeypatch, [])
    original_write_text = Path.write_text

    def fail_output_write(path, *args, **kwargs):
        if path == output_path:
            raise OSError("secret write failure")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_output_write)

    exit_code = transcribe.main(
        [str(audio_path), "--out", str(output_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "无法写入 SRT 字幕" in captured.err
    assert "请检查" in captured.err
    assert "secret" not in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert not output_path.exists()
