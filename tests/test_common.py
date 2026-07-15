from pathlib import Path
import json, sys
import subprocess
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import common

def test_sanitize_removes_windows_illegal_chars():
    assert common.sanitize_filename('How: to "win" <fast>?') == "How to win fast"

def test_sanitize_truncates_to_80():
    assert len(common.sanitize_filename("x" * 200)) <= 80

def test_defaults_when_no_config(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "REPO_ROOT", tmp_path)
    cfg = common.load_config()
    assert cfg["native_lang"] == "zh" and cfg["subtitle_layout"] == "original-top"

@pytest.mark.parametrize(
    ("contents", "problem"),
    [
        ("{", "不是有效的 JSON"),
        (json.dumps(["not", "an", "object"]), "顶层必须是 JSON 对象"),
    ],
)
def test_load_config_explains_invalid_config(
    tmp_path, monkeypatch, contents, problem
):
    config_path = tmp_path / "config.json"
    config_path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(common, "REPO_ROOT", tmp_path)

    with pytest.raises(SystemExit) as error:
        common.load_config()

    message = str(error.value)
    assert str(config_path) in message
    assert problem in message
    assert "请修正该文件" in message
    assert "复制 config.example.json 为 config.json" in message

def test_load_config_process_error_has_no_traceback(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("{", encoding="utf-8")
    scripts_dir = Path(common.__file__).resolve().parent
    command = (
        "from pathlib import Path\n"
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import common\n"
        "common.REPO_ROOT = Path(sys.argv[2])\n"
        "common.load_config()\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            command,
            str(scripts_dir),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode != 0
    assert str(config_path) in result.stderr
    assert "请修正该文件" in result.stderr
    assert "复制 config.example.json 为 config.json" in result.stderr
    assert "Traceback" not in result.stderr

def test_item_dir_expands_home_and_creates(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cfg = {"output_dir": "~/out"}
    d = common.item_dir(cfg, 'A/B: test', "2026-07-14")
    assert d.exists()
    assert d.parent == tmp_path / "out"
    assert d.name == "2026-07-14-AB test"
