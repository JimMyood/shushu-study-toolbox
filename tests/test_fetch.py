import importlib
import json
import math
from pathlib import Path
import re
import socket
import ssl
import sys

import pytest
import srt
import yt_dlp


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _fetch_module():
    return importlib.import_module("fetch")


def _fake_ydl(
    info,
    *,
    error=None,
    produce_artifact=True,
    subtitle_artifact="file",
    subtitle_text=(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n"
    ),
):
    state = {"instances": 0, "options": [], "calls": []}

    class FakeYoutubeDL:
        def __init__(self, options):
            self.params = dict(options)
            state["instances"] += 1
            state["options"].append(self.params)

        def __enter__(self):
            return self

        def __exit__(self, _type, _value, _traceback):
            return False

        def _produce_artifact(self):
            template = Path(self.params["outtmpl"])
            template.parent.mkdir(parents=True, exist_ok=True)
            if template.name == "audio.%(ext)s":
                artifact = Path(str(template).replace("%(ext)s", "m4a"))
                artifact.write_bytes(b"fake-m4a")
            elif template.name == "video.%(ext)s":
                artifact = Path(str(template).replace("%(ext)s", "mp4"))
                artifact.write_bytes(b"fake-mp4")
            elif template.name == "subs.orig.%(ext)s":
                artifact = template.parent / "subs.orig.downloaded.srt"
                if subtitle_artifact == "directory":
                    artifact.mkdir()
                elif subtitle_artifact == "symlink":
                    target = template.parent.parent / "outside-source.srt"
                    target.write_text(
                        "external subtitle", encoding="utf-8"
                    )
                    artifact.symlink_to(target)
                else:
                    artifact.write_text(
                        subtitle_text,
                        encoding="utf-8",
                    )

        def extract_info(self, url, download, process=True):
            state["calls"].append(
                {
                    "method": "extract_info",
                    "url": url,
                    "download": download,
                    "process": process,
                }
            )
            if error is not None:
                raise error
            if download and produce_artifact:
                self._produce_artifact()
            return dict(info)

        def process_ie_result(self, extracted_info, download=True):
            state["calls"].append(
                {
                    "method": "process_ie_result",
                    "info": extracted_info,
                    "download": download,
                }
            )
            if download and produce_artifact:
                self._produce_artifact()
            return extracted_info

    return FakeYoutubeDL, state


def test_build_video_opts_limits_height_and_uses_canonical_template(tmp_path):
    fetch = _fetch_module()

    options = fetch.build_opts("video", "1080", tmp_path)

    assert "height<=1080" in options["format"]
    assert Path(options["outtmpl"]).parent == tmp_path
    assert Path(options["outtmpl"]).name == "video.%(ext)s"


def test_build_video_opts_requests_only_mp4_compatible_formats(tmp_path):
    fetch = _fetch_module()

    options = fetch.build_opts("video", "720", tmp_path)

    assert options["merge_output_format"] == "mp4"
    assert "postprocessors" not in options
    assert "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]" in (
        options["format"]
    )
    assert "best[height<=720][ext=mp4]" in options["format"]


def test_build_video_opts_has_no_arbitrary_codec_fallback(tmp_path):
    fetch = _fetch_module()

    options = fetch.build_opts("video", "720", tmp_path)

    assert "bestvideo[height<=720]+bestaudio" not in options["format"]
    assert options["format"].count("/") == 1
    assert options["format"].endswith("best[height<=720][ext=mp4]")


def test_installed_yt_dlp_selector_rejects_webm_only_formats(tmp_path):
    fetch = _fetch_module()
    options = fetch.build_opts("video", "720", tmp_path)
    webm_only_formats = [
        {
            "format_id": "video-webm",
            "ext": "webm",
            "height": 720,
            "vcodec": "vp9",
            "acodec": "none",
            "url": "https://media.example.test/video.webm",
        },
        {
            "format_id": "audio-webm",
            "ext": "webm",
            "vcodec": "none",
            "acodec": "opus",
            "url": "https://media.example.test/audio.webm",
        },
    ]

    with yt_dlp.YoutubeDL({"quiet": True}) as downloader:
        selector = downloader.build_format_selector(options["format"])
        selected = downloader._select_formats(webm_only_formats, selector)

    assert selected == []


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
        (
            "This video is not available from your location due to "
            "geo restriction",
            "地区限制",
        ),
        (
            "The uploader has not made this video available in your "
            "country",
            "地区限制",
        ),
        ("Temporary failure in name resolution", "网络"),
        ("Unable to download API page: HTTP Error 503", "网络"),
        ("SSL: CERTIFICATE_VERIFY_FAILED", "网络"),
        ("certificate verify failed: unable to get local issuer", "网络"),
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


