import importlib
import json
import math
from pathlib import Path
import sys

import pytest
import yt_dlp


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _fetch_module():
    return importlib.import_module("fetch")


def _fake_ydl(info, *, error=None, produce_artifact=True):
    state = {"options": [], "calls": []}

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = dict(options)
            state["options"].append(self.options)

        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            return False

        def extract_info(self, url, download):
            state["calls"].append((url, download))
            if error is not None:
                raise error
            if download and produce_artifact:
                template = Path(self.options["outtmpl"])
                template.parent.mkdir(parents=True, exist_ok=True)
                processors = self.options.get("postprocessors", [])
                processor = processors[0]["key"] if processors else ""
                if processor == "FFmpegExtractAudio":
                    artifact = Path(
                        str(template).replace("%(ext)s", "m4a")
                    )
                    artifact.write_bytes(b"fake-m4a")
                elif processor == "FFmpegVideoConvertor":
                    artifact = Path(
                        str(template).replace("%(ext)s", "mp4")
                    )
                    artifact.write_bytes(b"fake-mp4")
                elif processor == "FFmpegSubtitlesConvertor":
                    language = self.options["subtitleslangs"][0]
                    artifact = template.parent / f"subs.orig.{language}.srt"
                    artifact.write_text(
                        "1\n00:00:00,000 --> 00:00:01,000\nHello\n",
                        encoding="utf-8",
                    )
            return dict(info)

    return FakeYoutubeDL, state


def test_build_video_opts_limits_height_and_uses_canonical_template(tmp_path):
    fetch = _fetch_module()

    options = fetch.build_opts("video", "1080", tmp_path)

    assert "height<=1080" in options["format"]
    assert Path(options["outtmpl"]).parent == tmp_path
    assert Path(options["outtmpl"]).name == "video.%(ext)s"


def test_build_video_opts_guarantees_mp4_output(tmp_path):
    fetch = _fetch_module()

    options = fetch.build_opts("video", "720", tmp_path)

    assert options["merge_output_format"] == "mp4"
    assert options["postprocessors"] == [
        {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
    ]


def test_build_video_opts_falls_back_when_native_mp4_is_unavailable(tmp_path):
    fetch = _fetch_module()

    options = fetch.build_opts("video", "720", tmp_path)

    assert (
        "/bestvideo[height<=720]+bestaudio/best[height<=720]"
        in options["format"]
    )


@pytest.mark.parametrize("quality", [None, "0", "高清"])
def test_build_video_opts_rejects_invalid_quality(tmp_path, quality):
    fetch = _fetch_module()

    with pytest.raises(ValueError, match="清晰度"):
        fetch.build_opts("video", quality, tmp_path)


def test_build_audio_opts_requests_best_audio_and_m4a_output(tmp_path):
    fetch = _fetch_module()

    options = fetch.build_opts("audio", None, tmp_path)

    assert options["format"].startswith("bestaudio")
    assert Path(options["outtmpl"]).parent == tmp_path
    assert Path(options["outtmpl"]).name == "audio.%(ext)s"
    assert options["postprocessors"] == [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "m4a",
            "preferredquality": "0",
        }
    ]


def test_build_subtitle_opts_skips_media_and_converts_to_srt(tmp_path):
    fetch = _fetch_module()

    options = fetch.build_opts("subs", None, tmp_path)

    assert options["skip_download"] is True
    assert options["subtitlesformat"] == "srt/best"
    assert Path(options["outtmpl"]).name == "subs.orig.%(ext)s"
    assert options["postprocessors"] == [
        {"key": "FFmpegSubtitlesConvertor", "format": "srt"}
    ]


def test_classify_error_translates_members_only_without_raw_error():
    fetch = _fetch_module()
    raw_message = "This video is available to Members"

    message = fetch.classify_error(RuntimeError(raw_message))

    assert "会员" in message
    assert "请" in message
    assert raw_message not in message


@pytest.mark.parametrize(
    ("raw_message", "expected"),
    [
        ("This video is not available in your country", "地区限制"),
        ("Unable to download webpage: connection timed out", "网络"),
    ],
)
def test_classify_error_translates_common_failures(raw_message, expected):
    fetch = _fetch_module()

    message = fetch.classify_error(RuntimeError(raw_message))

    assert expected in message
    assert "请" in message
    assert raw_message not in message


@pytest.mark.parametrize(
    ("raw_message", "expected"),
    [
        (
            "Join this channel to get access to members-only content",
            "会员",
        ),
        ("This content is geo-restricted", "地区限制"),
        ("Temporary failure in name resolution", "网络"),
    ],
)
def test_classify_error_recognizes_yt_dlp_message_variants(
    raw_message, expected
):
    fetch = _fetch_module()

    message = fetch.classify_error(RuntimeError(raw_message))

    assert expected in message
    assert "请" in message
    assert raw_message not in message


