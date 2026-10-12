"""EMA Lightning ile cümle cümle Türkçe seslendirme.

Her cümle ayrı üretildiği için altyazı zamanlamaları kesin olarak bilinir; ayrıca hizalama modeline gerek kalmaz.
"""

import re
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 48000

# Nokta/ünlem/soru işaretinden sonra boşluk gelen yerlerden böl ("229.999" bölünmez).
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


@dataclass
class Line:
    beat: int
    text: str  # altyazıda görünecek metin
    start: float
    end: float


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]


def apply_lexicon(text: str, lexicon: dict[str, str]) -> str:
    for word in sorted(lexicon, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(word)}(?=\W|$)", lexicon[word], text)
    return text


def synthesize(episode: dict, cfg: dict, out_wav: Path) -> list[Line]:
    from ema_lightning import EMA

    tts_cfg = cfg["tts"]
    lexicon = cfg.get("lexicon", {})
    sentences: list[tuple[int, str]] = [
        (i, s) for i, beat in enumerate(episode["beats"]) for s in split_sentences(beat["say"])
    ]
    tts = EMA()
    speeches = tts.say(
        [apply_lexicon(s, lexicon) for _, s in sentences], speed=tts_cfg["speed"], seed=tts_cfg["seed"]
    )

    pause = np.zeros(int(tts_cfg["sentence_pause"] * SAMPLE_RATE), dtype=np.float32)
    beat_pause = np.zeros(int(tts_cfg["beat_pause"] * SAMPLE_RATE), dtype=np.float32)
    chunks: list[np.ndarray] = [np.zeros(int(0.4 * SAMPLE_RATE), dtype=np.float32)]
    lines: list[Line] = []
    t = 0.4
    for n, ((beat, text), speech) in enumerate(zip(sentences, speeches)):
        assert speech.sample_rate == SAMPLE_RATE
        audio = speech.audio.astype(np.float32)
        lines.append(Line(beat, text, t, t + len(audio) / SAMPLE_RATE))
        last_of_beat = n + 1 == len(sentences) or sentences[n + 1][0] != beat
        gap = beat_pause if last_of_beat else pause
        chunks += [audio, gap]
        t += (len(audio) + len(gap)) / SAMPLE_RATE

    full = np.concatenate(chunks)
    peak = float(np.max(np.abs(full))) or 1.0
    pcm = (full / peak * 0.89 * 32767).astype(np.int16)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return lines