def test_classify_requested_format_unavailable_explains_mp4_options():
    fetch = _fetch_module()
    raw_message = "Requested format is not available"

    message = fetch.classify_error(RuntimeError(raw_message))

    assert "没有兼容 MP4" in message
    assert "--quality" not in message
    assert "换一个提供 MP4 兼容流的公开链接或来源" in message
    assert "请" in message
    assert raw_message not in message


def test_is_network_error_recognizes_macos_socket_gaierror():
    fetch = _fetch_module()
    error = socket.gaierror(
        socket.EAI_NONAME,
        "nodename nor servname provided, or not known",
    )

    assert fetch._is_network_error(error) is True


def test_classify_error_translates_macos_socket_gaierror():
    fetch = _fetch_module()
    error = socket.gaierror(
        socket.EAI_NONAME,
        "nodename nor servname provided, or not known",
    )

    message = fetch.classify_error(error)

    assert "网络连接失败" in message
    assert "请检查网络" in message
    assert "nodename nor servname" not in message


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


def test_select_subtitle_auto_chooses_single_official_english_track():
    fetch = _fetch_module()
    info = {
        "subtitles": {"en": [{"ext": "vtt"}]},
        "automatic_captions": {},
    }

    assert fetch.select_subtitle(info, "auto") == ("en", "official")


def test_select_subtitle_auto_matches_metadata_with_multiple_official_tracks():
    fetch = _fetch_module()
    info = {
        "language": "en-US",
        "subtitles": {
            "fr": [{"ext": "vtt"}],
            "en": [{"ext": "vtt"}],
            "ja": [{"ext": "vtt"}],
        },
    }

    assert fetch.select_subtitle(info, "auto") == ("en", "official")


def test_select_subtitle_auto_prefers_official_over_automatic():
    fetch = _fetch_module()
    info = {
        "original_language": "en",
        "subtitles": {"fr": [{"ext": "vtt"}]},
        "automatic_captions": {"en": [{"ext": "json3"}]},
    }

    assert fetch.select_subtitle(info, "auto") == ("fr", "official")


def test_select_subtitle_auto_uses_unique_automatic_when_no_official():
    fetch = _fetch_module()
    info = {
        "subtitles": {},
        "automatic_captions": {"de": [{"ext": "json3"}]},
    }

    assert fetch.select_subtitle(info, "auto") == (
        "de",
        "automatic",
    )


def test_select_subtitle_auto_prefers_orig_track_without_metadata():
    fetch = _fetch_module()
    info = {
        "automatic_captions": {
            "fr": [{"ext": "json3"}],
            "en-orig": [{"ext": "json3"}],
            "ja": [{"ext": "json3"}],
        }
    }

    assert fetch.select_subtitle(info, "auto") == (
        "en-orig",
        "automatic",
    )


def test_select_subtitle_auto_has_stable_fallback_and_excludes_live_chat():
    fetch = _fetch_module()
    info = {
        "subtitles": {
            "zh-Hant": [{"ext": "vtt"}],
            "live_chat": [{"ext": "json"}],
            "en": [{"ext": "vtt"}],
            "": [{"ext": "vtt"}],
        },
        "automatic_captions": {"aa": [{"ext": "json3"}]},
    }

    assert fetch.select_subtitle(info, "auto") == ("en", "official")
    assert fetch.available_subtitle_languages(info) == ["aa", "en", "zh-Hant"]


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
    assert state["instances"] == 1
    assert state["calls"] == [
        {
            "method": "extract_info",
            "url": "https://example.test/video",
            "download": False,
            "process": False,
        },
        {
            "method": "process_ie_result",
            "info": {
                "subtitles": {"en": [{"ext": "vtt"}]},
                "automatic_captions": {"en": [{"ext": "json3"}]},
            },
            "download": True,
        },
    ]
    download_options = state["options"][0]
    assert download_options["writesubtitles"] is True
    assert download_options["writeautomaticsub"] is False
    assert download_options["subtitleslangs"] == ["en"]
    assert {path.name for path in output_dir.iterdir()} == {
        "subs.orig.srt"
    }
    assert "Traceback" not in captured.out + captured.err


