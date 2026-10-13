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


def clip_probe(path: Path) -> tuple[int, int, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
         "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    st = d["streams"][0]
    return st["width"], st["height"], float(d["format"]["duration"])


def clip_aspect(path: Path) -> float:
    w, h, _ = clip_probe(path)
    return w / h


def prepare_episode(episode: dict) -> dict:
    """Şablon için ek alanlar: tarih etiketi; verisi olmayan zaman çizelgesi sahneleri bir öncekinin maddelerini kullanır."""
    beats, items = [], None
    for b in episode["beats"]:
        b = {**b, "chapter": b.get("chapter", -1)}
        if b["scene"] == "timeline":
            data = dict(b.get("data", {}))
            items = data.setdefault("items", items)
            b["data"] = data
        beats.append(b)
    clips = episode.get("clips", {})
    for b in beats:
        if b["scene"] == "clip":
            b["data"] = {**b["data"], "aspect": clip_aspect(ASSETS / "clips" / clips[b["data"]["clip"]]["file"])}
    credits_file = ASSETS / "photos" / "credits.json"
    credits = json.loads(credits_file.read_text(encoding="utf-8")) if credits_file.exists() else {}
    return {
        **episode,
        "beats": beats,
        "format": episode.get("format", "news"),
        "date_label": date_label(episode["date"]),
        "photo_credits": {**credits, **episode.get("photo_credits", {})},
    }


# Arka plan görselleri yüklenmeden kare çekilmesin
_WAIT_IMAGES = """() => Promise.all([...document.querySelectorAll('.bgphoto, .medal.ph')].map(el => new Promise(done => {
  const m = getComputedStyle(el).backgroundImage.match(/url\\("?(.*?)"?\\)/);
  if (!m) return done();
  const img = new Image(); img.onload = img.onerror = () => img.decode().then(done, done); img.src = m[1];
})))"""


_CLIP_BOX = """() => { const e = document.querySelector('.clipbox'); if (!e) return null;
  const r = e.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; }"""


def _draw(page, frame: dict) -> None:
    page.evaluate("f => render(f)", frame)
    page.evaluate(_WAIT_IMAGES)


def render_preview(episode: dict, out_dir: Path) -> list[Path]:
    """Seslendirme olmadan her sahnenin bir karesini çizer (tasarım kontrolü için)."""
    from .tts import split_sentences

    out_dir.mkdir(parents=True, exist_ok=True)
    ep = prepare_episode(episode)
    paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto((TEMPLATES / "scene.html").as_uri())
        page.evaluate("document.fonts.ready")
        for i, beat in enumerate(ep["beats"]):
            sub = split_subtitle(Line(i, split_sentences(beat["say"])[0], 0, 1))[0].text
            _draw(page, {"episode": ep, "beat": beat, "subtitle": sub})
            path = out_dir / f"{i:03d}-{beat['scene']}.png"
            page.screenshot(path=str(path))
            paths.append(path)
        browser.close()
    return paths


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
) -> list[tuple[Path, float, dict | None]]:
    """Her altyazı parçası için bir kare çizer. (kare, süre, kesit) listesi döner.

    Kesitli sahnelerde kesit: {"file", "box": [x, y, w, h] | None (tam ekran), "offset": sahnedeki saniye}.
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    ep = prepare_episode(episode)
    subs = [s for line in lines for s in split_subtitle(line)]

    # Kareler arası boşluk kalmasın: her kare bir sonrakinin başlangıcına kadar sürer.
    timeline: list[tuple[dict, str, float, float]] = []
    for i, s in enumerate(subs):
        start = 0.0 if i == 0 else s.start
        end = subs[i + 1].start if i + 1 < len(subs) else audio_dur
        timeline.append((ep["beats"][s.beat], s.text, start, end))

    out: list[tuple[Path, float, dict | None]] = []
    beat_start: dict[int, float] = {}
    with sync_playwright() as p:
        # CHROMIUM_PATH: Playwright'ın kendi indirdiği yerine sistemdeki Chromium'u kullanmak için
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=scale)
        page.goto((TEMPLATES / "scene.html").as_uri())
        page.evaluate("document.fonts.ready")
        for i, (beat, text, start, end) in enumerate(timeline):
            _draw(page, {"episode": ep, "beat": beat, "subtitle": text})
            path = frames_dir / f"{i:04d}.png"
            clip = None
            if beat["scene"] == "clip":
                key = id(beat)
                beat_start.setdefault(key, start)
                data = beat["data"]
                box = page.evaluate(_CLIP_BOX)
                clip = {
                    "file": str(ASSETS / "clips" / ep["clips"][data["clip"]]["file"]),
                    "box": [int(round(v * scale / 2)) * 2 for v in box] if box else None,
                    "offset": start - beat_start[key],
                }
            # Tam ekran kesitte kare saydam çekilir; kesit arkadan görünür
            page.screenshot(path=str(path), omit_background=bool(clip and clip["box"] is None))
            out.append((path, end - start, clip))

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


def _chunks(frames: list[tuple[Path, float, dict | None]], work: Path, fps: int) -> Path:
    """Kesitli videolar için: her kareyi (ve varsa kesiti) ayrı bir parça olarak kodlar, sonra uç uca ekler."""
    work.mkdir(parents=True, exist_ok=True)
    w, h = (int(v) for v in subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(frames[0][0])],
        capture_output=True, text=True, check=True).stdout.strip().split(","))
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", "-r", str(fps), "-an"]
    parts, t, done = [], 0.0, 0
    for i, (png, dur, clip) in enumerate(frames):
        t += dur
        n = round(t * fps) - done  # kare sayısıyla say: parçalar arası kayma birikmesin
        done += n
        if n <= 0:
            continue
        out = work / f"{i:04d}.mp4"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(fps), "-i", str(png)]
        if clip:
            _, _, cdur = clip_probe(Path(clip["file"]))
            ss = min(clip["offset"], max(cdur - 0.1, 0))
            cmd += ["-ss", f"{ss:.3f}", "-i", clip["file"]]
            hold = f"tpad=stop_mode=clone:stop_duration={dur + 1:.2f}"
            if clip["box"]:
                x, y, bw, bh = clip["box"]
                fc = (f"[1:v]fps={fps},scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
                      f"setpts=PTS-STARTPTS,{hold}[c];[0:v][c]overlay={x}:{y}:shortest=0[v]")
            else:
                fc = (f"[1:v]fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                      f"setpts=PTS-STARTPTS,{hold}[c];[c][0:v]overlay=0:0:shortest=0[v]")
            cmd += ["-filter_complex", fc, "-map", "[v]"]
        cmd += ["-frames:v", str(n), *enc, str(out)]
        subprocess.run(cmd, check=True)
        parts.append(out)
    lst = work / "parts.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    timeline = work / "timeline.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c", "copy", str(timeline)], check=True)
    return timeline


def assemble(frames: list[tuple[Path, float, dict | None]], audio: Path, out_mp4: Path, cfg: dict) -> None:
    vcfg = cfg["video"]
    total = sum(f[1] for f in frames)
    if any(f[2] for f in frames):
        video_in = ["-i", str(_chunks(frames, out_mp4.parent / "chunks", vcfg["fps"]))]
    else:
        concat = out_mp4.parent / "frames.txt"
        lines = []
        for path, dur, _ in frames:
            lines += [f"file '{path.resolve()}'", f"duration {dur:.3f}"]
        lines.append(f"file '{frames[-1][0].resolve()}'")  # concat demuxer son süreyi uygulasın diye
        concat.write_text("\n".join(lines) + "\n")
        video_in = ["-f", "concat", "-safe", "0", "-i", str(concat)]

    # Alt kenarda videonun ilerleyişini gösteren neon çizgi
    progress = f"drawbox=x=0:y=ih-6:w='iw*t/{total:.3f}':h=6:color=0xFF6A1A@0.95:t=fill"
    vf = f"fps={vcfg['fps']},format=yuv420p,{progress}"

    cmd = ["ffmpeg", "-y", "-loglevel", "error", *video_in, "-i", str(audio)]
    music = ASSETS / "music.mp3"
    if music.exists():
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        af = f"[2:a]volume={vcfg['music_volume']}[m];[1:a][m]amix=inputs=2:duration=first:normalize=0[a]"
        cmd += ["-filter_complex", f"[0:v]{vf}[v];{af}", "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-vf", vf, "-map", "0:v", "-map", "1:a"]
    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        *([] if any(f[2] for f in frames) else ["-tune", "stillimage"]),
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
