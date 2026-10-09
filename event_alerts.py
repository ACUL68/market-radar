"""Alert essenziali: soglie bond europei e notizie ANSA rilevanti.

Le notizie sono titoli RSS, non indicazioni operative. Nessuna chiamata AI aggiuntiva.
"""
from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from macro_learning import extract_move_bp, safe_float

ROME = ZoneInfo("Europe/Rome")
BOND_TIERS = ((25, "🔴 movimento forte"), (15, "🟠 anomalia"), (10, "🟡 preallarme"))
RSS_FEEDS = (
    ("ANSA Economia", "https://www.ansa.it/sito/notizie/economia/economia_rss.xml"),
    ("ANSA Mondo", "https://www.ansa.it/sito/notizie/mondo/mondo_rss.xml"),
)
NEWS_MAX_PER_SCAN = 1
NEWS_MAX_PER_DAY = 2
NEWS_MAX_AGE_HOURS = 14

# Richiediamo sempre una notizia d'impatto, non una semplice citazione del tema.
GEO_EVENT = re.compile(r"\b(?:attacc\w*|bombard\w*|missil\w*|invas\w*|escalation|ultimatum|embargo|sanzion\w*|bloc\w*|chiusur\w*|raid|guerra|conflitt\w*)\b", re.I)
GEO_CONTEXT = re.compile(r"\b(?:iran|israel\w*|russ\w*|ucrain\w*|taiwan|cina|nato|usa|stati uniti|medio oriente|hormuz|mar rosso|golfo persico|petrol\w*|gas|energia|export|rotte|nucleare|navigaz\w*)\b", re.I)
ECON_EVENT = re.compile(r"\b(?:crisi|croll\w*|impennat\w*|balz\w*|shock|default|falliment\w*|emergenz\w*|straordinar\w*|recession\w*|sospens\w*|tagli\w*|rialz\w*|dazi|sanzion\w*)\b", re.I)
ECON_CONTEXT = re.compile(r"\b(?:bors\w*|mercat\w*|obbligaz\w*|spread|rendiment\w*|petrol\w*|greggio|gas|energia|bce|fed|banc\w*|inflazion\w*|tass\w*|debit\w*|rating|pil|import|export)\b", re.I)


def _bond_symbol(name: str) -> str | None:
    lower = name.lower()
    if "bund" in lower:
        return "BUND"
    if "btp" in lower:
        return "BTP"
    if "oat" in lower or "francia" in lower:
        return "OAT"
    return None  # Treasury e Gilt restano nel macro-score esistente.


def pending_bond_alerts(state: dict[str, Any], bonds: list[dict[str, Any]], now: datetime | None = None) -> list[tuple[str, int, str]]:
    current = (now or datetime.now(ROME)).astimezone(ROME)
    date_key = current.date().isoformat()
    sent = state.get("bond_alert_levels", {})
    pending: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    for bond in bonds:
        symbol = _bond_symbol(str(bond.get("benchmark") or ""))
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        # I dati incerti o riferiti a un altro giorno non generano notifiche.
        if str(bond.get("observation_date") or "") != date_key:
            continue
        move = extract_move_bp(bond)
        if move is None or not math.isfinite(move):
            continue
        tier = next(((bp, name) for bp, name in BOND_TIERS if abs(move) >= bp), None)
        if not tier:
            continue
        threshold, label = tier
        direction = "UP" if move > 0 else "DOWN"
        key = f"{date_key}|{symbol}|{direction}"
        if int(sent.get(key, 0)) >= threshold:
            continue
        level = safe_float(bond.get("current_yield_pct"))
        line = f"{label} — {symbol} 10Y: rendimento {move:+.1f} pb rispetto alla chiusura precedente"
        if level is not None and math.isfinite(level):
            line += f" (ora {level:.3f}%)"
        sources = bond.get("sources") or []
        if isinstance(sources, list):
            link = next((url for url in sources if isinstance(url, str) and url.startswith("https://")), None)
            if link:
                line += f"\nFonte: {link}"
        pending.append((key, threshold, line))
    return pending


