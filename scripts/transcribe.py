"""使用 faster-whisper 在本地把音视频转写为 SRT。"""

import argparse
from datetime import timedelta
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence

from doctor import faster_whisper_guidance, find_ffmpeg
import srt


class TranscribeError(Exception):
    """可安全展示给命令行用户的转写错误。"""


class InvalidTranscriptionError(TranscribeError):
    """模型返回的字幕字段不适合安全发布。"""


class NoUsableSpeechError(TranscribeError):
    """模型没有返回任何非空语音内容。"""


class _ChineseArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(
            2,
            f"{self.prog}: 参数错误：{message}。"
            "请检查命令参数后重试。\n",
        )


def estimate_minutes(duration_s: float, model: str) -> int:
    """按模型相对实时速率粗估转写分钟数。"""
    rate = {"small": 0.3}.get(model, 0.3)
    return math.ceil(duration_s * rate / 60)


def _load_faster_whisper():
    try:
        import faster_whisper
    except Exception:
        return None
    return faster_whisper


def _find_ffprobe() -> str:
    system_ffprobe = shutil.which("ffprobe")
    if system_ffprobe:
        return system_ffprobe

    try:
        ffmpeg_path, _source = find_ffmpeg()
    except FileNotFoundError as error:
        raise FileNotFoundError("未找到 ffprobe") from error
    ffmpeg = Path(ffmpeg_path)
    probe_name = (
        "ffprobe.exe"
        if ffmpeg.name.lower().endswith(".exe")
        else "ffprobe"
    )
    sibling = ffmpeg.with_name(probe_name)
    if sibling.is_file():
        return str(sibling)
    raise FileNotFoundError("未找到 ffprobe")


def probe_duration(audio_path: Path) -> float:
    """用 ffprobe 读取媒体时长（秒）。"""
    message = (
        "无法读取音频时长，ffprobe 未能识别该文件。"
        "请确认输入是可播放的音频或视频，并检查 ffmpeg 是否已安装。"
    )
    try:
        command = [
            _find_ffprobe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(Path(audio_path)),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if result.returncode != 0:
            raise TranscribeError(message)
        duration = float(result.stdout.strip())
        if not math.isfinite(duration) or duration <= 0:
            raise TranscribeError(message)
        return duration
    except TranscribeError:
        raise
    except (OSError, UnicodeError, ValueError, OverflowError) as error:
        raise TranscribeError(message) from error


def _atomic_write_text(path: Path, content: str) -> None:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
        temporary_path.replace(path)
    except BaseException:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _paths_refer_to_same_file(input_path: Path, output_path: Path) -> bool:
    try:
        if input_path.resolve() == output_path.resolve():
            return True
    except (OSError, RuntimeError, ValueError):
        pass

    try:
        return input_path.samefile(output_path)
    except (OSError, ValueError):
        return False


def _render_segments(segments) -> str:
    subtitles = []
    previous_start = None
    iterator = iter(segments)
    while True:
        try:
            segment = next(iterator)
        except StopIteration:
            break

        try:
            text = segment.text
            if not isinstance(text, str):
                raise InvalidTranscriptionError
            content = text.strip()
            if not content:
                continue
            start = float(segment.start)
            end = float(segment.end)
            if (
                not math.isfinite(start)
                or not math.isfinite(end)
                or start < 0
                or start >= end
                or (previous_start is not None and start < previous_start)
            ):
                raise InvalidTranscriptionError
            subtitle = srt.Subtitle(
                index=len(subtitles) + 1,
                start=timedelta(seconds=start),
                end=timedelta(seconds=end),
                content=content,
            )
        except InvalidTranscriptionError:
            raise
        except Exception:
            raise InvalidTranscriptionError from None

        subtitles.append(subtitle)
        previous_start = start

    if not subtitles:
        raise NoUsableSpeechError(
            "未识别到可用语音，未生成字幕。"
            "请确认音频中有人声且可以正常播放后重试。"
        )

    try:
        return srt.compose(subtitles, reindex=False)
    except Exception:
        raise InvalidTranscriptionError from None


def _build_parser() -> argparse.ArgumentParser:
    parser = _ChineseArgumentParser(description="本地音视频转写为 SRT")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model", default="small")
    parser.add_argument("--lang", default="auto")
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    whisper = _load_faster_whisper()
    if whisper is None:
        print(
            "转写功能不可用：当前环境没有可用的 faster-whisper。"
            f"{faster_whisper_guidance()}",
            file=sys.stderr,
        )
        return 4

    try:
        input_exists = args.audio.is_file()
    except OSError:
        input_exists = False
    if not input_exists:
        print(
            f"找不到输入音频：{args.audio}。"
            "请确认路径指向现有的音频或视频文件后重试。",
            file=sys.stderr,
        )
        return 1

    if _paths_refer_to_same_file(args.audio, args.out):
        print(
            "输出路径与输入音频是同一文件，不能覆盖源文件。"
            "请为 --out 指定另一个 SRT 文件。",
            file=sys.stderr,
        )
        return 1

    try:
        duration_s = probe_duration(args.audio)
    except TranscribeError as error:
        print(str(error), file=sys.stderr)
        return 1
    estimated = estimate_minutes(duration_s, args.model)
    print(
        f"预计转写耗时约 {estimated} 分钟（{args.model} 模型）。",
        flush=True,
    )
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError):
        print(
            f"无法创建字幕输出目录：{args.out.parent}。"
            "请检查目录路径、权限和可用空间后重试。",
            file=sys.stderr,
        )
        return 1

    try:
        model = whisper.WhisperModel(args.model)
        language = None if args.lang == "auto" else args.lang
        segments, _info = model.transcribe(
            str(args.audio),
            language=language,
        )
        rendered_srt = _render_segments(segments)
    except NoUsableSpeechError as error:
        print(str(error), file=sys.stderr)
        return 1
    except InvalidTranscriptionError:
        print(
            "转写结果无效：字幕文字或时间轴不符合要求。"
            "请确认音频可播放后重试。",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "本地转写失败：模型加载或音频转写未完成。"
            "请确认音频可以播放；首次使用还需保持网络畅通以下载模型，"
            "然后重试。",
            file=sys.stderr,
        )
        return 1

    try:
        _atomic_write_text(args.out, rendered_srt)
    except (OSError, UnicodeError, ValueError):
        print(
            f"无法写入 SRT 字幕：{args.out}。"
            "请检查输出路径、目录权限和可用空间后重试。",
            file=sys.stderr,
        )
        return 1
    print(f"转写完成：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