def test_cli_subs_removes_blank_cues_before_atomic_publish(
    tmp_path, capsys
):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    downloaded_srt = (
        "1\n00:00:00,100 --> 00:00:01,200\nFirst line\n\n"
        "2\n00:00:01,300 --> 00:00:01,700\n \t \n\n"
        "3\n00:00:02,000 --> 00:00:03,400\n"
        "Third line\ncontinues here\n"
    )
    factory, _state = _fake_ydl(
        {"subtitles": {"en": [{"ext": "srt"}]}},
        subtitle_text=downloaded_srt,
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
    expected = [
        (cue.index, cue.start, cue.end, cue.content)
        for cue in srt.parse(downloaded_srt)
        if cue.content.strip()
    ]
    published = list(srt.parse(subtitle_path.read_text(encoding="utf-8")))
    assert exit_code == 0
    assert [
        (cue.index, cue.start, cue.end, cue.content) for cue in published
    ] == expected
    assert all(cue.content.strip() for cue in published)
    srt_tools = importlib.import_module("srt_tools")
    assert srt_tools.validate_subtitles(subtitle_path) == 0
    assert "Traceback" not in captured.out + captured.err


def test_cli_subs_rejects_all_blank_cues_preserves_existing_and_cleans_temp(
    tmp_path, capsys
):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    subtitle_path = output_dir / "subs.orig.srt"
    previous_subtitle = (
        b"1\n00:00:00,000 --> 00:00:01,000\nExisting subtitle\n"
    )
    subtitle_path.write_bytes(previous_subtitle)
    factory, _state = _fake_ydl(
        {"subtitles": {"en": [{"ext": "srt"}]}},
        subtitle_text=(
            "1\n00:00:00,000 --> 00:00:01,000\n \t \n\n"
            "2\n00:00:01,100 --> 00:00:02,000\n   \n"
        ),
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
    assert exit_code == 1
    assert "字幕内容为空" in captured.err
    assert "请" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert subtitle_path.read_bytes() == previous_subtitle
    assert list(output_dir.glob(".fetch-*")) == []
    assert set(output_dir.iterdir()) == {subtitle_path}


def test_cli_subs_preflights_conflicting_final_slot_before_network(
    tmp_path, capsys
):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    subtitle_slot = output_dir / "subs.orig.srt"
    subtitle_slot.mkdir()
    factory, state = _fake_ydl(
        {"subtitles": {"en": [{"ext": "vtt"}]}}
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
    assert exit_code == 1
    assert state["instances"] == 0
    assert state["calls"] == []
    assert subtitle_slot.is_dir()
    assert "subs.orig.srt" in captured.err
    assert "目录" in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_cli_subs_publish_failure_preserves_existing_regular_file(
    tmp_path, monkeypatch, capsys
):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    subtitle_path = output_dir / "subs.orig.srt"
    previous = b"old subtitle bytes"
    subtitle_path.write_bytes(previous)
    factory, _state = _fake_ydl(
        {"subtitles": {"en": [{"ext": "vtt"}]}}
    )
    real_replace = fetch._replace_file

    def fail_subtitle_publish(source, destination):
        if destination == subtitle_path:
            raise OSError("secret subtitle publish failure")
        return real_replace(source, destination)

    monkeypatch.setattr(fetch, "_replace_file", fail_subtitle_publish)

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
    assert exit_code == 1
    assert subtitle_path.read_bytes() == previous
    assert list(output_dir.glob(".fetch-*")) == []
    assert "字幕发布失败" in captured.err
    assert "secret" not in captured.out + captured.err
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
    assert state["instances"] == 1
    download_options = state["options"][0]
    assert download_options["writesubtitles"] is False
    assert download_options["writeautomaticsub"] is True


def test_cli_subs_auto_logs_actual_language_and_source(tmp_path, capsys):
    fetch = _fetch_module()
    factory, _state = _fake_ydl(
        {
            "language": "en",
            "subtitles": {"en-US": [{"ext": "vtt"}]},
        }
    )

    exit_code = fetch.main(
        [
            "subs",
            "https://example.test/video",
            "--lang",
            "auto",
            "--out",
            str(tmp_path / "out"),
        ],
        ydl_factory=factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "en-US" in captured.out
    assert "官方字幕" in captured.out


def test_cli_subs_escapes_selected_language_for_exact_yt_dlp_regex(
    tmp_path,
):
    fetch = _fetch_module()
    language = "en.+"
    factory, state = _fake_ydl(
        {"subtitles": {language: [{"ext": "vtt"}]}}
    )

    exit_code = fetch.main(
        [
            "subs",
            "https://example.test/video",
            "--lang",
            language,
            "--out",
            str(tmp_path / "out"),
        ],
        ydl_factory=factory,
    )

    assert exit_code == 0
    assert state["instances"] == 1
    assert state["options"][0]["subtitleslangs"] == [re.escape(language)]


def test_cli_subs_rejects_directory_disguised_as_srt(tmp_path, capsys):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    factory, _state = _fake_ydl(
        {"subtitles": {"en": [{"ext": "vtt"}]}},
        subtitle_artifact="directory",
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
    assert exit_code == 1
    assert "没有生成唯一的 SRT 文件" in captured.err
    assert not (output_dir / "subs.orig.srt").exists()
    assert "Traceback" not in captured.out + captured.err


def test_cli_subs_rejects_symlinked_srt_candidate(tmp_path, capsys):
    probe_target = tmp_path / "probe-target"
    probe_link = tmp_path / "probe-link"
    probe_target.write_text("probe", encoding="utf-8")
    try:
        probe_link.symlink_to(probe_target)
    except (NotImplementedError, OSError):
        pytest.skip("当前平台不允许创建测试 symlink")
    probe_link.unlink()
    probe_target.unlink()

    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    factory, _state = _fake_ydl(
        {"subtitles": {"en": [{"ext": "vtt"}]}},
        subtitle_artifact="symlink",
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
    assert exit_code == 1
    assert "没有生成唯一的 SRT 文件" in captured.err
    assert not (output_dir / "subs.orig.srt").exists()
    assert (output_dir / "outside-source.srt").is_file()
    assert "Traceback" not in captured.out + captured.err


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
    assert state["instances"] == 1
    assert state["calls"] == [
        {
            "method": "extract_info",
            "url": "https://example.test/video",
            "download": False,
            "process": False,
        }
    ]
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
    assert state["calls"] == [
        {
            "method": "extract_info",
            "url": "https://example.test/video",
            "download": True,
            "process": True,
        }
    ]


def test_cli_audio_preflights_symlink_slot_before_network(tmp_path, capsys):
    target = tmp_path / "outside-audio.m4a"
    target.write_bytes(b"outside")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    audio_slot = output_dir / "audio.m4a"
    try:
        audio_slot.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("当前平台不允许创建测试 symlink")
    fetch = _fetch_module()
    factory, state = _fake_ydl({"title": "must not download"})

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
    assert state["instances"] == 0
    assert state["calls"] == []
    assert audio_slot.is_symlink()
    assert target.read_bytes() == b"outside"
    assert "符号链接" in captured.err
    assert "Traceback" not in captured.out + captured.err


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


def test_cli_video_preflights_metadata_slot_before_network(
    tmp_path, capsys
):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    old_video = output_dir / "video.mp4"
    old_video.write_bytes(b"old-video")
    metadata_slot = output_dir / "meta.json"
    metadata_slot.mkdir()
    factory, state = _fake_ydl({"title": "must not download"})

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

    captured = capsys.readouterr()
    assert exit_code == 1
    assert state["instances"] == 0
    assert state["calls"] == []
    assert "meta.json" in captured.err
    assert "目录" in captured.err
    assert old_video.read_bytes() == b"old-video"
    assert metadata_slot.is_dir()
    assert "Traceback" not in captured.out + captured.err


def test_cli_video_rolls_back_pair_when_second_publish_fails(
    tmp_path, monkeypatch, capsys
):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    video_path = output_dir / "video.mp4"
    metadata_path = output_dir / "meta.json"
    video_path.write_bytes(b"old-video")
    metadata_path.write_bytes(b"old-metadata")
    factory, _state = _fake_ydl({"title": "new"})
    real_replace = fetch._replace_file

    def fail_second_publish(source, destination):
        if (
            source.parent.name == "new"
            and source.name == "meta.json"
            and destination == metadata_path
        ):
            raise OSError("secret simulated second publish failure")
        return real_replace(source, destination)

    monkeypatch.setattr(fetch, "_replace_file", fail_second_publish)

    exit_code = fetch.main(
        [
            "video",
            "https://example.test/video",
            "--out",
            str(output_dir),
        ],
        ydl_factory=factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert video_path.read_bytes() == b"old-video"
    assert metadata_path.read_bytes() == b"old-metadata"
    assert list(output_dir.glob(".fetch-*")) == []
    assert "旧文件已恢复" in captured.err
    assert "secret" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


def test_cli_video_atomically_replaces_existing_pair_and_cleans_backups(
    tmp_path,
):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "video.mp4").write_bytes(b"old-video")
    (output_dir / "meta.json").write_bytes(b"old-metadata")
    factory, _state = _fake_ydl(
        {
            "title": "new lesson",
            "duration": 10,
            "upload_date": "20260715",
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
    assert json.loads(
        (output_dir / "meta.json").read_text(encoding="utf-8")
    )["title"] == "new lesson"
    assert {path.name for path in output_dir.iterdir()} == {
        "video.mp4",
        "meta.json",
    }


def test_cli_video_keeps_recovery_directory_when_rollback_fails(
    tmp_path, monkeypatch, capsys
):
    fetch = _fetch_module()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    video_path = output_dir / "video.mp4"
    metadata_path = output_dir / "meta.json"
    video_path.write_bytes(b"old-video")
    metadata_path.write_bytes(b"old-metadata")
    factory, _state = _fake_ydl({"title": "new"})
    real_replace = fetch._replace_file

    def fail_publish_and_video_restore(source, destination):
        if (
            source.parent.name == "new"
            and source.name == "meta.json"
            and destination == metadata_path
        ):
            raise OSError("secret publish failure")
        if (
            source.parent.name == "backups"
            and source.name == "video.mp4"
            and destination == video_path
        ):
            raise OSError("secret rollback failure")
        return real_replace(source, destination)

    monkeypatch.setattr(
        fetch, "_replace_file", fail_publish_and_video_restore
    )

    exit_code = fetch.main(
        [
            "video",
            "https://example.test/video",
            "--out",
            str(output_dir),
        ],
        ydl_factory=factory,
    )

    captured = capsys.readouterr()
    recovery_dirs = list(output_dir.glob(".fetch-*"))
    assert exit_code == 1
    assert len(recovery_dirs) == 1
    recovery_dir = recovery_dirs[0]
    assert (recovery_dir / "backups" / "video.mp4").read_bytes() == (
        b"old-video"
    )
    assert (recovery_dir / "RECOVERY.txt").is_file()
    assert str(recovery_dir) in captured.err
    assert "自动回滚也未完成" in captured.err
    assert "RECOVERY.txt" in captured.err
    assert "secret" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


def test_cli_video_reports_when_no_compatible_mp4_format(
    tmp_path, capsys
):
    fetch = _fetch_module()
    factory, _state = _fake_ydl(
        {},
        error=yt_dlp.utils.DownloadError(
            "Requested format is not available. Use --list-formats"
        ),
    )

    exit_code = fetch.main(
        [
            "video",
            "https://example.test/video",
            "--quality",
            "720",
            "--out",
            str(tmp_path / "out"),
        ],
        ydl_factory=factory,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "没有兼容 MP4" in captured.err
    assert "--quality" not in captured.err
    assert "请换一个提供 MP4 兼容流的公开链接或来源" in captured.err
    assert "确认链接有效" not in captured.err
    assert "Traceback" not in captured.out + captured.err


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


@pytest.mark.parametrize(
    "network_error",
    [
        TimeoutError("connection timed out"),
        ConnectionResetError("connection reset by peer"),
        ssl.SSLError("certificate verify failed"),
        OSError("Unable to download API page: HTTP Error 503"),
        OSError("SSL: CERTIFICATE_VERIFY_FAILED"),
    ],
)
def test_cli_routes_network_oserror_to_network_help(
    tmp_path, capsys, network_error
):
    fetch = _fetch_module()
    factory, _state = _fake_ydl({}, error=network_error)

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
    assert "输出目录" not in captured.err
    assert "Traceback" not in captured.out + captured.err


def test_cli_routes_macos_socket_gaierror_to_network_help(
    tmp_path, capsys
):
    fetch = _fetch_module()
    error = socket.gaierror(
        socket.EAI_NONAME,
        "nodename nor servname provided, or not known",
    )
    factory, _state = _fake_ydl({}, error=error)

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
    assert "输出目录" not in captured.err
    assert "Traceback" not in captured.out + captured.err


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
