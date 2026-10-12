"""Sahneleri HTML şablonundan kare kare çizer ve FFmpeg ile videoya dönüştürür."""

import json
import os
import re
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

from .config import ASSETS, TEMPLATES
from .tts import Line

MONTHS = ["OCAK", "ŞUBAT", "MART", "NİSAN", "MAYIS", "HAZİRAN", "TEMMUZ", "AĞUSTOS", "EYLÜL", "EKİM", "KASIM", "ARALIK"]
MAX_SUB_CHARS = 110


def date_label(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{d} {MONTHS[m - 1]} {y}"


def split_subtitle(line: Line) -> list[Line]:
    """Uzun cümleleri ekrana sığacak parçalara böler; süreyi karakter sayısına göre paylaştırır."""
    if len(line.text) <= MAX_SUB_CHARS:
        return [line]
    words = line.text.split()
    parts: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > MAX_SUB_CHARS * 0.75 and re.search(r"[,;:]$", cur):
            parts.append(cur)
            cur = w
        elif cur and len(cur) + 1 + len(w) > MAX_SUB_CHARS:
            parts.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    parts.append(cur)
    total = sum(len(p) for p in parts)
    out, t = [], line.start
    for p in parts:
        dur = (line.end - line.start) * len(p) / total
        out.append(Line(line.beat, p, t, t + dur))
        t += dur
    return out


def render_frames(
    episode: dict, lines: list[Line], audio_dur: float, frames_dir: Path, thumb_path: Path, scale: int = 1
) -> list[tuple[Path, float]]:
    """Her altyazı parçası için bir kare çizer. (kare, süre) listesi döner."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    ep = {**episode, "date_label": date_label(episode["date"])}
    subs = [s for line in lines for s in split_subtitle(line)]

    # Kareler arası boşluk kalmasın: her kare bir sonrakinin başlangıcına kadar sürer.
    timeline: list[tuple[dict, str, float, float]] = []
    for i, s in enumerate(subs):
        start = 0.0 if i == 0 else s.start
        end = subs[i + 1].start if i + 1 < len(subs) else audio_dur
        timeline.append((episode["beats"][s.beat], s.text, start, end))

    out: list[tuple[Path, float]] = []
    with sync_playwright() as p:
        # CHROMIUM_PATH: Playwright'ın kendi indirdiği yerine sistemdeki Chromium'u kullanmak için
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=scale)
        page.goto((TEMPLATES / "scene.html").as_uri())
        page.evaluate("document.fonts.ready")
        for i, (beat, text, start, end) in enumerate(timeline):
            page.evaluate("f => render(f)", {"episode": ep, "beat": beat, "subtitle": text})
            path = frames_dir / f"{i:04d}.png"
            page.screenshot(path=str(path))
            out.append((path, end - start))

        browser.close()

    with sync_playwright() as p:
        # YouTube kapak görseli 1280x720 ve en fazla 2 MB olmalı
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.goto((TEMPLATES / "scene.html").as_uri())
        page.evaluate("document.fonts.ready")
        page.evaluate("ep => renderThumb(ep)", ep)
        page.screenshot(path=str(thumb_path), type="jpeg", quality=92)
        browser.close()
    return out


def assemble(frames: list[tuple[Path, float]], audio: Path, out_mp4: Path, cfg: dict) -> None:
    vcfg = cfg["video"]
    concat = out_mp4.parent / "frames.txt"
    lines = []
    for path, dur in frames:
        lines += [f"file '{path.resolve()}'", f"duration {dur:.3f}"]
    lines.append(f"file '{frames[-1][0].resolve()}'")  # concat demuxer son süreyi uygulasın diye
    concat.write_text("\n".join(lines) + "\n")
    total = sum(d for _, d in frames)

    # Alt kenarda videonun ilerleyişini gösteren neon çizgi
    progress = f"drawbox=x=0:y=ih-6:w='iw*t/{total:.3f}':h=6:color=0xFF6A1A@0.95:t=fill"
    vf = f"fps={vcfg['fps']},format=yuv420p,{progress}"

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-i", str(audio)]
    music = ASSETS / "music.mp3"
    if music.exists():
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        af = f"[2:a]volume={vcfg['music_volume']}[m];[1:a][m]amix=inputs=2:duration=first:normalize=0[a]"
        cmd += ["-filter_complex", f"[0:v]{vf}[v];{af}", "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-vf", vf, "-map", "0:v", "-map", "1:a"]
    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", "-shortest", str(out_mp4),
    ]
    subprocess.run(cmd, check=True)


def write_srt(lines: list[Line], path: Path) -> None:
    def ts(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    subs = [s for line in lines for s in split_subtitle(line)]
    path.write_text(
        "\n".join(f"{i + 1}\n{ts(s.start)} --> {ts(s.end)}\n{s.text}\n" for i, s in enumerate(subs)), encoding="utf-8"
    )


def save_lines(lines: list[Line], path: Path) -> None:
    path.write_text(json.dumps([l.__dict__ for l in lines], ensure_ascii=False, indent=1), encoding="utf-8")


def load_lines(path: Path) -> list[Line]:
    return [Line(**d) for d in json.loads(path.read_text(encoding="utf-8"))]
