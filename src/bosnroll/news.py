"""RSS kaynaklarından günün teknoloji haberlerini toplar."""

import calendar
import html
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import feedparser
import trafilatura


@dataclass
class Article:
    source: str
    title: str
    url: str
    published: float
    summary: str
    text: str = ""


def fetch_candidates(cfg: dict, seen_urls: set[str]) -> list[Article]:
    """Tüm kaynaklardan son `max_age_hours` içindeki, daha önce kullanılmamış haberleri döner."""
    news_cfg = cfg["news"]
    cutoff = time.time() - news_cfg["max_age_hours"] * 3600
    articles: list[Article] = []
    for source, url in news_cfg["feeds"].items():
        feed = feedparser.parse(url)
        for e in feed.entries:
            parsed = e.get("published_parsed") or e.get("updated_parsed")
            ts = calendar.timegm(parsed) if parsed else time.time()
            if ts < cutoff or e.link in seen_urls:
                continue
            summary = html.unescape(re.sub(r"<[^>]+>", " ", e.get("summary", ""))).strip()
            articles.append(Article(source, e.title, e.link, ts, summary[:500]))
    articles.sort(key=lambda a: a.published, reverse=True)
    return articles[: news_cfg["max_candidates"]]


def fetch_full_text(article: Article) -> Article:
    downloaded = trafilatura.fetch_url(article.url)
    article.text = (trafilatura.extract(downloaded) or "") if downloaded else ""
    return article


class SeenStore:
    """Daha önce videoya giren haberlerin URL'lerini tutar (tekrar kullanılmasın diye)."""

    def __init__(self, path: Path):
        self.path = path
        self.urls: set[str] = set(json.loads(path.read_text())) if path.exists() else set()

    def add(self, urls: list[str]) -> None:
        self.urls.update(urls)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(self.urls), ensure_ascii=False, indent=1))


def to_dicts(articles: list[Article]) -> list[dict]:
    return [asdict(a) for a in articles]
