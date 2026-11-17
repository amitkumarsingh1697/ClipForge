"""One-time YouTube authorisation (loopback flow).

On a headless server:  ssh -L 8765:localhost:8765 you@server
then run this script there and open the printed URL in your laptop's browser.
In Google Cloud Console create an OAuth client of type *Desktop app* and set the
consent screen to *In production* so the refresh token doesn't expire after 7 days.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.utils.auth_helpers import run_youtube_loopback_flow  # noqa: E402

secrets = get_settings().youtube_client_secrets
if not secrets or not Path(secrets).exists():
    sys.exit("Set YOUTUBE_CLIENT_SECRETS to the downloaded client_secret.json path")
print("Saved", run_youtube_loopback_flow(Path(secrets)))
