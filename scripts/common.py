"""树树工具箱各脚本共用的配置与路径工具。"""

import argparse
from contextlib import redirect_stdout
from datetime import date, datetime
import json
import sys
import unicodedata
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "output_dir": "~/ShushuStudy",
    "native_lang": "zh",
    "source_lang": "auto",
    "whisper_model": "small",
    "video_quality": "1080",
    "subtitle_layout": "original-top",
}

_WINDOWS_ILLEGAL_CHARS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


def load_config() -> dict:
    """读取仓库配置，并以默认值补齐未配置的字段。"""
    config_path = REPO_ROOT / "config.json"
    try:
        raw_config = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print("复制 config.example.json 为 config.json 可自定义")
        return DEFAULTS.copy()
    except (OSError, UnicodeError):
        raise SystemExit(
            f"无法读取配置文件 {config_path}。"
            "请确认它是 UTF-8 文本且当前用户可读，"
            "或删除后复制 config.example.json 为 config.json。"
        ) from None

    try:
        user_config = json.loads(raw_config)
    except json.JSONDecodeError:
        raise SystemExit(
            f"配置文件 {config_path} 不是有效的 JSON。"
            "请修正该文件，或删除后复制 config.example.json 为 config.json。"
        ) from None

    if not isinstance(user_config, dict):
        raise SystemExit(
            f"配置文件 {config_path} 顶层必须是 JSON 对象。"
            "请修正该文件，或删除后复制 config.example.json 为 config.json。"
        )

    config = DEFAULTS.copy()
    config.update(user_config)
    return config


def sanitize_filename(name: str) -> str:
    """移除跨平台非法字符，整理空白并限制文件名长度。"""
    cleaned = "".join(
        char
        for char in name
        if char not in _WINDOWS_ILLEGAL_CHARS
        and unicodedata.category(char) != "Cc"
    )
    # Windows 会忽略文件名尾部的点和空格，不先清理会导致
    # 路径判断与真实目录名不一致。截断后再清一次避免第 80 位是点。
    cleaned = " ".join(cleaned.split()).rstrip(" .")[:80].rstrip(" .")
    device_name = cleaned.partition(".")[0].upper()
    if device_name in _WINDOWS_RESERVED_NAMES:
        prefixed = f"_{cleaned}"
        if len(prefixed) <= 80:
            return prefixed

        stem, separator, extension = prefixed.rpartition(".")
        stem_limit = 80 - len(separator) - len(extension)
        protected_device_length = len(prefixed.partition(".")[0])
        if separator and stem_limit >= protected_device_length:
            return f"{stem[:stem_limit]}{separator}{extension}"
        return prefixed[:80]
    return cleaned


def item_dir(config: dict, title: str, date_str: str) -> Path:
    """创建并返回单份学习素材的输出目录。"""
    output_dir = Path(config["output_dir"]).expanduser()
    destination = output_dir / f"{date_str}-{sanitize_filename(title)}"
    destination.mkdir(parents=True, exist_ok=True)
    return destination


class _ChineseArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(
            2,
            f"{self.prog}: 参数错误：{message}。"
            "请检查命令参数后重试。\n",
        )


def _date_string(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            "日期必须是有效的 YYYY-MM-DD"
        ) from None
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError(
            "日期必须是有效的 YYYY-MM-DD"
        )
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = _ChineseArgumentParser(
        description="读取树树工具箱配置并准备素材目录"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser(
        "prepare", help="创建并输出本次素材目录与配置"
    )
    prepare.add_argument("--title", required=True, help="素材标题")
    prepare.add_argument(
        "--date",
        type=_date_string,
        default=date.today().isoformat(),
        help="目录日期（YYYY-MM-DD）",
    )
    return parser


def _prepare_payload(title: str, date_str: str) -> dict:
    if not sanitize_filename(title):
        raise ValueError("标题去除非法字符后不能为空")

    # load_config 在使用默认值时会给人类提示；prepare 的 stdout
    # 必须只包含 JSON，所以把该提示转发到 stderr。
    with redirect_stdout(sys.stderr):
        config = load_config()

    destination = item_dir(config, title, date_str)
    output_dir = Path(config["output_dir"]).expanduser()
    return {
        "item_dir": str(destination),
        "output_dir": str(output_dir),
        "native_lang": config["native_lang"],
        "source_lang": config["source_lang"],
        "whisper_model": config["whisper_model"],
        "video_quality": config["video_quality"],
        "subtitle_layout": config["subtitle_layout"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = _prepare_payload(args.title, args.date)
    except SystemExit:
        raise
    except (KeyError, TypeError, ValueError, OSError, UnicodeError):
        print(
            "无法准备素材目录。"
            "请检查标题、config.json 的六项配置和输出目录权限。",
            file=sys.stderr,
        )
        return 1

    try:
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        print(
            "无法输出配置 JSON：配置值包含无法编码的字符。"
            "请修正 config.json 后重试。",
            file=sys.stderr,
        )
        return 1

    try:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            sys.stdout.write(encoded.decode("utf-8"))
            sys.stdout.flush()
        else:
            buffer.write(encoded)
            buffer.flush()
    except (OSError, UnicodeError):
        print(
            "无法输出配置 JSON：终端输出失败。"
            "请检查终端后重试。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
