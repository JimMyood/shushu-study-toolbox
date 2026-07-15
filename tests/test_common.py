from pathlib import Path
import json, os, sys
import subprocess
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import common

def test_sanitize_removes_windows_illegal_chars():
    assert common.sanitize_filename('How: to "win" <fast>?') == "How to win fast"

def test_sanitize_truncates_to_80():
    assert len(common.sanitize_filename("x" * 200)) <= 80


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Lesson...   ", "Lesson"),
        ("name. ", "name"),
        ("x" * 79 + "..", "x" * 79),
        ("...   ", ""),
    ],
)
def test_sanitize_removes_windows_trailing_dots_and_spaces(raw, expected):
    assert common.sanitize_filename(raw) == expected

def test_defaults_when_no_config(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "REPO_ROOT", tmp_path)
    cfg = common.load_config()
    assert cfg["native_lang"] == "zh" and cfg["subtitle_layout"] == "original-top"


def test_missing_config_is_detected_by_read_without_exists_precheck(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / "config.json"

    class ConfigPathDouble:
        def exists(self):
            raise AssertionError("load_config must not precheck with exists")

        def read_text(self, **_kwargs):
            raise FileNotFoundError("simulated race: config disappeared")

        def __str__(self):
            return str(config_path)

    class RepoRootDouble:
        def __truediv__(self, name):
            assert name == "config.json"
            return ConfigPathDouble()

    monkeypatch.setattr(common, "REPO_ROOT", RepoRootDouble())

    config = common.load_config()

    captured = capsys.readouterr()
    assert config == common.DEFAULTS
    assert "复制 config.example.json 为 config.json 可自定义" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    "read_error",
    [
        PermissionError("simulated unreadable file"),
        OSError(13, "simulated parent directory not traversable"),
    ],
)
def test_config_read_permission_errors_do_not_call_exists_or_leak_details(
    tmp_path, monkeypatch, read_error
):
    config_path = tmp_path / "blocked" / "config.json"

    class ConfigPathDouble:
        def exists(self):
            raise AssertionError("load_config must not precheck with exists")

        def read_text(self, **_kwargs):
            raise read_error

        def __str__(self):
            return str(config_path)

    class RepoRootDouble:
        def __truediv__(self, name):
            assert name == "config.json"
            return ConfigPathDouble()

    monkeypatch.setattr(common, "REPO_ROOT", RepoRootDouble())

    with pytest.raises(SystemExit) as error:
        common.load_config()

    message = str(error.value)
    assert str(config_path) in message
    assert "无法读取" in message
    assert "UTF-8" in message
    assert "当前用户可读" in message
    assert "simulated" not in message
    assert "PermissionError" not in message

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


@pytest.mark.parametrize("failure_kind", ["invalid_utf8", "unreadable"])
def test_load_config_translates_read_failures_to_actionable_chinese(
    tmp_path, monkeypatch, failure_kind
):
    config_path = tmp_path / "config.json"
    if failure_kind == "invalid_utf8":
        config_path.write_bytes(b'{"native_lang":"\xff"}')
    else:
        config_path.mkdir()
    monkeypatch.setattr(common, "REPO_ROOT", tmp_path)

    with pytest.raises(SystemExit) as error:
        common.load_config()

    message = str(error.value)
    assert str(config_path) in message
    assert "无法读取" in message
    assert "UTF-8" in message
    assert "当前用户可读" in message
    assert "复制 config.example.json 为 config.json" in message
    assert "UnicodeDecodeError" not in message
    assert "IsADirectoryError" not in message

@pytest.mark.parametrize(
    "failure_kind", ["invalid_json", "invalid_utf8", "unreadable"]
)
def test_load_config_process_error_has_no_traceback(tmp_path, failure_kind):
    config_path = tmp_path / "config.json"
    if failure_kind == "invalid_json":
        config_path.write_text("{", encoding="utf-8")
    elif failure_kind == "invalid_utf8":
        config_path.write_bytes(b'{"native_lang":"\xff"}')
    else:
        config_path.mkdir()
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
    assert "请" in result.stderr
    assert "复制 config.example.json 为 config.json" in result.stderr
    assert "Traceback" not in result.stderr
    assert "UnicodeDecodeError" not in result.stderr
    assert "IsADirectoryError" not in result.stderr

