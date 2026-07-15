from pathlib import Path
import json, sys
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

def test_item_dir_expands_home_and_creates(tmp_path):
    cfg = {"output_dir": str(tmp_path / "out")}
    d = common.item_dir(cfg, 'A/B: test', "2026-07-14")
    assert d.exists() and d.name == "2026-07-14-AB test"
