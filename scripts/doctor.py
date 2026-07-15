"""检查树树工具箱的本地运行环境。"""

import argparse
from contextlib import redirect_stdout
import importlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Sequence

from common import load_config


def find_ffmpeg() -> tuple[str, str]:
    """返回可用 ffmpeg 的路径及来源，优先使用系统版本。"""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg, "system"

    try:
        from static_ffmpeg import run as static_ffmpeg_run

        static_path, _ = (
            static_ffmpeg_run.get_or_fetch_platform_executables_else_raise()
        )
    except Exception as error:
        raise FileNotFoundError(
            "未找到可用的 ffmpeg。请安装系统 ffmpeg，"
            "或运行 `python -m pip install static-ffmpeg` 启用静态版兜底。"
        ) from error

    return str(Path(static_path)), "static"


def _can_import(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
    except Exception:
        return False
    return True


def _check_output_dir(output_dir: Path) -> bool:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_dir,
            prefix=".shushu-doctor-",
        ) as probe:
            probe.write("ok")
            probe.flush()
    except (OSError, ValueError):
        return False
    return True


def _ffmpeg_install_command() -> str:
    if sys.platform == "darwin":
        return "brew install ffmpeg"
    if sys.platform.startswith("win"):
        return "winget install ffmpeg"
    return "sudo apt install ffmpeg"


def _python_313_install_command() -> str:
    if sys.platform == "darwin":
        return "brew install python@3.13"
    if sys.platform.startswith("win"):
        return "winget install Python.Python.3.13"
    return "sudo apt install python3.13 python3.13-venv"


def _collect_checks(config: dict) -> tuple[dict, str | None, Path | None]:
    ffmpeg_path = None
    try:
        ffmpeg_path, ffmpeg_source = find_ffmpeg()
    except FileNotFoundError:
        ffmpeg_source = "missing"

    try:
        output_dir = Path(config["output_dir"]).expanduser()
    except (KeyError, TypeError, ValueError):
        output_dir = None

    checks = {
        "python": sys.version_info >= (3, 10),
        "yt_dlp": _can_import("yt_dlp"),
        "ffmpeg": ffmpeg_source,
        "faster_whisper": _can_import("faster_whisper"),
        "output_dir_writable": (
            _check_output_dir(output_dir) if output_dir is not None else False
        ),
    }
    return checks, ffmpeg_path, output_dir


def _all_checks_pass(checks: dict) -> bool:
    return bool(
        checks["python"]
        and checks["yt_dlp"]
        and checks["ffmpeg"] != "missing"
        and checks["faster_whisper"]
        and checks["output_dir_writable"]
    )


def _load_config_for_checks() -> dict:
    try:
        return load_config()
    except SystemExit as error:
        print(str(error), file=sys.stderr)
        return {"output_dir": None}


def _print_human_report(
    checks: dict, ffmpeg_path: str | None, output_dir: Path | None
) -> None:
    python_version = ".".join(str(part) for part in sys.version_info[:3])
    if checks["python"]:
        print(f"✅ Python {python_version}（要求 ≥ 3.10）")
    else:
        print(
            f"❌ 当前 Python {python_version} 低于 3.10。"
            "请安装 Python 3.10 或更高版本后重新创建 venv。"
        )

    if checks["yt_dlp"]:
        print("✅ yt-dlp 可导入")
    else:
        print(
            "❌ 未找到 yt-dlp，下载功能不可用。"
            "请运行 `python -m pip install yt-dlp`。"
        )

    if checks["ffmpeg"] != "missing":
        print(f"✅ ffmpeg（{checks['ffmpeg']}）：{ffmpeg_path}")
    else:
        print(
            "❌ 未找到可用的 ffmpeg，视频合成功能不可用。"
            f"请运行 `{_ffmpeg_install_command()}`；"
            "pip 已含 static-ffmpeg 兜底，通常无需手装。"
        )

    if checks["faster_whisper"]:
        print("✅ faster-whisper 可导入")
    elif sys.version_info >= (3, 14):
        print(
            "❌ 转写功能不可用。Python 3.14 暂无该包，可 "
            f"`{_python_313_install_command()}` 后用 3.13 建 venv；"
            "不用转写功能可忽略。"
        )
    else:
        print(
            "❌ 转写功能不可用。"
            "请运行 `python -m pip install faster-whisper`；"
            "不用转写功能可忽略。"
        )

    if checks["output_dir_writable"]:
        print(f"✅ 输出目录可写：{output_dir}")
    elif output_dir is None:
        print(
            "❌ config.json 的 output_dir 配置无效。"
            "请把它改为字符串形式的可写目录。"
        )
    else:
        print(
            f"❌ 输出目录不可写：{output_dir}。"
            "请检查目录权限，或在 config.json 中把 output_dir 改为可写目录。"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查树树工具箱运行环境")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    if args.json:
        with redirect_stdout(sys.stderr):
            config = _load_config_for_checks()
            checks, ffmpeg_path, output_dir = _collect_checks(config)
        print(json.dumps(checks, ensure_ascii=False))
    else:
        config = _load_config_for_checks()
        checks, ffmpeg_path, output_dir = _collect_checks(config)
        _print_human_report(checks, ffmpeg_path, output_dir)

    return 0 if _all_checks_pass(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