def test_classify_unknown_error_does_not_leak_traceback():
    fetch = _fetch_module()
    raw_message = "Traceback (most recent call last): secret-internal-path"

    message = fetch.classify_error(RuntimeError(raw_message))

    assert "下载失败" in message
    assert "请" in message
    assert "Traceback" not in message
    assert "secret-internal-path" not in message


def test_select_subtitle_prefers_official_language_variant_over_auto_exact():
    fetch = _fetch_module()
    info = {
        "subtitles": {"en-US": [{"ext": "vtt"}]},
        "automatic_captions": {"en": [{"ext": "vtt"}]},
    }

    selected = fetch.select_subtitle(info, "en")

    assert selected == ("en-US", "official")


def test_select_subtitle_falls_back_to_automatic_caption():
    fetch = _fetch_module()
    info = {
        "subtitles": {"fr": [{"ext": "vtt"}]},
        "automatic_captions": {"en": [{"ext": "json3"}]},
    }

    selected = fetch.select_subtitle(info, "en")

    assert selected == ("en", "automatic")


def test_select_subtitle_ignores_empty_tracks_and_reports_all_usable_languages():
    fetch = _fetch_module()
    info = {
        "subtitles": {
            "en": [],
            "ja": [{"ext": "vtt"}],
        },
        "automatic_captions": {
            "fr": [{"ext": "json3"}],
            "ja": [{"ext": "json3"}],
        },
    }

    selected = fetch.select_subtitle(info, "en")
    languages = fetch.available_subtitle_languages(info)

    assert selected is None
    assert languages == ["fr", "ja"]


def test_write_metadata_serializes_exact_schema_utf8_and_readable_date(
    tmp_path,
):
    fetch = _fetch_module()
    metadata_path = tmp_path / "meta.json"
    info = {
        "title": "原子入门",
        "uploader": "树树课堂",
        "duration": 95,
        "upload_date": "20240703",
        "ignored": "不会写入",
    }

    fetch.write_metadata(info, "https://example.test/watch/abc", metadata_path)

    raw = metadata_path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload == {
        "url": "https://example.test/watch/abc",
        "title": "原子入门",
        "uploader": "树树课堂",
        "duration_s": 95.0,
        "date": "2024-07-03",
    }
    assert type(payload["duration_s"]) is float
    assert "原子入门" in raw
    assert raw.endswith("\n")


@pytest.mark.parametrize(
    "raw_duration", [None, "unknown", float("nan"), True, -1]
)
def test_write_metadata_uses_finite_float_for_invalid_duration(
    tmp_path, raw_duration
):
    fetch = _fetch_module()
    metadata_path = tmp_path / "meta.json"

    fetch.write_metadata(
        {"duration": raw_duration, "upload_date": "not-a-date"},
        "https://example.test/video",
        metadata_path,
    )

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert type(payload["duration_s"]) is float
    assert payload["duration_s"] == 0.0
    assert math.isfinite(payload["duration_s"])
    assert payload["date"] == ""


def test_cli_subs_downloads_official_track_to_canonical_path(
    tmp_path, capsys
):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    factory, state = _fake_ydl(
        {
            "subtitles": {"en": [{"ext": "vtt"}]},
            "automatic_captions": {"en": [{"ext": "json3"}]},
        }
    )

    exit_code = fetch.main(
        [
            "subs",
            "https://example.test/video",
            "--lang",
            "en",
            "--out",
            str(output_dir),
        ],
        ydl_factory=factory,
    )

    captured = capsys.readouterr()
    subtitle_path = output_dir / "subs.orig.srt"
    assert exit_code == 0
    assert subtitle_path.read_text(encoding="utf-8").endswith("Hello\n")
    assert state["calls"] == [
        ("https://example.test/video", False),
        ("https://example.test/video", True),
    ]
    download_options = state["options"][1]
    assert download_options["writesubtitles"] is True
    assert download_options["writeautomaticsub"] is False
    assert download_options["subtitleslangs"] == ["en"]
    assert {path.name for path in output_dir.iterdir()} == {
        "subs.orig.srt"
    }
    assert "Traceback" not in captured.out + captured.err


def test_cli_subs_uses_automatic_caption_only_after_official_miss(
    tmp_path,
):
    fetch = _fetch_module()
    factory, state = _fake_ydl(
        {
            "subtitles": {"fr": [{"ext": "vtt"}]},
            "automatic_captions": {"en": [{"ext": "json3"}]},
        }
    )

    exit_code = fetch.main(
        [
            "subs",
            "https://example.test/video",
            "--lang",
            "en",
            "--out",
            str(tmp_path / "out"),
        ],
        ydl_factory=factory,
    )

    assert exit_code == 0
    download_options = state["options"][1]
    assert download_options["writesubtitles"] is False
    assert download_options["writeautomaticsub"] is True


