import importlib
import json
import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
DOCTOR_SCRIPT = SCRIPTS_DIR / "doctor.py"


def test_doctor_json_shape(tmp_path):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)

    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(DOCTOR_SCRIPT), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )

    payload = json.loads(result.stdout)
    assert set(payload) == {
        "python",
        "yt_dlp",
        "ffmpeg",
        "faster_whisper",
        "output_dir_writable",
    }


def test_find_ffmpeg_returns_tuple():
    sys.path.insert(0, str(SCRIPTS_DIR))
    doctor = importlib.import_module("doctor")

    ffmpeg_path, source = doctor.find_ffmpeg()

    assert Path(ffmpeg_path).is_file()
    assert source == "system"
