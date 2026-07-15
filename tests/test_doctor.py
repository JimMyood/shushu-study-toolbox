import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import common
import doctor


EXPECTED_KEYS = {
    "python",
    "yt_dlp",
    "ffmpeg",
    "faster_whisper",
    "output_dir_writable",
}
BOOLEAN_KEYS = {
    "python",
    "yt_dlp",
    "faster_whisper",
    "output_dir_writable",
}


def _fake_executable(tmp_path: Path, name: str = "ffmpeg") -> Path:
    executable = tmp_path / name
    executable.write_text("fake executable", encoding="utf-8")
    executable.chmod(0o755)
    return executable


def test_doctor_json_shape_values_and_success_exit(tmp_path, monkeypatch, capsys):
    fake_ffmpeg = _fake_executable(tmp_path)
    monkeypatch.setattr(
        doctor, "load_config", lambda: {"output_dir": str(tmp_path / "output")}
    )
    monkeypatch.setattr(
        doctor, "find_ffmpeg", lambda: (str(fake_ffmpeg), "system")
    )
    monkeypatch.setattr(doctor, "_can_import", lambda _name: True)

    exit_code = doctor.main(["--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert set(payload) == EXPECTED_KEYS
    assert all(type(payload[key]) is bool for key in BOOLEAN_KEYS)
    assert payload["ffmpeg"] in {"system", "static", "missing"}
    assert payload == {
        "python": True,
        "yt_dlp": True,
        "ffmpeg": "system",
        "faster_whisper": True,
        "output_dir_writable": True,
    }


def test_invalid_config_still_outputs_diagnostic_json(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.json"
    config_path.write_text("{", encoding="utf-8")
    fake_ffmpeg = _fake_executable(tmp_path)
    imported_modules = []

    monkeypatch.setattr(common, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        doctor, "find_ffmpeg", lambda: (str(fake_ffmpeg), "system")
    )

    def can_import(module_name):
        imported_modules.append(module_name)
        return True

    monkeypatch.setattr(doctor, "_can_import", can_import)

    exit_code = doctor.main(["--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 1
    assert set(payload) == EXPECTED_KEYS
    assert payload == {
        "python": True,
        "yt_dlp": True,
        "ffmpeg": "system",
        "faster_whisper": True,
        "output_dir_writable": False,
    }
    assert imported_modules == ["yt_dlp", "faster_whisper"]
    assert str(config_path) in captured.err
    assert "不是有效的 JSON" in captured.err
    assert "请修正该文件" in captured.err
    assert "Traceback" not in captured.err


def test_invalid_config_human_report_has_no_traceback(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.json"
    config_path.write_text("{", encoding="utf-8")
    fake_ffmpeg = _fake_executable(tmp_path)

    monkeypatch.setattr(common, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        doctor, "find_ffmpeg", lambda: (str(fake_ffmpeg), "system")
    )
    monkeypatch.setattr(doctor, "_can_import", lambda _name: True)

    exit_code = doctor.main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert str(config_path) in captured.err
    assert "不是有效的 JSON" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert "✅ Python" in captured.out
    assert "✅ yt-dlp" in captured.out
    assert "✅ ffmpeg" in captured.out
    assert "✅ faster-whisper" in captured.out
    assert "❌ config.json 的 output_dir 配置无效" in captured.out


def test_nul_output_dir_degrades_to_false(tmp_path, monkeypatch, capsys):
    fake_ffmpeg = _fake_executable(tmp_path)
    monkeypatch.setattr(doctor, "load_config", lambda: {"output_dir": "\0"})
    monkeypatch.setattr(
        doctor, "find_ffmpeg", lambda: (str(fake_ffmpeg), "system")
    )
    monkeypatch.setattr(doctor, "_can_import", lambda _name: True)

    exit_code = doctor.main(["--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 1
    assert set(payload) == EXPECTED_KEYS
    assert payload["output_dir_writable"] is False
    assert "Traceback" not in captured.out + captured.err


def test_find_ffmpeg_prefers_system_and_returns_tuple(
    tmp_path, monkeypatch
):
    fake_ffmpeg = _fake_executable(tmp_path)
    static_ffmpeg = ModuleType("static_ffmpeg")
    static_ffmpeg.run = SimpleNamespace(
        get_or_fetch_platform_executables_else_raise=lambda: pytest.fail(
            "找到系统 ffmpeg 后不应调用静态兜底"
        )
    )
    monkeypatch.setitem(sys.modules, "static_ffmpeg", static_ffmpeg)
    monkeypatch.setattr(
        doctor.shutil, "which", lambda name: str(fake_ffmpeg)
    )

    result = doctor.find_ffmpeg()

    assert isinstance(result, tuple)
    assert result == (str(fake_ffmpeg), "system")


def test_find_ffmpeg_uses_static_fallback(tmp_path, monkeypatch):
    fake_ffmpeg = _fake_executable(tmp_path, "static_ffmpeg")
    fake_ffprobe = _fake_executable(tmp_path, "static_ffprobe")
    static_ffmpeg = ModuleType("static_ffmpeg")
    static_ffmpeg.run = SimpleNamespace(
        get_or_fetch_platform_executables_else_raise=lambda: (
            str(fake_ffmpeg),
            str(fake_ffprobe),
        )
    )
    monkeypatch.setitem(sys.modules, "static_ffmpeg", static_ffmpeg)
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)

    result = doctor.find_ffmpeg()

    assert isinstance(result, tuple)
    assert result == (str(fake_ffmpeg), "static")


def test_find_ffmpeg_raises_chinese_error_when_missing(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    monkeypatch.setitem(sys.modules, "static_ffmpeg", None)

    with pytest.raises(FileNotFoundError) as error:
        doctor.find_ffmpeg()

    message = str(error.value)
    assert "未找到可用的 ffmpeg" in message
    assert "请安装系统 ffmpeg" in message