def mark_bond_alerts(state: dict[str, Any], pending: list[tuple[str, int, str]], now: datetime | None = None) -> None:
    today = (now or datetime.now(ROME)).astimezone(ROME).date().isoformat()
    values = state.setdefault("bond_alert_levels", {})
    for key, threshold, _ in pending:
        values[key] = threshold
    state["bond_alert_levels"] = {k: v for k, v in values.items() if k.startswith(today + "|")}


def _normalize_title(title: str) -> set[str]:
    return set(re.findall(r"[a-zà-ÿ0-9]{4,}", title.casefold()))


def _similar_title(left: str, right: str) -> bool:
    a, b = _normalize_title(left), _normalize_title(right)
    return bool(a and b and len(a & b) / len(a | b) >= 0.65)


def _news_category(title: str) -> str | None:
    if GEO_EVENT.search(title) and GEO_CONTEXT.search(title):
        return "GEOPOLITICA"
    if ECON_EVENT.search(title) and ECON_CONTEXT.search(title):
        return "ECONOMIA"
    return None


def _read_feed(source: str, url: str, now: datetime) -> list[dict[str, Any]]:
    try:
        response = requests.get(url, headers={"User-Agent": "MarketRadar/1.0 personal RSS reader"}, timeout=8)
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except (requests.RequestException, ET.ParseError) as exc:
        print(f"News RSS {source} non disponibile: {exc}")
        return []
    posts = []
    for item in root.findall("./channel/item")[:35]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        if not title or not link.startswith("https://") or not published:
            continue
        try:
            when = parsedate_to_datetime(published)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        age = now - when.astimezone(ROME)
        if not timedelta(minutes=-10) <= age <= timedelta(hours=NEWS_MAX_AGE_HOURS):
            continue
        category = _news_category(title)
        if not category:
            continue
        posts.append({"title": title, "link": link, "published": when.astimezone(ROME), "category": category, "source": source})
    return posts


def pending_news_alerts(state: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    current = (now or datetime.now(ROME)).astimezone(ROME)
    today = current.date().isoformat()
    sent = state.get("news_alerts", {})
    todays = [entry for key, entry in sent.items() if key.startswith(today + "|")]
    # Manteniamo due giorni di storico: una notizia del venerdì non deve
    # essere ritrasmessa sabato soltanto perché è cambiata la data.
    oldest = (current.date() - timedelta(days=2)).isoformat()
    recent_titles = [
        str(entry.get("title", ""))
        for key, entry in sent.items()
        if key[:10] >= oldest and isinstance(entry, dict)
    ]
    limit = min(NEWS_MAX_PER_SCAN, max(0, NEWS_MAX_PER_DAY - len(todays)))
    if limit == 0:
        return []
    news = []
    for source, url in RSS_FEEDS:
        news.extend(_read_feed(source, url, current))
    # Priorità alle notizie geopolitiche, poi ai titoli più recenti.
    news.sort(key=lambda item: (item["category"] == "GEOPOLITICA", item["published"]), reverse=True)
    result = []
    for item in news:
        digest = hashlib.sha256(item["link"].encode("utf-8")).hexdigest()[:18]
        key = today + "|" + digest
        if any(old_key.endswith("|" + digest) for old_key in sent):
            continue
        titles = recent_titles + [x["title"] for x in result]
        if any(_similar_title(item["title"], title) for title in titles):
            continue
        item["key"] = key
        result.append(item)
        if len(result) >= limit:
            break
    return result


def format_news_alert(news: dict[str, Any]) -> str:
    when = news["published"].astimezone(ROME).strftime("%d/%m/%Y %H:%M")
    return (f"📰 MARKET RADAR — {news['category']}\n"
            f"{news['title']}\n"
            f"Fonte: {news['source']} — {when} Europe/Rome\n"
            f"{news['link']}\n"
            "Notizia potenzialmente rilevante: verificare l'impatto sui mercati, nessun segnale operativo.")


def mark_news_alert(state: dict[str, Any], news: dict[str, Any], now: datetime | None = None) -> None:
    current = (now or datetime.now(ROME)).astimezone(ROME).date()
    oldest = (current - timedelta(days=2)).isoformat()
    sent = state.setdefault("news_alerts", {})
    sent[news["key"]] = {"title": news["title"]}
    state["news_alerts"] = {
        key: value for key, value in sent.items() if key[:10] >= oldest
    }
