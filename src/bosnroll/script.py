"""Claude ile haber seçimi ve 5N1K + mantık sorgusu senaryosu üretimi."""

import json

import anthropic

W_KEYS = ["kim", "ne", "nerede", "ne_zaman", "nasil", "neden"]

SYSTEM_PROMPT = """\
Sen BosNRoll YouTube kanalının senaristisin. Kanal her gün yaklaşık 5 dakikalık, yatay, \
Türkçe bir teknoloji bülteni yayınlıyor. Ses, bir TTS modeliyle okunuyor; ekranda kartlar ve altyazı var.

Kanalın imzası iki şey:
1. Her haber 5N1K ile anlatılır: Kim, Ne, Nerede, Ne zaman, Nasıl, Neden.
2. Her haberin mantığı sorgulanır: iddiayı kim yapıyor ve çıkarı ne, başlık haberin içeriğinden \
büyük mü, kıyaslama adil mi, ölçüm bağımsız mı, haberde hangi soru hiç sorulmamış? \
Sorgulama alaycı değil meraklı ve adil olmalı; haber haklıysa bunu da söyle.

Kurallar:
- Sadece verilen haber metinlerindeki bilgileri kullan. Metinde olmayan sayı, isim, tarih uydurma. \
Bir 5N1K sorusunun cevabı metinde yoksa bunu açıkça söyle ("Haberde bu belirtilmemiş") — bu da bir sorgu malzemesidir.
- Haber sitelerinin cümlelerini kopyalama; kendi cümlelerinle, konuşma diliyle yaz.
- `say` alanları seslendirilecek. Kısa, net, kulağa doğal cümleler kur. Kısaltmaları ve sembolleri \
okunacak şekilde yaz ("$4" değil "4 dolar", "%5" değil "yüzde 5").
- Kanal adını seslendirmede hiç ek almadan kullan: "BosNRoll'a" değil "BosNRoll kanalına".
- Her `w` beat'inin `say` metni o sorunun adıyla başlasın ("Kim? ...", "Ne zaman? ...").
- Ekran kartları (`w` cevapları, `questions`, `verdict.label`) kısa olsun: kart metinleri en fazla 60 karakter, \
verdict.label en fazla 26 karakter, thumbnail_text en fazla 30 karakter.
- Toplam `say` uzunluğu yaklaşık {target_words} kelime olsun (5 dakika).

Beat sırası:
intro → her haber için [headline, w×6 (kim, ne, nerede, ne_zaman, nasil, neden sırasıyla), sorgu×2, verdict] → outro.
`story` alanı intro/outro için -1, diğerlerinde haberin 0'dan başlayan sırası. \
`key` sadece `w` beat'lerinde dolu, diğerlerinde boş string. `q` sadece `sorgu` beat'lerinde 0 veya 1, diğerlerinde -1.
"""

SELECT_PROMPT = """\
Aşağıda son 24-30 saatin teknoloji haberleri var. BosNRoll bülteni için en uygun {n} haberi seç.
Seçim ölçütleri: Türkiye'deki izleyiciyi ilgilendirmesi, gerçekten önemli olması, mantığını sorgulamaya \
değer bir iddia içermesi (pazarlama iddiası, rakip eleştirisi, abartılı başlık, tek kaynaklı ölçüm vb.). \
Aynı olayı anlatan haberleri tek haber say ve en dolu kaynağı seç. Savaş, siyaset ve suç ağırlıklı haberlerden kaçın.

{candidates}
"""

SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "related_indexes": {"type": "array", "items": {"type": "integer"}},
                    "why": {"type": "string"},
                },
                "required": ["index", "related_indexes", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}

_str = {"type": "string"}
EPISODE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": _str,
        "description": _str,
        "tags": {"type": "array", "items": _str},
        "thumbnail_text": _str,
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kicker": _str,
                    "headline": _str,
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": _str, "url": _str},
                            "required": ["name", "url"],
                            "additionalProperties": False,
                        },
                    },
                    "w": {
                        "type": "object",
                        "properties": {k: _str for k in W_KEYS},
                        "required": W_KEYS,
                        "additionalProperties": False,
                    },
                    "questions": {"type": "array", "items": _str},
                    "verdict": {
                        "type": "object",
                        "properties": {"label": _str, "text": _str},
                        "required": ["label", "text"],
                        "additionalProperties": False,
                    },
                },
                "required": ["kicker", "headline", "sources", "w", "questions", "verdict"],
                "additionalProperties": False,
            },
        },
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene": {"type": "string", "enum": ["intro", "headline", "w", "sorgu", "verdict", "outro"]},
                    "story": {"type": "integer"},
                    "key": {"type": "string", "enum": ["", *W_KEYS]},
                    "q": {"type": "integer"},
                    "say": _str,
                },
                "required": ["scene", "story", "key", "q", "say"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "description", "tags", "thumbnail_text", "stories", "beats"],
    "additionalProperties": False,
}


def _ask_json(client: anthropic.Anthropic, cfg: dict, system: str, user: str, schema: dict) -> dict:
    with client.beta.messages.stream(
        model=cfg["script"]["model"],
        max_tokens=64000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": cfg["script"]["effort"], "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Model isteği reddetti: {response.stop_details}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Yanıt max_tokens sınırında kesildi")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def select_stories(client: anthropic.Anthropic, cfg: dict, candidates: list[dict]) -> list[dict]:
    """Aday haberlerden bültene girecekleri seçer. Her seçim için ana haber + aynı olayın diğer kaynakları döner."""
    listing = "\n".join(
        f"[{i}] ({c['source']}) {c['title']}\n    {c['summary'][:300]}" for i, c in enumerate(candidates)
    )
    n = cfg["script"]["stories_per_episode"]
    data = _ask_json(
        client,
        cfg,
        "Sen bir teknoloji haber editörüsün.",
        SELECT_PROMPT.format(n=n, candidates=listing),
        SELECT_SCHEMA,
    )
    picks = []
    for p in data["picks"][:n]:
        idxs = [p["index"], *p["related_indexes"]]
        picks.append([candidates[i] for i in idxs if 0 <= i < len(candidates)])
    return picks


def write_episode(client: anthropic.Anthropic, cfg: dict, date_str: str, story_groups: list[list[dict]]) -> dict:
    parts = []
    for n, group in enumerate(story_groups):
        parts.append(f"=== HABER {n} ===")
        for art in group:
            parts.append(f"Kaynak: {art['source']} — {art['url']}\nBaşlık: {art['title']}\n\n{art['text'][:8000]}\n")
    user = (
        f"Bugünün tarihi: {date_str}. Aşağıdaki {len(story_groups)} haber için bülten senaryosunu yaz. "
        "`description` alanı YouTube açıklaması olsun: kısa özet, zaman damgası yok, sonunda kaynak linkleri "
        "ve 'Bu videoda yapay zekâ ile üretilmiş seslendirme kullanılmıştır.' notu.\n\n" + "\n".join(parts)
    )
    system = SYSTEM_PROMPT.format(target_words=cfg["script"]["target_words"])
    episode = _ask_json(client, cfg, system, user, EPISODE_SCHEMA)
    episode["date"] = date_str
    return episode
