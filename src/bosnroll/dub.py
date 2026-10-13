"""İngilizce dublaj: İngilizce altyazı metnini Kokoro ile seslendirip Türkçe zamanlamaya oturtur.

Her sahne (beat), Türkçe seslendirmede başladığı anda başlar; böylece görüntülerle senkron kalır.
İngilizce metin sahneye sığmazsa konuşma biraz hızlandırılır.

Kullanım: python -m bosnroll.dub --id nobel-fizik-2026
Model dosyaları (kokoro-onnx): kokoro-v1.0.onnx ve voices-v1.0.bin, KOKORO_DIR klasöründe.
"""

import argparse
import os
import wave
from pathlib import Path

import numpy as np

from .config import ROOT
from .render import load_lines, split_subtitle

SR = 24000  # Kokoro çıkış örnekleme hızı
MAX_SPEED = 1.3


def read_srt_texts(path: Path) -> list[str]:
    blocks = path.read_text(encoding="utf-8").strip().split("\n\n")
    return [" ".join(b.split("\n")[2:]).strip() for b in blocks]


def build_dub(episode_dir: Path, voice: str, kokoro_dir: Path) -> Path:
    from kokoro_onnx import Kokoro

    lines = load_lines(episode_dir / "lines.json")
    subs = [s for line in lines for s in split_subtitle(line)]
    texts = read_srt_texts(episode_dir / "subtitles.en.srt")
    assert len(texts) == len(subs), "İngilizce altyazı satır sayısı Türkçe ile aynı olmalı"
    with wave.open(str(episode_dir / "voice.wav")) as w:
        total = w.getnframes() / w.getframerate()

    # Altyazı parçalarını sahnelere topla: (başlangıç, metin)
    beats: list[tuple[int, float, list[str]]] = []
    for s, t in zip(subs, texts):
        if beats and beats[-1][0] == s.beat:
            beats[-1][2].append(t)
        else:
            beats.append((s.beat, s.start, [t]))

    tts = Kokoro(str(kokoro_dir / "kokoro-v1.0.onnx"), str(kokoro_dir / "voices-v1.0.bin"))
    out = np.zeros(int((total + 1) * SR), dtype=np.float32)
    for i, (_, start, parts) in enumerate(beats):
        end = beats[i + 1][1] if i + 1 < len(beats) else total
        slot = end - start - 0.25
        text = " ".join(parts)
        audio, sr = tts.create(text, voice=voice, speed=1.0, lang="en-us")
        assert sr == SR
        if len(audio) / SR > slot:
            speed = min(MAX_SPEED, len(audio) / SR / slot * 1.02)
            audio, _ = tts.create(text, voice=voice, speed=speed, lang="en-us")
        a = int(start * SR)
        audio = audio[: len(out) - a]
        out[a : a + len(audio)] += audio
        print(f"  sahne {i + 1}/{len(beats)}  {len(audio) / SR:5.1f} sn / yer {slot:5.1f} sn", flush=True)

    out = out[: int(total * SR)]
    out = out / (np.max(np.abs(out)) or 1.0) * 0.89
    path = episode_dir / "voice.en.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((out * 32767).astype(np.int16).tobytes())
    return path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--id", required=True)
    p.add_argument("--voice", default="am_michael")
    a = p.parse_args()
    kokoro_dir = Path(os.environ.get("KOKORO_DIR", ROOT / "models" / "kokoro"))
    print("İngilizce ses:", build_dub(ROOT / "episodes" / a.id, a.voice, kokoro_dir))


if __name__ == "__main__":
    main()
