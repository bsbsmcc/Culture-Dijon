#!/usr/bin/env python3
"""
Scraper FRAC Bourgogne (Dijon)
================================

Le FRAC (Fonds Régional d'Art Contemporain) Bourgogne présente
des expositions et événements d'art contemporain.
Site : frac-bourgogne.org — site custom.

Stratégie :
  - Scrape /agenda ou /expositions
  - Détecte les balises <time>, schema.org JSON-LD,
    ou blocs HTML avec dates françaises

Lance simplement :
    python scrapers/frac-bourgogne.py

Sortie : docs/frac-bourgogne.ics

⚠️  Maintenance : inspecter https://www.frac-bourgogne.org/agenda
    pour identifier les sélecteurs CSS à jour.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://www.frac-bourgogne.org"
PROG_URLS   = [BASE_URL + "/agenda", BASE_URL + "/expositions", BASE_URL + "/evenements", BASE_URL]
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "frac-bourgogne.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "FRAC Bourgogne — 49 Rue de Longvic, 21000 Dijon"

MOIS = {
    "janvier":1,"janv":1,"février":2,"fevrier":2,"fév":2,"mars":3,
    "avril":4,"avr":4,"mai":5,"juin":6,"juillet":7,"juil":7,
    "août":8,"aout":8,"septembre":9,"sept":9,"octobre":10,"oct":10,
    "novembre":11,"nov":11,"décembre":12,"decembre":12,"déc":12,
}


def _esc(s: str) -> str:
    return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")

def _fmt_date(y:int,m:int,d:int) -> str:
    return f"{y:04d}{m:02d}{d:02d}"

def _add_day(y:int,m:int,d:int) -> tuple[int,int,int]:
    dt = datetime(y,m,d)+timedelta(days=1)
    return dt.year,dt.month,dt.day


def _parse_fr_date(s: str, ref_year: int | None = None) -> tuple[int,int,int] | None:
    """Parse '15 septembre 2025', '15 sept 2025', '15 septembre'."""
    s = s.lower().strip().replace(".","")
    ry = ref_year or datetime.now().year
    m = re.match(r"^(\d{1,2})\s+([a-zûéèà]+)\s+(\d{4})$", s)
    if m:
        d,mo_s,y = m.groups()
        mo = MOIS.get(mo_s)
        if mo:
            return int(y), mo, int(d)
    m = re.match(r"^(\d{1,2})\s+([a-zûéèà]+)$", s)
    if m:
        d,mo_s = m.groups()
        mo = MOIS.get(mo_s)
        if mo:
            return ry, mo, int(d)
    return None


def scrape(session: requests.Session) -> list[dict]:
    events = []

    for url in PROG_URLS:
        print(f"  GET {url}", file=sys.stderr)
        try:
            r = session.get(url, timeout=TIMEOUT)
            if r.status_code == 404:
                continue
            r.raise_for_status()
        except Exception:
            continue

        soup = BeautifulSoup(r.text, "html.parser")

        # Stratégie A : JSON-LD schema.org
        for script in soup.find_all("script", {"type":"application/ld+json"}):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type","") not in ("Event","ExhibitionEvent","VisualArtsEvent"):
                    continue
                title = item.get("name","(exposition)")
                start_s = item.get("startDate","")
                end_s   = item.get("endDate","")
                if not start_s:
                    continue
                try:
                    sd = datetime.fromisoformat(start_s[:10])
                    ed = datetime.fromisoformat(end_s[:10]) if end_s else sd
                    events.append({
                        "title": title,
                        "url": item.get("url",""),
                        "start": (sd.year,sd.month,sd.day),
                        "end": (ed.year,ed.month,ed.day),
                        "allday": True,
                    })
                except ValueError:
                    continue

        if events:
            break

        # Stratégie B : balises <time datetime>
        for time_el in soup.find_all("time", {"datetime": True}):
            dt_str = time_el.get("datetime","").strip()
            m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", dt_str)
            if not m:
                continue
            y,mo,d = int(m.group(1)),int(m.group(2)),int(m.group(3))

            card = time_el.find_parent(["article","div","li","section"])
            title = ""
            if card:
                for tag in card.find_all(re.compile(r"h[1-6]")):
                    t = tag.get_text(strip=True)
                    if t:
                        title = t
                        break
            link_el = card.find("a", href=True) if card else None
            href = ""
            if link_el:
                lh = link_el.get("href","")
                href = lh if lh.startswith("http") else BASE_URL+lh

            events.append({
                "title": title or "(exposition)",
                "url": href,
                "start": (y,mo,d),
                "end": (y,mo,d),
                "allday": True,
            })

        if events:
            break

        # Stratégie C : articles/sections avec dates en texte français
        candidates = soup.find_all(["article","section","li"],
                                   class_=re.compile(r"event|expo|agenda|item|card", re.I))
        if not candidates:
            candidates = soup.find_all("article")

        for card in candidates:
            title_el = card.find(re.compile(r"h[1-6]"))
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                continue

            text = card.get_text(separator=" ", strip=True)
            date_range = None
            patterns = re.findall(
                r"\d{1,2}\s+[a-zûéèà]+\.?\s*(?:20\d\d)?\s*(?:—|–|au|-)\s*\d{1,2}\s+[a-zûéèà]+\.?\s*20\d\d",
                text, re.I
            )
            for chunk in patterns:
                for sep in [" — "," – "," au "," - "]:
                    if sep.lower() in chunk.lower():
                        parts = re.split(sep, chunk, maxsplit=1, flags=re.IGNORECASE)
                        if len(parts) == 2:
                            d2 = _parse_fr_date(parts[1].strip())
                            ry = d2[0] if d2 else None
                            d1 = _parse_fr_date(parts[0].strip(), ref_year=ry)
                            if d1 and d2:
                                date_range = (d1, d2)
                                break
                if date_range:
                    break

            if not date_range:
                single = re.search(r"\d{1,2}\s+[a-zûéèà]+\.?\s+20\d\d", text, re.I)
                if single:
                    parsed = _parse_fr_date(single.group())
                    if parsed:
                        date_range = (parsed, parsed)

            if not date_range:
                continue

            link_el = card.find("a", href=True)
            href = ""
            if link_el:
                lh = link_el.get("href","")
                href = lh if lh.startswith("http") else BASE_URL+lh

            events.append({
                "title": title,
                "url": href,
                "start": date_range[0],
                "end": date_range[1],
                "allday": True,
            })

        if events:
            break

    return events


def main() -> int:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    print("=== FRAC Bourgogne ===", file=sys.stderr)

    events = scrape(session)

    # Dédupliquer
    seen: set[tuple] = set()
    unique = []
    for ev in events:
        key = (ev["title"], ev["start"])
        if key not in seen:
            seen.add(key)
            unique.append(ev)

    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR","VERSION:2.0",
        "PRODID:-//frac-bourgogne-scraper//EN",
        "CALSCALE:GREGORIAN","METHOD:PUBLISH",
        "X-WR-CALNAME:FRAC Bourgogne — Expositions",
        "X-WR-TIMEZONE:Europe/Paris",
    ]
    for i, ev in enumerate(unique):
        sy,sm,sd_ = ev["start"]
        ey,em,ed_ = ev["end"]
        slug = ev["url"].rstrip("/").rsplit("/",1)[-1] if ev["url"] else f"ev-{i}"
        uid  = f"frac-{slug}-{sy}{sm:02d}{sd_:02d}@frac-bourgogne.org"
        ny,nm,nd_ = _add_day(ey,em,ed_)

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART;VALUE=DATE:{_fmt_date(sy,sm,sd_)}",
            f"DTEND;VALUE=DATE:{_fmt_date(ny,nm,nd_)}",
            f"SUMMARY:{_esc('[FRAC] '+ev['title'])}",
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
        print("  ⚠️  0 événements — vérifier sélecteurs ou URL", file=sys.stderr)
    print(f"  → {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
