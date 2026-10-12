"""Cümle cümle Türkçe seslendirme.

İki motor desteklenir (config.yaml → tts.engine):
- voxcpm: VoxCPM2 ile kanal sahibinin sesinin klonu (Apache 2.0, doğal ama yavaş: CPU'da ~7x gerçek zaman)
- ema:    EMA Lightning (Apache 2.0, tek hazır ses, çok hızlı)

Her cümle ayrı üretildiği için altyazı zamanlamaları kesin olarak bilinir; ayrıca hizalama modeline gerek kalmaz.
"""

import hashlib
import json
import re
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import ROOT

SAMPLE_RATE = 48000

# Nokta/ünlem/soru işaretinden sonra boşluk gelen yerlerden böl ("229.999" bölünmez).
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


@dataclass
class Line:
    beat: int
    text: str  # altyazıda görünecek metin
    start: float
    end: float


def split_sentences(text: str, min_chars: int = 25) -> list[str]:
    """Cümlelere böler; "Kim?" gibi çok kısa parçaları sonraki cümleyle birleştirir (tek başına doğal okunmuyorlar)."""
    out: list[str] = []
    carry = ""
    for s in (s.strip() for s in _SENTENCE_END.split(text.strip())):
        if not s:
            continue
        s = f"{carry} {s}".strip()
        if len(s) < min_chars:
            carry = s
            continue
        out.append(s)
        carry = ""
    if carry:
        out.append(carry)
    return out


_ONES = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
_SCALES = ["", "bin", "milyon", "milyar", "trilyon"]
# 5.160 · 1988 · 5,7 · 2011'de  (binlik ayırıcı nokta, ondalık virgül)
_NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(?:'(?=\w))?")


def _three(n: int) -> list[str]:
    h, t, o = n // 100, n // 10 % 10, n % 10
    words = ([] if h == 0 else ["yüz"] if h == 1 else [_ONES[h], "yüz"]) + [_TENS[t], _ONES[o]]
    return [w for w in words if w]


def number_to_words(n: int) -> str:
    if n == 0:
        return "sıfır"
    groups, words = [], []
    while n:
        groups.append(n % 1000)
        n //= 1000
    for i in reversed(range(len(groups))):
        g = groups[i]
        if not g:
            continue
        if i == 1 and g == 1:
            words.append("bin")  # "bir bin" değil
        else:
            words += _three(g) + ([_SCALES[i]] if i else [])
    return " ".join(words)


def spell_numbers(text: str) -> str:
    """Rakamları Türkçe okunuşa çevirir: "5,7" → "beş virgül yedi", "2011'de" → "iki bin on birde"."""

    def repl(m: re.Match) -> str:
        words = number_to_words(int(m.group(1).replace(".", "")))
        if m.group(2):
            frac = m.group(2)
            zeros = len(frac) - len(frac.lstrip("0"))
            words += " virgül " + " ".join(["sıfır"] * zeros + ([number_to_words(int(frac))] if int(frac) else []))
        return words

    return _NUMBER.sub(repl, text)


def apply_lexicon(text: str, lexicon: dict[str, str]) -> str:
    for word in sorted(lexicon, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(word)}(?=\W|$)", lexicon[word], text)
    return text


def _ema(texts: list[str], tts_cfg: dict) -> list[np.ndarray]:
    from ema_lightning import EMA

    speeches = EMA().say(texts, speed=tts_cfg["speed"], seed=tts_cfg["seed"])
    assert all(sp.sample_rate == SAMPLE_RATE for sp in speeches)
    return [sp.audio.astype(np.float32) for sp in speeches]


def _voxcpm(texts: list[str], tts_cfg: dict) -> list[np.ndarray]:
    from voxcpm import VoxCPM

    vcfg = tts_cfg["voxcpm"]
    ref_wav = str(ROOT / vcfg["reference_wav"])
    ref_text = (ROOT / vcfg["reference_text"]).read_text(encoding="utf-8").strip()
    model = VoxCPM.from_pretrained(vcfg["model"], load_denoiser=False)
    sr = model.tts_model.sample_rate
    assert sr == SAMPLE_RATE, f"Beklenmeyen örnekleme hızı: {sr}"
    out = []
    for n, text in enumerate(texts, 1):
        wav = model.generate(
            text=text,
            prompt_wav_path=ref_wav,
            prompt_text=ref_text,
            reference_wav_path=ref_wav,
            cfg_value=vcfg["cfg_value"],
            inference_timesteps=vcfg["inference_timesteps"],
        )
        out.append(_trim_silence(np.asarray(wav, dtype=np.float32)))
        print(f"  ses {n}/{len(texts)}", flush=True)
    return out


def _trim_silence(audio: np.ndarray, threshold: float = 0.01, pad: float = 0.05) -> np.ndarray:
    """Baştaki ve sondaki sessizliği kırpar; aralardaki duraksamaları biz ekliyoruz."""
    loud = np.flatnonzero(np.abs(audio) > threshold * (np.max(np.abs(audio)) or 1.0))
    if not len(loud):
        return audio
    p = int(pad * SAMPLE_RATE)
    return audio[max(loud[0] - p, 0) : loud[-1] + p]


def voice_key(episode: dict, cfg: dict) -> str:
    """Seslendirmeyi etkileyen her şeyin özeti; değişmediyse ses yeniden üretilmez."""
    tts_cfg = cfg["tts"]
    h = hashlib.sha256()
    h.update(json.dumps([b["say"] for b in episode["beats"]], ensure_ascii=False).encode())
    h.update(json.dumps({"tts": tts_cfg, "lexicon": cfg.get("lexicon", {})}, sort_keys=True).encode())
    if tts_cfg["engine"] == "voxcpm":
        for k in ("reference_wav", "reference_text"):
            h.update((ROOT / tts_cfg["voxcpm"][k]).read_bytes())
    h.update(Path(__file__).read_bytes())  # cümle bölme / sayı okuma kuralları
    return h.hexdigest()


def synthesize(episode: dict, cfg: dict, out_wav: Path) -> list[Line]:
    tts_cfg = cfg["tts"]
    # VoxCPM2 çok dilli; İngilizce isimleri kendisi doğru okuyor, o yüzden kendi küçük sözlüğünü kullanır.
    lexicon = tts_cfg["voxcpm"].get("lexicon", {}) if tts_cfg["engine"] == "voxcpm" else cfg.get("lexicon", {})
    sentences: list[tuple[int, str]] = [
        (i, s) for i, beat in enumerate(episode["beats"]) for s in split_sentences(beat["say"])
    ]
    texts = [apply_lexicon(s, lexicon) for _, s in sentences]
    if tts_cfg["engine"] == "voxcpm":
        texts = [spell_numbers(t) for t in texts]
    if tts_cfg["engine"] == "voxcpm":
        audios = _voxcpm(texts, tts_cfg)
    else:
        audios = _ema(texts, tts_cfg)

    pause = np.zeros(int(tts_cfg["sentence_pause"] * SAMPLE_RATE), dtype=np.float32)
    beat_pause = np.zeros(int(tts_cfg["beat_pause"] * SAMPLE_RATE), dtype=np.float32)
    chunks: list[np.ndarray] = [np.zeros(int(0.4 * SAMPLE_RATE), dtype=np.float32)]
    lines: list[Line] = []
    t = 0.4
    for n, ((beat, text), audio) in enumerate(zip(sentences, audios)):
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
