#!/usr/bin/env python3
"""
Scraper Réseau Dijon Musées — MBA & associés
=============================================

Le réseau Dijon Musées regroupe plusieurs établissements :
  - Musée des Beaux-Arts (mba.dijon.fr)
  - Musée Magnin
  - Musée de la Vie Bourguignonne
  - Musée Archéologique
  - Musée Rude

Tous partagent le même portail (mba.dijon.fr). Ce scraper
cherche un flux ICS agrégé ou scrape la page agenda du réseau.

Lance simplement :
    python scrapers/mba-dijon.py

Sortie : docs/mba-dijon.ics

⚠️  Maintenance : inspecter https://www.mba.dijon.fr/agenda
    pour adapter les sélecteurs ou trouver un flux ICS.
    Chercher "ics", "ical", "export" dans le HTML source.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://www.mba.dijon.fr"
# Tenter un flux ICS natif en premier
ICS_CANDIDATES = [
    BASE_URL + "/agenda/export.ics",
    BASE_URL + "/agenda?format=ics",
    BASE_URL + "/feed/ics",
    "https://openagenda.com/agendas/DIJON-MUSEES/events.v2.ics?relative%5B0%5D=current&relative%5B1%5D=upcoming",
]
PROG_URLS   = [BASE_URL + "/agenda", BASE_URL + "/evenements", BASE_URL + "/programme"]
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "mba-dijon.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "Musée des Beaux-Arts de Dijon — 1 Rue Rameau, 21000 Dijon"

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


def try_ics_direct(session: requests.Session) -> bytes | None:
    """Tente de récupérer un flux ICS direct du réseau."""
    for url in ICS_CANDIDATES:
        if "DIJON-MUSEES" in url:
            continue  # placeholder, skip
        try:
            r = session.get(url, timeout=TIMEOUT)
            if r.status_code in (404,400,403):
                continue
            r.raise_for_status()
            if b"BEGIN:VCALENDAR" in r.content:
                print(f"  → Flux ICS trouvé : {url}", file=sys.stderr)
                return r.content
        except Exception:
            continue
    return None


def scrape_html(session: requests.Session) -> list[dict]:
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

        # Vérifie si la page renvoie vers un ICS
        for a_tag in soup.find_all("a", href=True):
            href = a_tag.get("href","")
            if href.endswith(".ics") or "ical" in href or "ics" in href:
                full = href if href.startswith("http") else BASE_URL+href
                print(f"  → Lien ICS découvert dans le HTML : {full}", file=sys.stderr)
                print(f"  ℹ️  Ajouter cette URL dans calendars.yaml à la place du scraper", file=sys.stderr)

        # JSON-LD
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
                        "title": title,"url": item.get("url",""),
                        "start": (sd.year,sd.month,sd.day),
                        "end": (ed.year,ed.month,ed.day),
                    })
                except ValueError:
                    continue

        if events:
            break

        # Balises <time>
        for time_el in soup.find_all("time", {"datetime": True}):
            dt_str = time_el.get("datetime","").strip()
            m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", dt_str)
            if not m:
                continue
            y,mo,d = int(m.group(1)),int(m.group(2)),int(m.group(3))
            card = time_el.find_parent(["article","div","li"])
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
            events.append({"title":title or "(événement)","url":href,
                           "start":(y,mo,d),"end":(y,mo,d)})

        if events:
            break

    return events


def build_ics_from_events(events: list[dict]) -> list[str]:
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR","VERSION:2.0",
        "PRODID:-//mba-dijon-scraper//EN",
        "CALSCALE:GREGORIAN","METHOD:PUBLISH",
        "X-WR-CALNAME:Dijon Musées — Agenda",
        "X-WR-TIMEZONE:Europe/Paris",
    ]
    for i, ev in enumerate(events):
        sy,sm,sd_ = ev["start"]
        ey,em,ed_ = ev["end"]
        slug = ev["url"].rstrip("/").rsplit("/",1)[-1] if ev["url"] else f"ev-{i}"
        uid  = f"mba-{slug}-{sy}{sm:02d}{sd_:02d}@mba.dijon.fr"
        ny,nm,nd_ = _add_day(ey,em,ed_)

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART;VALUE=DATE:{_fmt_date(sy,sm,sd_)}",
            f"DTEND;VALUE=DATE:{_fmt_date(ny,nm,nd_)}",
            f"SUMMARY:{_esc('[MBA] '+ev['title'])}",
            f"LOCATION:{_esc(LOCATION)}",
        ]
        if ev["url"]:
            lines.append(f"URL:{ev['url']}")
            lines.append(f"DESCRIPTION:{_esc(ev['url'])}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return lines


def main() -> int:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    print("=== Musée des Beaux-Arts / Dijon Musées ===", file=sys.stderr)

    # Essai 1 : flux ICS direct
    raw_ics = try_ics_direct(session)
    if raw_ics:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_bytes(raw_ics)
        print(f"  → Flux ICS direct sauvegardé dans {OUTPUT_PATH}", file=sys.stderr)
        print("  ℹ️  Remplacer cette entrée dans calendars.yaml par l'URL directe", file=sys.stderr)
        return 0

    # Essai 2 : scrape HTML
    print("  → Pas de flux ICS direct, scrape HTML", file=sys.stderr)
    events = scrape_html(session)
    print(f"  Événements générés : {len(events)}", file=sys.stderr)
    if not events:
        print("  ⚠️  0 événements — vérifier sélecteurs ou chercher ICS manuellement", file=sys.stderr)

    lines = build_ics_from_events(events)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines), encoding="utf-8")
    print(f"  → {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
