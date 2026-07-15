import importlib.util
from datetime import timedelta
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
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


def test_missing_faster_whisper_cli_exits_four_with_version_aware_guidance(
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
    if (3, 10) <= sys.version_info[:2] < (3, 14):
        assert "当前激活 venv" in result.stderr
        assert "python -m pip install -r requirements.txt" in result.stderr
        assert "Python 3.14 暂无" not in result.stderr
    else:
        assert "Python 3.10–3.13" in result.stderr
        assert "推荐 Python 3.13" in result.stderr
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


def test_find_ffprobe_uses_exe_sibling_for_windows_static_path(
    tmp_path, monkeypatch
):
    transcribe = _load_transcribe()
    fake_ffmpeg = tmp_path / "ffmpeg.exe"
    fake_ffprobe = tmp_path / "ffprobe.exe"
    fake_ffmpeg.write_bytes(b"ffmpeg")
    fake_ffprobe.write_bytes(b"ffprobe")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        transcribe,
        "find_ffmpeg",
        lambda: (str(fake_ffmpeg), "static"),
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
        subprocess.CompletedProcess(
            ["ffprobe"], returncode=0, stdout="0.0", stderr=""
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
        [SimpleNamespace(start=0.0, end=1.0, text="hello")],
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
    state = _install_fake_whisper(
        monkeypatch,
        [SimpleNamespace(start=0.0, end=1.0, text="hello")],
    )

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
    _install_fake_whisper(
        monkeypatch,
        [SimpleNamespace(start=0.0, end=1.0, text="hello")],
    )

    exit_code = transcribe.main(
        [str(audio_path), "--out", str(output_path)]
    )

    assert exit_code == 0
    assert output_path.is_file()


@pytest.mark.parametrize(
    "segments",
    [
        [],
        [SimpleNamespace(start=0.0, end=1.0, text="   \n")],
    ],
)
def test_no_usable_speech_fails_and_preserves_old_output(
    tmp_path, monkeypatch, capsys, segments
):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"irreplaceable old subtitles")
    monkeypatch.setattr(transcribe, "probe_duration", lambda _path: 5)
    _install_fake_whisper(monkeypatch, segments)

    exit_code = transcribe.main(
        [str(audio_path), "--out", str(output_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "未识别到可用语音" in captured.err
    assert "转写完成" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
    assert output_path.read_bytes() == b"irreplaceable old subtitles"
    assert set(output_path.parent.iterdir()) == {output_path}


def test_blank_segments_are_filtered_and_indices_are_contiguous(
    tmp_path, monkeypatch
):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    monkeypatch.setattr(transcribe, "probe_duration", lambda _path: 5)
    _install_fake_whisper(
        monkeypatch,
        [
            SimpleNamespace(start=0.0, end=0.5, text="  "),
            SimpleNamespace(start="1.0", end="2.25", text=" first "),
            SimpleNamespace(start=2.5, end=3.0, text="\n\t"),
            SimpleNamespace(start=3.25, end=4.0, text="second"),
        ],
    )

    assert transcribe.main(
        [str(audio_path), "--out", str(output_path)]
    ) == 0

    subtitles = list(srt.parse(output_path.read_text(encoding="utf-8")))
    assert [item.index for item in subtitles] == [1, 2]
    assert [item.content for item in subtitles] == ["first", "second"]
    assert subtitles[0].start == timedelta(seconds=1)


@pytest.mark.parametrize(
    "segment",
    [
        SimpleNamespace(start=0.0, end=1.0, text=None),
        SimpleNamespace(start=0.0, end=1.0, text=123),
        SimpleNamespace(start=True, end=2.0, text="text"),
        SimpleNamespace(start=0.0, end=True, text="text"),
        SimpleNamespace(start="nope", end=1.0, text="text"),
        SimpleNamespace(start=float("nan"), end=1.0, text="text"),
        SimpleNamespace(start=0.0, end=float("inf"), text="text"),
        SimpleNamespace(start=-0.1, end=1.0, text="text"),
        SimpleNamespace(start=1.0, end=1.0, text="text"),
        SimpleNamespace(start=2.0, end=1.0, text="text"),
        SimpleNamespace(start=0.0001, end=0.0002, text="text"),
        SimpleNamespace(start=0.0010, end=0.0011, text="text"),
    ],
)
def test_invalid_segment_fails_without_replacing_old_output_or_leaking_detail(
    tmp_path, monkeypatch, capsys, segment
):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"old target must survive")
    monkeypatch.setattr(transcribe, "probe_duration", lambda _path: 5)
    _install_fake_whisper(monkeypatch, [segment])

    exit_code = transcribe.main(
        [str(audio_path), "--out", str(output_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "转写结果无效" in captured.err
    assert "请" in captured.err
    assert "转写完成" not in captured.out + captured.err
    assert "nope" not in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert output_path.read_bytes() == b"old target must survive"
    assert set(output_path.parent.iterdir()) == {output_path}


def test_non_monotonic_segment_start_fails_and_preserves_old_output(
    tmp_path, monkeypatch, capsys
):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    output_path.parent.mkdir(parents=True)
    output_path.write_text("old", encoding="utf-8")
    monkeypatch.setattr(transcribe, "probe_duration", lambda _path: 5)
    _install_fake_whisper(
        monkeypatch,
        [
            SimpleNamespace(start=2.0, end=3.0, text="later"),
            SimpleNamespace(start=1.0, end=1.5, text="earlier"),
        ],
    )

    exit_code = transcribe.main(
        [str(audio_path), "--out", str(output_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "转写结果无效" in captured.err
    assert output_path.read_text(encoding="utf-8") == "old"
    assert set(output_path.parent.iterdir()) == {output_path}


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


def _assert_input_output_conflict(
    transcribe,
    input_path,
    output_path,
    monkeypatch,
    capsys,
):
    original_content = b"original audio bytes"
    input_path.write_bytes(original_content)
    probe_calls = []
    monkeypatch.setattr(
        transcribe,
        "probe_duration",
        lambda path: probe_calls.append(path) or 12,
    )
    state = _install_fake_whisper(monkeypatch, [])

    exit_code = transcribe.main(
        [str(input_path), "--out", str(output_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "不能覆盖源文件" in captured.err
    assert "请为 --out 指定另一个" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert probe_calls == []
    assert state["models"] == []
    assert input_path.read_bytes() == original_content


@pytest.mark.parametrize("path_form", ["lexical", "resolved"])
def test_rejects_input_as_output_before_probe_or_model(
    tmp_path, monkeypatch, capsys, path_form
):
    transcribe = _load_transcribe()
    input_path = tmp_path / "input.m4a"
    if path_form == "lexical":
        output_path = input_path
    else:
        detour = tmp_path / "detour"
        detour.mkdir()
        output_path = detour / ".." / input_path.name

    _assert_input_output_conflict(
        transcribe,
        input_path,
        output_path,
        monkeypatch,
        capsys,
    )


def test_rejects_symlink_to_input_as_output_before_probe_or_model(
    tmp_path, monkeypatch, capsys
):
    transcribe = _load_transcribe()
    input_path = tmp_path / "input.m4a"
    input_path.write_bytes(b"placeholder")
    output_path = tmp_path / "output.srt"
    try:
        output_path.symlink_to(input_path)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"当前平台不能创建 symlink：{error}")

    _assert_input_output_conflict(
        transcribe,
        input_path,
        output_path,
        monkeypatch,
        capsys,
    )

    assert output_path.is_symlink()


def test_rejects_hardlink_to_input_as_output_before_probe_or_model(
    tmp_path, monkeypatch, capsys
):
    transcribe = _load_transcribe()
    input_path = tmp_path / "input.m4a"
    input_path.write_bytes(b"placeholder")
    output_path = tmp_path / "output.srt"
    try:
        output_path.hardlink_to(input_path)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"当前平台不能创建 hardlink：{error}")

    _assert_input_output_conflict(
        transcribe,
        input_path,
        output_path,
        monkeypatch,
        capsys,
    )

    assert output_path.samefile(input_path)


def _segments_that_fail():
    raise RuntimeError("secret iterator failure")
    yield


@pytest.mark.parametrize("failure_stage", ["init", "transcribe", "iterate"])
def test_model_errors_exit_one_without_internal_details(
    tmp_path, monkeypatch, capsys, failure_stage
):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"old output")
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
    assert output_path.read_bytes() == b"old output"
    assert set(output_path.parent.iterdir()) == {output_path}


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


def test_atomic_write_closes_same_directory_temp_before_replace(
    tmp_path, monkeypatch
):
    transcribe = _load_transcribe()
    output_path = tmp_path / "output.srt"
    output_path.write_text("旧字幕", encoding="utf-8")
    state = {}
    real_named_temporary_file = tempfile.NamedTemporaryFile
    real_replace = Path.replace

    def recording_named_temporary_file(*args, **kwargs):
        temporary = real_named_temporary_file(*args, **kwargs)
        state["temporary"] = temporary
        state["temporary_path"] = Path(temporary.name)
        return temporary

    def recording_replace(path, target):
        assert state["temporary"].closed is True
        assert path.parent == output_path.parent
        state["replace_called"] = True
        return real_replace(path, target)

    monkeypatch.setattr(
        tempfile,
        "NamedTemporaryFile",
        recording_named_temporary_file,
    )
    monkeypatch.setattr(Path, "replace", recording_replace)

    transcribe._atomic_write_text(output_path, "新字幕：你好")

    assert state["replace_called"] is True
    assert output_path.read_text(encoding="utf-8") == "新字幕：你好"
    assert set(tmp_path.iterdir()) == {output_path}


class _FailingTemporaryFile:
    def __init__(self, path: Path, failure_stage: str):
        self.name = str(path)
        self._path = path
        self._failure_stage = failure_stage
        self._file = None

    def __enter__(self):
        self._file = self._path.open(
            "w",
            encoding="utf-8",
            newline="",
        )
        return self

    def write(self, content: str):
        written = self._file.write(content)
        if self._failure_stage == "write":
            raise OSError("secret write failure")
        return written

    def flush(self):
        self._file.flush()
        if self._failure_stage == "flush":
            raise OSError("secret flush failure")

    def __exit__(self, _type, _value, _traceback):
        self._file.close()
        return False


@pytest.mark.parametrize("failure_stage", ["write", "flush", "replace"])
def test_atomic_write_failure_preserves_old_target_and_removes_temp(
    tmp_path, monkeypatch, capsys, failure_stage
):
    transcribe = _load_transcribe()
    audio_path, output_path = _success_paths(tmp_path)
    output_path.parent.mkdir(parents=True)
    output_path.write_text("不可丢失的旧字幕", encoding="utf-8")
    monkeypatch.setattr(transcribe, "probe_duration", lambda _path: 12)
    _install_fake_whisper(
        monkeypatch,
        [SimpleNamespace(start=0.0, end=1.0, text="新字幕")],
    )
    temporary_path = output_path.parent / ".forced-output.srt.tmp"

    if failure_stage in {"write", "flush"}:
        monkeypatch.setattr(
            tempfile,
            "NamedTemporaryFile",
            lambda *args, **kwargs: _FailingTemporaryFile(
                temporary_path,
                failure_stage,
            ),
        )
    else:
        real_replace = Path.replace

        def fail_replace(path, target):
            if Path(target) == output_path:
                raise OSError("secret replace failure")
            return real_replace(path, target)

        monkeypatch.setattr(Path, "replace", fail_replace)

    exit_code = transcribe.main(
        [str(audio_path), "--out", str(output_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "无法写入 SRT 字幕" in captured.err
    assert "请检查" in captured.err
    assert "secret" not in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert output_path.read_text(encoding="utf-8") == "不可丢失的旧字幕"
    assert set(output_path.parent.iterdir()) == {output_path}
