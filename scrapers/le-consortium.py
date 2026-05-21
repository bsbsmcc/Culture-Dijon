#!/usr/bin/env python3
"""
Scraper Le Consortium (Dijon)
==============================

Le Consortium est un centre d'art contemporain majeur de Dijon.
Son site (leconsortium.fr) utilise un CMS custom.

Stratégie :
  - Scrape la page /expositions (ou /agenda selon la structure du site)
  - Cherche des cartes d'exposition avec dates de début/fin
  - Format attendu : "15 septembre — 12 janvier 2026" ou balises <time>

Lance simplement :
    python scrapers/le-consortium.py

Sortie : docs/le-consortium.ics

⚠️  Maintenance : inspecter https://www.leconsortium.fr/expositions
    pour identifier les sélecteurs CSS corrects.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://www.leconsortium.fr"
EXPO_URLS   = [BASE_URL + "/expositions", BASE_URL + "/agenda", BASE_URL + "/programme"]
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "le-consortium.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "Le Consortium — 37 Rue de Longvic, 21000 Dijon"

MOIS = {
    "janvier":1,"janv":1,
    "février":2,"fevrier":2,"fév":2,
    "mars":3,
    "avril":4,"avr":4,
    "mai":5,
    "juin":6,
    "juillet":7,"juil":7,
    "août":8,"aout":8,
    "septembre":9,"sept":9,
    "octobre":10,"oct":10,
    "novembre":11,"nov":11,
    "décembre":12,"decembre":12,"déc":12,
}


def _esc(s: str) -> str:
    return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")


def _fmt_date(y: int, m: int, d: int) -> str:
    return f"{y:04d}{m:02d}{d:02d}"


def _add_day(y: int, m: int, d: int) -> tuple[int,int,int]:
    dt = datetime(y, m, d) + timedelta(days=1)
    return dt.year, dt.month, dt.day


def _parse_french_date(s: str, ref_year: int = None) -> tuple[int,int,int] | None:
    """Parse une date française comme '15 septembre 2025' ou '15 sept'."""
    s = s.lower().strip().replace(".", "")
    if ref_year is None:
        ref_year = datetime.now().year
    m = re.match(r"^(\d{1,2})\s+([a-zûéèà]+)\s+(\d{4})$", s)
    if m:
        d, mo_s, y = m.groups()
        mo = MOIS.get(mo_s)
        if mo:
            return int(y), mo, int(d)
    m = re.match(r"^(\d{1,2})\s+([a-zûéèà]+)$", s)
    if m:
        d, mo_s = m.groups()
        mo = MOIS.get(mo_s)
        if mo:
            return ref_year, mo, int(d)
    return None


def _parse_date_range(text: str) -> tuple[tuple,tuple] | None:
    """Parse '15 sept — 12 janv 2026' ou '15 septembre 2025 – 12 janvier 2026'."""
    text = text.strip()
    for sep in [" — ", " – ", " au ", " - "]:
        if sep in text.lower():
            parts = re.split(sep, text, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) == 2:
                # L'année peut être absente sur la première date
                d2 = _parse_french_date(parts[1].strip())
                ref_y = d2[0] if d2 else datetime.now().year
                d1 = _parse_french_date(parts[0].strip(), ref_year=ref_y)
                if d1 and d2:
                    return d1, d2
    single = _parse_french_date(text)
    if single:
        return single, single
    return None


def scrape_page(url: str, session: requests.Session) -> list[dict]:
    print(f"  GET {url}", file=sys.stderr)
    r = session.get(url, timeout=TIMEOUT)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    events = []

    # Stratégie A : balises <time datetime="YYYY-MM-DD">
    for time_el in soup.find_all("time", {"datetime": True}):
        dt_str = time_el.get("datetime","").strip()
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", dt_str)
        if not m:
            continue
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        card = time_el.find_parent(["article","div","li","a"])
        title = ""
        if card:
            for tag in card.find_all(re.compile(r"h[1-6]")):
                t = tag.get_text(strip=True)
                if t:
                    title = t
                    break
        link_el = (card.find("a", href=True) if card else None)
        href = ""
        if link_el:
            h = link_el.get("href","")
            href = h if h.startswith("http") else BASE_URL + h
        events.append({
            "title": title or "(exposition)",
            "url": href,
            "start": (y, mo, d),
            "end": (y, mo, d),
        })

    if events:
        return events

    # Stratégie B : cherche des cartes avec texte de date français
    cards = soup.find_all(["article","li"], class_=re.compile(r"event|expo|programme|item", re.I))
    if not cards:
        cards = soup.find_all("article")

    for card in cards:
        title_el = card.find(re.compile(r"h[1-6]"))
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        # Cherche un texte de date dans le texte de la carte
        text = card.get_text(separator=" ", strip=True)
        date_range = None
        for chunk in re.findall(r"[\d]{1,2}\s+[a-zûéèàé]+\.?\s*(?:20\d\d)?(?:\s*[—–-]\s*[\d]{1,2}\s+[a-zûéèàé]+\.?\s*20\d\d)?", text, re.I):
            date_range = _parse_date_range(chunk)
            if date_range:
                break

        if not date_range:
            continue

        link_el = card.find("a", href=True)
        href = ""
        if link_el:
            h = link_el.get("href","")
            href = h if h.startswith("http") else BASE_URL + h

        events.append({
            "title": title,
            "url": href,
            "start": date_range[0],
            "end": date_range[1],
        })

    return events


def main() -> int:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    print("=== Le Consortium ===", file=sys.stderr)

    all_events: list[dict] = []
    for url in EXPO_URLS:
        batch = scrape_page(url, session)
        all_events.extend(batch)
        if batch:
            break  # première URL qui retourne des résultats suffit

    # Dédupliquer par (titre, start)
    seen: set[tuple] = set()
    unique = []
    for ev in all_events:
        key = (ev["title"], ev["start"])
        if key not in seen:
            seen.add(key)
            unique.append(ev)

    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//le-consortium-scraper//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Le Consortium — Expositions",
        "X-WR-TIMEZONE:Europe/Paris",
    ]

    for i, ev in enumerate(unique):
        sy, sm, sd = ev["start"]
        ey, em, ed = ev["end"]
        slug = ev["url"].rstrip("/").rsplit("/",1)[-1] if ev["url"] else f"ev-{i}"
        uid  = f"consortium-{slug}-{sy}{sm:02d}{sd:02d}@leconsortium.fr"
        ny, nm, nd = _add_day(ey, em, ed)

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART;VALUE=DATE:{_fmt_date(sy,sm,sd)}",
            f"DTEND;VALUE=DATE:{_fmt_date(ny,nm,nd)}",
            f"SUMMARY:{_esc('[Consortium] ' + ev['title'])}",
            f"LOCATION:{_esc(LOCATION)}",
        ]
        if ev["url"]:
            lines.append(f"URL:{ev['url']}")
            lines.append(f"DESCRIPTION:{_esc(ev['url'])}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines), encoding="utf-8")
    print(f"  Événements générés : {len(unique)}", file=sys.stderr)
    if not unique:
        print("  ⚠️  0 événements — vérifier sélecteurs ou URL agenda", file=sys.stderr)
    print(f"  → {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
