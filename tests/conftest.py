import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CELERY_EAGER", "1")
    monkeypatch.setenv("API_TOKEN", "test-token")
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_CHAT_ID", "INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    from app.config import get_settings
    import app.utils.job_store as js

    get_settings.cache_clear()
    js._store = None
    import sys
    sys.modules.pop("app.celery_worker", None)  # re-read eager flag
    yield
    get_settings.cache_clear()


def make_video(path: Path, duration=90, w=1280, h=720, burst=(50, 60), box_x="800+150*sin(2*t)"):
    """Black gameplay stand-in: a white box moving on the RIGHT side; quiet hum with a loud burst."""
    b0, b1 = burst
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=30:d={duration}",
           "-f", "lavfi", "-i", f"anoisesrc=d={duration}:c=pink:a=0.5",
           "-f", "lavfi", "-i", f"sine=f=220:d={duration}:sample_rate=44100",
           "-f", "lavfi", "-i", f"color=c=white:s=120x120:r=30:d={duration}",
           "-filter_complex", f"[0:v][3:v]overlay=x='{box_x}':y=300:eval=frame[v];[2:a]volume=0.03[q];[1:a]volume='if(between(t,{b0},{b1}),1.0,0)':eval=frame[l];[q][l]amix=inputs=2:normalize=0[a]",
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("media") / "gameplay.mp4")


@pytest.fixture(scope="session")
def short_portrait(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("media") / "portrait.mp4", duration=12, w=720, h=1280,
                      burst=(4, 6), box_x="300")
