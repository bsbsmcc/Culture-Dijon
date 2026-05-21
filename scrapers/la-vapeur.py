#!/usr/bin/env python3
"""
Scraper La Vapeur (Dijon)
=========================

La Vapeur est une salle de musiques actuelles. Son site (lavapeur.com)
utilise un CMS custom. Ce scraper tente deux stratégies dans l'ordre :

  1. API REST WordPress (wp-json/wp/v2/evenement ou tribe_events)
     → si le site tourne sous WordPress + The Events Calendar
  2. Fallback HTML : scrape la page /programmation avec BeautifulSoup
     → cherche des balises <time> ou attributs data-date

Lance simplement :
    python scrapers/la-vapeur.py

Sortie : docs/la-vapeur.ics

⚠️  Maintenance : si le scraper échoue (0 événements), inspecter
    https://www.lavapeur.com/programmation dans DevTools → Network
    pour identifier l'API ou les sélecteurs CSS à jour.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://www.lavapeur.com"
PROG_URL    = BASE_URL + "/programmation"
WP_API_URL  = BASE_URL + "/wp-json/tribe/events/v1/events?per_page=50&page={page}&status=publish"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "la-vapeur.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "La Vapeur — 42 Avenue de Stalingrad, 21000 Dijon"

MOIS = {
    "janvier":1,"janv":1,"jan":1,
    "février":2,"fevrier":2,"fév":2,"fev":2,
    "mars":3,
    "avril":4,"avr":4,
    "mai":5,
    "juin":6,
    "juillet":7,"juil":7,
    "août":8,"aout":8,
    "septembre":9,"sept":9,
    "octobre":10,"oct":10,
    "novembre":11,"nov":11,
    "décembre":12,"decembre":12,"déc":12,"dec":12,
}


def _esc(s: str) -> str:
    return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")


def _fmt_date(y: int, m: int, d: int) -> str:
    return f"{y:04d}{m:02d}{d:02d}"


def _fmt_dt(y: int, mo: int, d: int, h: int, mi: int) -> str:
    return f"{y:04d}{mo:02d}{d:02d}T{h:02d}{mi:02d}00"


def _add_day(y: int, m: int, d: int) -> tuple[int,int,int]:
    dt = datetime(y, m, d) + timedelta(days=1)
    return dt.year, dt.month, dt.day


# ── Stratégie 1 : API The Events Calendar (WordPress) ──────────────────────────

def try_wp_api(session: requests.Session) -> list[dict] | None:
    """Tente de récupérer les événements via l'API tribe/events/v1.
    Retourne une liste de dicts normalisés, ou None si l'API n'existe pas."""
    url = WP_API_URL.format(page=1)
    try:
        r = session.get(url, timeout=TIMEOUT)
        if r.status_code in (404, 400):
            return None
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    events_raw = data.get("events", [])
    total_pages = data.get("total_pages", 1)

    for page in range(2, total_pages + 1):
        r2 = session.get(WP_API_URL.format(page=page), timeout=TIMEOUT)
        if r2.ok:
            events_raw.extend(r2.json().get("events", []))

    result = []
    for ev in events_raw:
        title = ev.get("title", "(sans titre)")
        url_ev = ev.get("url", "")
        start_str = ev.get("start_date", "")   # "2026-05-15 20:30:00"
        end_str   = ev.get("end_date", "")
        try:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
            end_dt   = datetime.strptime(end_str,   "%Y-%m-%d %H:%M:%S") if end_str else start_dt + timedelta(hours=3)
        except ValueError:
            continue
        result.append({"title": title, "url": url_ev, "start": start_dt, "end": end_dt})
    return result


# ── Stratégie 2 : HTML BeautifulSoup ────────────────────────────────────────────

def try_html_scrape(session: requests.Session) -> list[dict]:
    """Scrape la page programmation HTML. Cherche des cartes avec <time datetime>."""
    r = session.get(PROG_URL, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    events = []
    # Cherche tous les éléments <time> avec attribut datetime ISO
    for time_el in soup.find_all("time", {"datetime": True}):
        dt_str = time_el.get("datetime", "").strip()
        # Formats attendus : "2026-05-15", "2026-05-15T20:30"
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?", dt_str)
        if not m:
            continue
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        h, mi = (int(m.group(4)), int(m.group(5))) if m.group(4) else (None, None)

        # Remonter au conteneur de la carte pour trouver le titre
        card = time_el.find_parent(["article", "div", "li", "a"])
        title = ""
        if card:
            for tag in card.find_all(re.compile(r"^h[1-6]$")):
                t = tag.get_text(strip=True)
                if t:
                    title = t
                    break
            if not title:
                title = card.get_text(separator=" ", strip=True)[:80]

        link = ""
        a_tag = (card.find("a", href=True) if card else None) or time_el.find_parent("a")
        if a_tag:
            href = a_tag.get("href", "")
            link = href if href.startswith("http") else BASE_URL + href

        if h is not None:
            start_dt = datetime(y, mo, d, h, mi)
            end_dt   = start_dt + timedelta(hours=3)
        else:
            start_dt = datetime(y, mo, d)
            end_dt   = start_dt

        events.append({"title": title or "(sans titre)", "url": link, "start": start_dt, "end": end_dt, "allday": h is None})

    return events


# ── Génération ICS ──────────────────────────────────────────────────────────────

def build_ics(events: list[dict], via_api: bool) -> list[str]:
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//la-vapeur-scraper//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:La Vapeur — Programmation",
        "X-WR-TIMEZONE:Europe/Paris",
    ]
    for i, ev in enumerate(events):
        start = ev["start"]
        end   = ev["end"]
        uid   = f"vapeur-{i}-{start.strftime('%Y%m%d')}@lavapeur.com"
        slug  = ev.get("url","").rstrip("/").rsplit("/",1)[-1]
        if slug:
            uid = f"vapeur-{slug}@lavapeur.com"

        allday = ev.get("allday", False) or (not via_api and not hasattr(start, "hour"))

        if allday or (start.hour == 0 and start.minute == 0 and (end - start).total_seconds() >= 20*3600):
            ny, nm, nd = _add_day(start.year, start.month, start.day)
            dtstart = f"DTSTART;VALUE=DATE:{_fmt_date(start.year, start.month, start.day)}"
            dtend   = f"DTEND;VALUE=DATE:{_fmt_date(ny, nm, nd)}"
        else:
            dtstart = f"DTSTART;TZID=Europe/Paris:{_fmt_dt(start.year,start.month,start.day,start.hour,start.minute)}"
            dtend   = f"DTEND;TZID=Europe/Paris:{_fmt_dt(end.year,end.month,end.day,end.hour,end.minute)}"

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_stamp}",
            dtstart,
            dtend,
            f"SUMMARY:{_esc('[Vapeur] ' + ev['title'])}",
            f"LOCATION:{_esc(LOCATION)}",
        ]
        if ev.get("url"):
            lines.append(f"URL:{ev['url']}")
            lines.append(f"DESCRIPTION:{_esc(ev['url'])}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return lines


def main() -> int:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    print("=== La Vapeur ===", file=sys.stderr)

    events = try_wp_api(session)
    via_api = events is not None
    if via_api:
        print(f"  → API WordPress : {len(events)} événements", file=sys.stderr)
    else:
        print("  → API non disponible, fallback HTML", file=sys.stderr)
        events = try_html_scrape(session)
        print(f"  → HTML scrape : {len(events)} événements", file=sys.stderr)

    if not events:
        print("  ⚠️  0 événements — vérifier les sélecteurs ou l'API", file=sys.stderr)

    lines = build_ics(events, via_api)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines), encoding="utf-8")
    print(f"  → {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
