# src/transcription/audio/downloader.py
import subprocess
import requests
import tempfile
from pathlib import Path


def download_and_trim(audio_url: str, output_dir: Path, filename: str, skip_seconds: int = 60) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename

    # 1) Download to temp file
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        with requests.get(audio_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            for chunk in r.iter_content(8192):
                if chunk:
                    tmp.write(chunk)

    # 2) Trim with ffmpeg
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(skip_seconds),
        "-i", str(tmp_path),
        "-c", "copy",
        str(output_path)
    ]
    subprocess.run(cmd, check=True)

    tmp_path.unlink(missing_ok=True)
    return output_path
