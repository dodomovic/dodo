"""YouTube Data API v3 ile video, kapak görseli ve altyazı yükleme.

Gerekli ortam değişkenleri (GitHub Secrets):
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
Refresh token'ı bir kez almak için: python -m bosnroll auth
"""

import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _youtube():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload_episode(episode: dict, video: Path, thumb: Path, srt: Path, cfg: dict) -> str:
    yt_cfg = cfg["youtube"]
    yt = _youtube()
    body = {
        "snippet": {
            "title": episode["title"][:100],
            "description": episode["description"][:5000],
            "tags": episode["tags"][:30],
            "categoryId": yt_cfg["category_id"],
            "defaultLanguage": "tr",
            "defaultAudioLanguage": "tr",
        },
        "status": {
            "privacyStatus": yt_cfg["privacy"],
            "selfDeclaredMadeForKids": yt_cfg["made_for_kids"],
            "containsSyntheticMedia": yt_cfg["contains_synthetic_media"],
        },
    }
    req = yt.videos().insert(
        part="snippet,status", body=body, media_body=MediaFileUpload(str(video), chunksize=8 << 20, resumable=True)
    )
    response = None
    while response is None:
        _, response = req.next_chunk()
    video_id = response["id"]

    # Özel kapak görseli için kanalın telefonla doğrulanmış olması gerekir.
    yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumb))).execute()
    yt.captions().insert(
        part="snippet",
        body={"snippet": {"videoId": video_id, "language": "tr", "name": "Türkçe", "isDraft": False}},
        media_body=MediaFileUpload(str(srt), mimetype="application/octet-stream"),
    ).execute()
    return video_id


def authorize(client_secrets_file: str) -> None:
    """Tarayıcıda bir kez giriş yapıp refresh token alır (yerel bilgisayarda çalıştırılır)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_file, SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    print("Bu değeri GitHub'da YT_REFRESH_TOKEN secret'ı olarak kaydedin:")
    print(creds.refresh_token)
