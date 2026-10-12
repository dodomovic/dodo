"""BosNRoll günlük bülten hattı.

Kullanım:
  python -m bosnroll all              # haber topla → senaryo → ses → video → YouTube (private)
  python -m bosnroll script           # sadece haber seçimi + senaryo (episodes/<tarih>/episode.json)
  python -m bosnroll video            # mevcut episode.json'dan ses + video üret
  python -m bosnroll upload           # üretilmiş videoyu yükle
  python -m bosnroll preview          # seslendirmeden her sahnenin bir karesini çiz (tasarım kontrolü)
  python -m bosnroll auth secrets.json  # YouTube refresh token al (bir kez, yerelde)
Ortak seçenekler: --date YYYY-MM-DD (varsayılan: bugün, İstanbul saati)
                  --id KLASÖR (episodes/ altındaki klasör; varsayılan tarih. Ör. özel bölümler: --id nobel-fizik-2026)
"""

import argparse
import json
import wave
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ROOT, load_config


def episode_dir(date: str) -> Path:
    return ROOT / "episodes" / date


def cmd_script(cfg: dict, date: str) -> None:
    import anthropic

    from . import news, script

    seen = news.SeenStore(ROOT / "episodes" / "seen_urls.json")
    candidates = news.to_dicts(news.fetch_candidates(cfg, seen.urls))
    print(f"{len(candidates)} aday haber bulundu")
    client = anthropic.Anthropic()
    groups = script.select_stories(client, cfg, candidates)
    for g in groups:
        for art in g:
            art["text"] = news.fetch_full_text(news.Article(**art)).text or art["summary"]
        print("Seçildi:", g[0]["title"])
    episode = script.write_episode(client, cfg, date, groups)
    out = episode_dir(date)
    out.mkdir(parents=True, exist_ok=True)
    (out / "episode.json").write_text(json.dumps(episode, ensure_ascii=False, indent=2), encoding="utf-8")
    seen.add([a["url"] for g in groups for a in g])
    words = sum(len(b["say"].split()) for b in episode["beats"])
    print(f"Senaryo yazıldı: {out / 'episode.json'} ({words} kelime)")


def cmd_video(cfg: dict, date: str) -> None:
    from . import render, tts

    out = episode_dir(date)
    episode = json.loads((out / "episode.json").read_text(encoding="utf-8"))
    voice = out / "voice.wav"
    lines = tts.synthesize(episode, cfg, voice)
    render.save_lines(lines, out / "lines.json")
    with wave.open(str(voice)) as w:
        audio_dur = w.getnframes() / w.getframerate()
    print(f"Seslendirme: {audio_dur:.1f} sn")
    frames = render.render_frames(
        episode, lines, audio_dur, out / "frames", out / "thumbnail.jpg", scale=cfg["video"]["scale"]
    )
    render.write_srt(lines, out / "subtitles.srt")
    render.assemble(frames, voice, out / "video.mp4", cfg)
    print(f"Video hazır: {out / 'video.mp4'}")


def cmd_upload(cfg: dict, date: str) -> None:
    from . import upload

    out = episode_dir(date)
    episode = json.loads((out / "episode.json").read_text(encoding="utf-8"))
    vid = upload.upload_episode(episode, out / "video.mp4", out / "thumbnail.jpg", out / "subtitles.srt", cfg)
    print(f"Yüklendi ({cfg['youtube']['privacy']}): https://youtu.be/{vid}")


def main() -> None:
    p = argparse.ArgumentParser(prog="bosnroll")
    p.add_argument("command", choices=["all", "script", "video", "upload", "preview", "auth"])
    p.add_argument("arg", nargs="?")
    p.add_argument("--date", default=datetime.now(ZoneInfo("Europe/Istanbul")).date().isoformat())
    p.add_argument("--id", help="episodes/ altındaki klasör adı (varsayılan: --date)")
    a = p.parse_args()
    if a.id:
        a.date = a.id
    cfg = load_config()
    if a.command == "auth":
        from .upload import authorize

        authorize(a.arg or "client_secret.json")
        return
    if a.command == "preview":
        from .render import render_preview

        out = episode_dir(a.date)
        episode = json.loads((out / "episode.json").read_text(encoding="utf-8"))
        print(f"{len(render_preview(episode, out / 'preview'))} önizleme karesi: {out / 'preview'}")
        return
    if a.command in ("all", "script"):
        cmd_script(cfg, a.date)
    if a.command in ("all", "video"):
        cmd_video(cfg, a.date)
    if a.command in ("all", "upload"):
        cmd_upload(cfg, a.date)


if __name__ == "__main__":
    main()