def test_cli_subs_returns_exit_3_and_lists_languages_when_target_missing(
    tmp_path, capsys
):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    factory, state = _fake_ydl(
        {
            "subtitles": {"ja": [{"ext": "vtt"}]},
            "automatic_captions": {"fr": [{"ext": "json3"}]},
        }
    )

    exit_code = fetch.main(
        [
            "subs",
            "https://example.test/video",
            "--lang",
            "en",
            "--out",
            str(output_dir),
        ],
        ydl_factory=factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 3
    assert state["calls"] == [("https://example.test/video", False)]
    assert "未找到 en 字幕" in captured.err
    assert "fr, ja" in captured.err
    assert "请" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert not (output_dir / "subs.orig.srt").exists()


def test_cli_audio_produces_only_canonical_audio_file(tmp_path):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    factory, state = _fake_ydl({"title": "Short lesson"})

    exit_code = fetch.main(
        [
            "audio",
            "https://example.test/video",
            "--out",
            str(output_dir),
        ],
        ydl_factory=factory,
    )

    assert exit_code == 0
    assert (output_dir / "audio.m4a").read_bytes() == b"fake-m4a"
    assert {path.name for path in output_dir.iterdir()} == {"audio.m4a"}
    assert state["calls"] == [("https://example.test/video", True)]


def test_cli_video_produces_canonical_video_and_metadata(tmp_path):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    factory, state = _fake_ydl(
        {
            "title": "原子入门",
            "uploader": "树树课堂",
            "duration": 95,
            "upload_date": "20240703",
        }
    )

    exit_code = fetch.main(
        [
            "video",
            "https://example.test/video",
            "--quality",
            "720",
            "--out",
            str(output_dir),
        ],
        ydl_factory=factory,
    )

    assert exit_code == 0
    assert (output_dir / "video.mp4").read_bytes() == b"fake-mp4"
    payload = json.loads(
        (output_dir / "meta.json").read_text(encoding="utf-8")
    )
    assert payload == {
        "url": "https://example.test/video",
        "title": "原子入门",
        "uploader": "树树课堂",
        "duration_s": 95.0,
        "date": "2024-07-03",
    }
    assert {path.name for path in output_dir.iterdir()} == {
        "meta.json",
        "video.mp4",
    }
    assert "height<=720" in state["options"][0]["format"]


def test_cli_catches_yt_dlp_download_error_without_traceback(
    tmp_path, capsys
):
    fetch = _fetch_module()
    raw_message = (
        "Unable to download webpage: connection timed out; "
        "Traceback secret-path"
    )
    factory, _state = _fake_ydl(
        {}, error=yt_dlp.utils.DownloadError(raw_message)
    )

    exit_code = fetch.main(
        [
            "audio",
            "https://example.test/video",
            "--out",
            str(tmp_path / "out"),
        ],
        ydl_factory=factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "网络连接失败" in captured.err
    assert "请检查网络" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert "secret-path" not in captured.out + captured.err


def test_cli_reports_missing_final_artifact_and_cleans_temporary_files(
    tmp_path, capsys
):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    factory, _state = _fake_ydl({}, produce_artifact=False)

    exit_code = fetch.main(
        [
            "audio",
            "https://example.test/video",
            "--out",
            str(output_dir),
        ],
        ydl_factory=factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "没有生成 audio.m4a" in captured.err
    assert "请更新 yt-dlp" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert list(output_dir.iterdir()) == []


def test_cli_parameter_error_is_chinese_actionable_and_traceback_free(
    tmp_path, capsys
):
    fetch = _fetch_module()

    with pytest.raises(SystemExit) as error:
        fetch.main(
            [
                "video",
                "https://example.test/video",
                "--quality",
                "高清",
                "--out",
                str(tmp_path / "out"),
            ]
        )

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert "参数错误" in captured.err
    assert "视频清晰度必须是正整数" in captured.err
    assert "请检查命令参数" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_cli_reports_unwritable_output_path_without_traceback(
    tmp_path, capsys
):
    fetch = _fetch_module()
    output_path = tmp_path / "already-a-file"
    output_path.write_text("blocking file", encoding="utf-8")
    factory, _state = _fake_ydl({})

    exit_code = fetch.main(
        [
            "audio",
            "https://example.test/video",
            "--out",
            str(output_path),
        ],
        ydl_factory=factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "无法写入输出目录" in captured.err
    assert "请检查路径" in captured.err
    assert "Traceback" not in captured.out + captured.err
