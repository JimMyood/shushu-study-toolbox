"""树树工具箱各脚本共用的配置与路径工具。"""

import json
import unicodedata
from pathlib import Path


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


def load_config() -> dict:
    """读取仓库配置，并以默认值补齐未配置的字段。"""
    config_path = REPO_ROOT / "config.json"
    if not config_path.exists():
        print("复制 config.example.json 为 config.json 可自定义")
        return DEFAULTS.copy()

    try:
        user_config = json.loads(config_path.read_text(encoding="utf-8"))
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
    return " ".join(cleaned.split())[:80]


def item_dir(config: dict, title: str, date_str: str) -> Path:
    """创建并返回单份学习素材的输出目录。"""
    output_dir = Path(config["output_dir"]).expanduser()
    destination = output_dir / f"{date_str}-{sanitize_filename(title)}"
    destination.mkdir(parents=True, exist_ok=True)
    return destination