def test_item_dir_expands_home_and_creates(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cfg = {"output_dir": "~/out"}
    d = common.item_dir(cfg, 'A/B: test', "2026-07-14")
    assert d.exists()
    assert d.parent == tmp_path / "out"
    assert d.name == "2026-07-14-AB test"


def test_prepare_cli_calls_item_dir_and_emits_all_config_values(
    tmp_path, monkeypatch, capsys
):
    configured = {
        "output_dir": "~/中文资料",
        "native_lang": "zh-Hans",
        "source_lang": "en",
        "whisper_model": "medium",
        "video_quality": "720",
        "subtitle_layout": "translation-top",
    }
    called = {}
    expected_dir = tmp_path / "prepared"
    monkeypatch.setattr(common, "load_config", lambda: configured.copy())

    def fake_item_dir(config, title, date_str):
        called.update(
            config=config.copy(), title=title, date_str=date_str
        )
        expected_dir.mkdir()
        return expected_dir

    monkeypatch.setattr(common, "item_dir", fake_item_dir)

    exit_code = common.main(
        [
            "prepare",
            "--title",
            "原子 / 入门",
            "--date",
            "2026-07-15",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert called == {
        "config": configured,
        "title": "原子 / 入门",
        "date_str": "2026-07-15",
    }
    assert payload == {
        "item_dir": str(expected_dir),
        "output_dir": str(Path(configured["output_dir"]).expanduser()),
        "native_lang": "zh-Hans",
        "source_lang": "en",
        "whisper_model": "medium",
        "video_quality": "720",
        "subtitle_layout": "translation-top",
    }
    assert "中文资料" in captured.out
    assert captured.err == ""


def test_prepare_cli_really_expands_home_sanitizes_and_outputs_utf8(
    tmp_path, monkeypatch, capsys
):
    fake_home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_home.mkdir()
    (repo_root / "config.json").write_text(
        json.dumps(
            {
                "output_dir": "~/学习素材",
                "native_lang": "zh",
                "source_lang": "en",
                "whisper_model": "tiny",
                "video_quality": "480",
                "subtitle_layout": "original-top",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(common, "REPO_ROOT", repo_root)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    assert common.main(
        [
            "prepare",
            "--title",
            "中文 / 课程: 第一课",
            "--date",
            "2026-07-15",
        ]
    ) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    expected = fake_home / "学习素材" / "2026-07-15-中文 课程 第一课"
    assert Path(payload["item_dir"]) == expected
    assert payload["output_dir"] == str(fake_home / "学习素材")
    assert expected.is_dir()
    assert "中文" in captured.out
    assert captured.err == ""


def test_prepare_cli_rejects_invalid_date_in_chinese_without_traceback(
    capsys,
):
    with pytest.raises(SystemExit) as error:
        common.main(
            [
                "prepare",
                "--title",
                "lesson",
                "--date",
                "2026-02-30",
            ]
        )

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert "参数错误" in captured.err
    assert "YYYY-MM-DD" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_prepare_subprocess_writes_utf8_json_when_stdio_is_ascii(tmp_path):
    repo_root = tmp_path / "repo"
    output = tmp_path / "中文输出"
    repo_root.mkdir()
    (repo_root / "config.json").write_text(
        json.dumps(
            {
                "output_dir": str(output),
                "native_lang": "zh",
                "source_lang": "en",
                "whisper_model": "small",
                "video_quality": "720",
                "subtitle_layout": "original-top",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    scripts_dir = Path(common.__file__).resolve().parent
    command = (
        "from pathlib import Path\n"
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import common\n"
        "common.REPO_ROOT = Path(sys.argv[2])\n"
        "raise SystemExit(common.main(['prepare','--title','中文课程',"
        "'--date','2026-07-15']))\n"
    )
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "ascii"

    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            command,
            str(scripts_dir),
            str(repo_root),
        ],
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout.decode("utf-8"))
    assert payload["item_dir"].endswith("2026-07-15-中文课程")
    assert payload["native_lang"] == "zh"
    assert b"Traceback" not in result.stdout + result.stderr


def test_prepare_rejects_unencodable_surrogate_without_traceback(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config = {
        "output_dir": str(tmp_path / "out"),
        "native_lang": "\ud800",
        "source_lang": "en",
        "whisper_model": "small",
        "video_quality": "720",
        "subtitle_layout": "original-top",
    }
    (repo_root / "config.json").write_text(
        json.dumps(config, ensure_ascii=True), encoding="utf-8"
    )
    scripts_dir = Path(common.__file__).resolve().parent
    command = (
        "from pathlib import Path\n"
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import common\n"
        "common.REPO_ROOT = Path(sys.argv[2])\n"
        "raise SystemExit(common.main(['prepare','--title','lesson',"
        "'--date','2026-07-15']))\n"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            command,
            str(scripts_dir),
            str(repo_root),
        ],
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "无法输出配置 JSON" in result.stderr.decode("utf-8")
    assert b"Traceback" not in result.stdout + result.stderr


def test_workflow_docs_route_real_config_values_through_prepare():
    repo_root = Path(__file__).resolve().parent.parent
    skill = (repo_root / "SKILL.md").read_text(encoding="utf-8")
    download = (repo_root / "tools" / "download.md").read_text(
        encoding="utf-8"
    )
    subtitle = (repo_root / "tools" / "subtitle.md").read_text(
        encoding="utf-8"
    )
    combined = "\n".join((skill, download, subtitle))

    assert "common.py prepare" in skill
    for name in (
        "item_dir",
        "output_dir",
        "native_lang",
        "source_lang",
        "whisper_model",
        "video_quality",
        "subtitle_layout",
    ):
        assert name in skill
    assert "common.py prepare" in download
    assert "--quality \"$VIDEO_QUALITY\"" in download
    assert "common.py prepare" in subtitle
    assert "--lang \"$SOURCE_LANG\"" in subtitle
    assert "--model \"$WHISPER_MODEL\"" in subtitle
    assert "--lang \"$SOURCE_LANG\"" in subtitle
    assert "用户明确" in combined and "临时覆盖" in combined
    assert "$HOME/ShushuStudy" not in combined
    assert "--quality 1080" not in download
    assert "--model small" not in subtitle
    assert "python3" not in combined


def test_download_and_subtitle_reuse_current_pipeline_prepare_json():
    repo_root = Path(__file__).resolve().parent.parent

    for name in ("download", "subtitle"):
        manual = (repo_root / "tools" / f"{name}.md").read_text(
            encoding="utf-8"
        )
        assert "本流水线已有 prepare JSON" in manual, name
        assert "只有独立启动、尚未 prepare 时" in manual, name


def test_downstream_manuals_reuse_prepared_item_dir_and_config():
    repo_root = Path(__file__).resolve().parent.parent
    manuals = {
        name: (repo_root / "tools" / f"{name}.md").read_text(
            encoding="utf-8"
        )
        for name in ("translate", "embed", "notes", "digest")
    }

    for name, manual in manuals.items():
        assert "common.py prepare" in manual, name
        assert "复用" in manual, name
        assert "$HOME/ShushuStudy" not in manual, name
        assert "mkdir" not in manual, name
    assert '--layout "$SUBTITLE_LAYOUT"' in manuals["translate"]
    assert "native_lang" in manuals["notes"]
    assert "native_lang" in manuals["digest"]


def test_readme_script_examples_use_prepare_and_config_values():
    repo_root = Path(__file__).resolve().parent.parent
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert "python scripts/common.py prepare" in readme
    assert "逐条模板，不是可整段执行的 Shell 脚本" in readme
    assert '--quality "<video_quality>"' in readme
    assert '--lang "<source_lang>"' in readme
    assert '--out "<item_dir>"' in readme
    assert "$HOME/ShushuStudy/example" not in readme
    assert "--quality 1080" not in readme


def test_readme_setup_selects_supported_transcription_python_before_venv():
    repo_root = Path(__file__).resolve().parent.parent
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert "Python 3.10–3.13" in readme
    assert "Python 3.13 is recommended" in readme
    assert "py -0p" in readme
    assert "PYTHON_BIN" in readme
    assert "PYTHON_VERSION" in readme
    assert "python3.13 -m venv" not in readme
    assert "py -3.13 -m venv" not in readme
    assert "source .venv/bin/activate\npython -m pip" in readme
    assert ".venv\\Scripts\\activate\npython -m pip" in readme


def test_skill_and_manuals_use_active_python_and_version_aware_exit4():
    repo_root = Path(__file__).resolve().parent.parent
    paths = [repo_root / "SKILL.md", *sorted((repo_root / "tools").glob("*.md"))]
    contents = {path.name: path.read_text(encoding="utf-8") for path in paths}

    for name, content in contents.items():
        assert "python3 scripts/" not in content, name
        assert "python3 -m" not in content, name
        assert "py -3.13" not in content, name
    subtitle = contents["subtitle.md"]
    assert "与当前解释器匹配的恢复指引" in subtitle
    assert "Python 3.13 指引" not in subtitle
