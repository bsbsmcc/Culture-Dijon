#!/usr/bin/env python3
"""
Scraper Grand Kursaal (Dijon)
==============================

Le Grand Kursaal est une salle de spectacles polyvalente.
Site : grandkursaal.com — CMS inconnu, à inspecter.

Stratégies :
  1. API tribe/events/v1 (WordPress + The Events Calendar)
  2. Scrape HTML de /programmation avec balises <time>

Lance simplement :
    python scrapers/grand-kursaal.py

Sortie : docs/grand-kursaal.ics

⚠️  Maintenance : inspecter https://www.grandkursaal.com/programmation
    pour adapter les sélecteurs CSS ou identifier l'API.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://www.grandkursaal.com"
WP_API      = BASE_URL + "/wp-json/tribe/events/v1/events?per_page=50&page={page}"
PROG_URLS   = [BASE_URL + "/programmation", BASE_URL + "/agenda", BASE_URL + "/concerts"]
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "grand-kursaal.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "Grand Kursaal — 20 Rue du Stade, 21000 Dijon"


def _esc(s: str) -> str:
    return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")

def _fmt_date(y:int,m:int,d:int) -> str:
    return f"{y:04d}{m:02d}{d:02d}"

def _fmt_dt(y:int,mo:int,d:int,h:int,mi:int) -> str:
    return f"{y:04d}{mo:02d}{d:02d}T{h:02d}{mi:02d}00"

def _add_day(y:int,m:int,d:int) -> tuple[int,int,int]:
    dt = datetime(y,m,d)+timedelta(days=1)
    return dt.year,dt.month,dt.day


def try_tribe_api(session: requests.Session) -> list[dict] | None:
    url = WP_API.format(page=1)
    try:
        r = session.get(url, timeout=TIMEOUT)
        if r.status_code in (404,400,401):
            return None
        r.raise_for_status()
        data = r.json()
        if "events" not in data:
            return None
    except Exception:
        return None

    events_raw = data["events"]
    for page in range(2, data.get("total_pages",1)+1):
        r2 = session.get(WP_API.format(page=page), timeout=TIMEOUT)
        if r2.ok:
            events_raw.extend(r2.json().get("events",[]))

    result = []
    for ev in events_raw:
        try:
            sd = datetime.strptime(ev.get("start_date",""), "%Y-%m-%d %H:%M:%S")
            ed_s = ev.get("end_date","")
            ed = datetime.strptime(ed_s, "%Y-%m-%d %H:%M:%S") if ed_s else sd+timedelta(hours=3)
        except ValueError:
            continue
        result.append({
            "title": ev.get("title","(sans titre)"),
            "url": ev.get("url",""),
            "start": sd, "end": ed, "allday": False
        })
    return result


def try_html(session: requests.Session) -> list[dict]:
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
                events.append({"title":title or "(spectacle)","url":href,
                                "start":sd,"end":sd+timedelta(hours=3),"allday":False})
            else:
                events.append({"title":title or "(spectacle)","url":href,
                                "start":(y,mo,d),"end":(y,mo,d),"allday":True})

        if events:
            break
    return events


def build_ics(events: list[dict]) -> list[str]:
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR","VERSION:2.0",
        "PRODID:-//grand-kursaal-scraper//EN",
        "CALSCALE:GREGORIAN","METHOD:PUBLISH",
        "X-WR-CALNAME:Grand Kursaal — Programmation",
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
            uid_d   = s.strftime("%Y%m%d")

        slug = ev.get("url","").rstrip("/").rsplit("/",1)[-1] or f"ev-{i}"
        uid  = f"kursaal-{slug}-{uid_d}@grandkursaal.com"

        lines += ["BEGIN:VEVENT",f"UID:{uid}",f"DTSTAMP:{now_stamp}",
                  dtstart,dtend,
                  f"SUMMARY:{_esc('[Kursaal] '+ev['title'])}",
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
    print("=== Grand Kursaal ===", file=sys.stderr)

    events = try_tribe_api(session)
    if events is not None:
        print(f"  → API tribe : {len(events)} événements", file=sys.stderr)
    else:
        print("  → Fallback HTML", file=sys.stderr)
        events = try_html(session)
        print(f"  → HTML : {len(events)} événements", file=sys.stderr)

    if not events:
        print("  ⚠️  0 événements — vérifier sélecteurs ou API", file=sys.stderr)

    lines = build_ics(events)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines), encoding="utf-8")
    print(f"  → {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
