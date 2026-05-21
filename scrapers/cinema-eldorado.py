#!/usr/bin/env python3
"""
Scraper Cinéma Eldorado (Dijon)
================================

Cinéma art & essai historique de Dijon.
Site : cinema-eldorado.fr — CMS inconnu, à inspecter.

Les cinémas indépendants publient souvent leur programmation via :
  - Une API JSON (/programmation.json, /api/films, etc.)
  - Un CMS Allocine-like avec balises schema.org (Event)
  - Des pages HTML simples avec dates en texte

Ce scraper tente :
  1. API JSON /api/programmation ou /programmation.json
  2. Balises schema.org <script type="application/ld+json"> avec @type Event/Movie
  3. Fallback HTML avec <time> ou sélecteurs communs de cinéma

Lance simplement :
    python scrapers/cinema-eldorado.py

Sortie : docs/cinema-eldorado.ics

⚠️  Maintenance : inspecter https://www.cinema-eldorado.fr dans
    DevTools → Network pour identifier l'API de programmation.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://www.cinema-eldorado.fr"
JSON_APIS   = [
    BASE_URL + "/api/programmation",
    BASE_URL + "/programmation.json",
    BASE_URL + "/api/films",
]
PROG_URLS   = [BASE_URL + "/programmation", BASE_URL + "/agenda", BASE_URL + "/films"]
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "cinema-eldorado.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "Cinéma Eldorado — 21 Rue Alfred de Musset, 21000 Dijon"


def _esc(s: str) -> str:
    return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")

def _fmt_date(y:int,m:int,d:int) -> str:
    return f"{y:04d}{m:02d}{d:02d}"

def _fmt_dt(y:int,mo:int,d:int,h:int,mi:int) -> str:
    return f"{y:04d}{mo:02d}{d:02d}T{h:02d}{mi:02d}00"

def _add_day(y:int,m:int,d:int) -> tuple[int,int,int]:
    dt = datetime(y,m,d)+timedelta(days=1)
    return dt.year,dt.month,dt.day


def try_json_api(session: requests.Session) -> list[dict] | None:
    for url in JSON_APIS:
        try:
            r = session.get(url, timeout=TIMEOUT)
            if r.status_code in (404,400,401,403):
                continue
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue

        events = []
        items = data if isinstance(data, list) else data.get("films", data.get("events", data.get("programmation", [])))
        for item in items:
            title = item.get("titre") or item.get("title") or item.get("nom","(film)")
            # Cherche les séances
            seances = item.get("seances") or item.get("screenings") or item.get("horaires",[])
            if seances:
                for s in seances:
                    dt_str = s.get("date") or s.get("datetime") or s.get("horaire","")
                    if not dt_str:
                        continue
                    try:
                        sd = datetime.fromisoformat(dt_str[:16])
                        ed = sd + timedelta(hours=2)
                        events.append({"title":title,"url":item.get("url",""),
                                       "start":sd,"end":ed,"allday":False})
                    except ValueError:
                        continue
            else:
                # Pas de séances détaillées, cherche une plage de dates
                start_s = item.get("date_debut") or item.get("start","")
                end_s   = item.get("date_fin") or item.get("end","")
                if start_s:
                    try:
                        sd = datetime.fromisoformat(start_s[:10])
                        ed = datetime.fromisoformat(end_s[:10]) if end_s else sd
                        events.append({"title":title,"url":item.get("url",""),
                                       "start":(sd.year,sd.month,sd.day),
                                       "end":(ed.year,ed.month,ed.day),"allday":True})
                    except ValueError:
                        continue
        if events:
            return events

    return None


def try_schema_org(session: requests.Session) -> list[dict]:
    """Cherche des blocs JSON-LD avec @type ScreeningEvent ou Event."""
    events = []
    for url in PROG_URLS:
        try:
            r = session.get(url, timeout=TIMEOUT)
            if r.status_code == 404:
                continue
            r.raise_for_status()
        except Exception:
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        for script in soup.find_all("script", {"type":"application/ld+json"}):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                t = item.get("@type","")
                if t not in ("Event","ScreeningEvent","MovieEvent","MusicEvent","TheaterEvent"):
                    continue
                title = item.get("name","(événement)")
                url_ev = item.get("url","")
                start_s = item.get("startDate","")
                end_s   = item.get("endDate","")
                if not start_s:
                    continue
                try:
                    if "T" in start_s:
                        sd = datetime.fromisoformat(start_s[:16])
                        ed_s2 = end_s if end_s and "T" in end_s else ""
                        ed = datetime.fromisoformat(ed_s2[:16]) if ed_s2 else sd+timedelta(hours=2)
                        events.append({"title":title,"url":url_ev,"start":sd,"end":ed,"allday":False})
                    else:
                        sd = datetime.fromisoformat(start_s[:10])
                        ed = datetime.fromisoformat(end_s[:10]) if end_s else sd
                        events.append({"title":title,"url":url_ev,
                                       "start":(sd.year,sd.month,sd.day),
                                       "end":(ed.year,ed.month,ed.day),"allday":True})
                except ValueError:
                    continue
        if events:
            break
    return events


def try_html(session: requests.Session) -> list[dict]:
    events = []
    for url in PROG_URLS:
        try:
            r = session.get(url, timeout=TIMEOUT)
            if r.status_code == 404:
                continue
            r.raise_for_status()
        except Exception:
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        for time_el in soup.find_all("time", {"datetime": True}):
            dt_str = time_el.get("datetime","").strip()
            m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?", dt_str)
            if not m:
                continue
            y,mo,d = int(m.group(1)),int(m.group(2)),int(m.group(3))
            h  = int(m.group(4)) if m.group(4) else None
            mi = int(m.group(5)) if m.group(5) else 0

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

            if h is not None:
                sd = datetime(y,mo,d,h,mi)
                events.append({"title":title or "(film)","url":href,
                                "start":sd,"end":sd+timedelta(hours=2),"allday":False})
            else:
                events.append({"title":title or "(film)","url":href,
                                "start":(y,mo,d),"end":(y,mo,d),"allday":True})
        if events:
            break
    return events


def build_ics(events: list[dict]) -> list[str]:
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR","VERSION:2.0",
        "PRODID:-//cinema-eldorado-scraper//EN",
        "CALSCALE:GREGORIAN","METHOD:PUBLISH",
        "X-WR-CALNAME:Cinéma Eldorado — Programmation",
        "X-WR-TIMEZONE:Europe/Paris",
    ]
    for i, ev in enumerate(events):
        if ev.get("allday"):
            sy,sm,sd_ = ev["start"]
            dtstart = f"DTSTART;VALUE=DATE:{_fmt_date(sy,sm,sd_)}"
            ny,nm,nd_ = _add_day(sy,sm,sd_)
            dtend   = f"DTEND;VALUE=DATE:{_fmt_date(ny,nm,nd_)}"
            uid_d   = f"{sy}{sm:02d}{sd_:02d}"
        else:
            s,e = ev["start"],ev["end"]
            dtstart = f"DTSTART;TZID=Europe/Paris:{_fmt_dt(s.year,s.month,s.day,s.hour,s.minute)}"
            dtend   = f"DTEND;TZID=Europe/Paris:{_fmt_dt(e.year,e.month,e.day,e.hour,e.minute)}"
            uid_d   = s.strftime("%Y%m%d%H%M")

        slug = ev.get("url","").rstrip("/").rsplit("/",1)[-1] or f"ev-{i}"
        uid  = f"eldorado-{slug}-{uid_d}@cinema-eldorado.fr"

        lines += ["BEGIN:VEVENT",f"UID:{uid}",f"DTSTAMP:{now_stamp}",
                  dtstart,dtend,
                  f"SUMMARY:{_esc('[Eldorado] '+ev['title'])}",
                  f"LOCATION:{_esc(LOCATION)}"]
        if ev.get("url"):
            lines.append(f"URL:{ev['url']}")
            lines.append(f"DESCRIPTION:{_esc(ev['url'])}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return lines


def main() -> int:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    print("=== Cinéma Eldorado ===", file=sys.stderr)

    events = try_json_api(session)
    if events:
        print(f"  → API JSON : {len(events)} événements", file=sys.stderr)
    else:
        events = try_schema_org(session)
        if events:
            print(f"  → Schema.org JSON-LD : {len(events)} événements", file=sys.stderr)
        else:
            print("  → Fallback HTML", file=sys.stderr)
            events = try_html(session)
            print(f"  → HTML : {len(events)} événements", file=sys.stderr)

    if not events:
        print("  ⚠️  0 événements — vérifier API ou sélecteurs", file=sys.stderr)

    lines = build_ics(events or [])
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines), encoding="utf-8")
    print(f"  → {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
