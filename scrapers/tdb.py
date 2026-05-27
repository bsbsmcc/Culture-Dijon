#!/usr/bin/env python3
"""
Scraper TDB — Théâtre Dijon Bourgogne (CDN)
============================================

Le TDB est un Centre Dramatique National. Son site tdb-cdn.com
utilise un CMS inconnu (à inspecter — "cdn" dans le domaine suggère
un headless CMS ou un framework JS). Le scraper tente :

  1. API REST WordPress (wp-json) — fréquent pour les théâtres institutionnels
  2. Fallback HTML : scrape /saison ou /spectacles avec <time> ou dates FR

Lance simplement :
    python scrapers/tdb.py

Sortie : docs/tdb.ics

⚠️  Maintenance : si 0 événements, inspecter https://www.tdb-cdn.com
    dans DevTools → Network pour trouver l'API ou les sélecteurs.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://www.tdb-cdn.com"
WP_API_URL  = BASE_URL + "/wp-json/tribe/events/v1/events?per_page=50&page={page}"
WP_API_V2   = BASE_URL + "/wp-json/wp/v2/spectacle?per_page=100&_fields=id,title,slug,link,acf"
PROG_URLS   = [BASE_URL + "/la-saison", BASE_URL + "/spectacles", BASE_URL + "/programme", BASE_URL + "/saison"]
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "tdb.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "Théâtre Dijon Bourgogne — Parvis Saint-Jean, 21000 Dijon"

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

def _fmt_dt(y:int,mo:int,d:int,h:int,mi:int) -> str:
    return f"{y:04d}{mo:02d}{d:02d}T{h:02d}{mi:02d}00"

def _add_day(y:int,m:int,d:int) -> tuple[int,int,int]:
    dt = datetime(y,m,d)+timedelta(days=1)
    return dt.year,dt.month,dt.day


def try_tribe_api(session: requests.Session) -> list[dict] | None:
    url = WP_API_URL.format(page=1)
    try:
        r = session.get(url, timeout=TIMEOUT)
        if r.status_code in (404, 400, 401):
            return None
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    events_raw = data.get("events", [])
    for page in range(2, data.get("total_pages", 1) + 1):
        r2 = session.get(WP_API_URL.format(page=page), timeout=TIMEOUT)
        if r2.ok:
            events_raw.extend(r2.json().get("events", []))

    result = []
    for ev in events_raw:
        title = ev.get("title","(sans titre)")
        url_ev = ev.get("url","")
        try:
            start_dt = datetime.strptime(ev.get("start_date",""), "%Y-%m-%d %H:%M:%S")
            end_str  = ev.get("end_date","")
            end_dt   = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S") if end_str else start_dt + timedelta(hours=2)
        except ValueError:
            continue
        result.append({"title":title,"url":url_ev,"start":start_dt,"end":end_dt,"allday":False})
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
            h = int(m.group(4)) if m.group(4) else None
            mi = int(m.group(5)) if m.group(5) else 0

            card = time_el.find_parent(["article","div","li","a"])
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
                href = lh if lh.startswith("http") else BASE_URL + lh

            if h is not None:
                start_dt = datetime(y,mo,d,h,mi)
                end_dt   = start_dt + timedelta(hours=2, minutes=30)
                events.append({"title":title or "(spectacle)","url":href,"start":start_dt,"end":end_dt,"allday":False})
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
        "PRODID:-//tdb-scraper//EN",
        "CALSCALE:GREGORIAN","METHOD:PUBLISH",
        "X-WR-CALNAME:TDB — Théâtre Dijon Bourgogne",
        "X-WR-TIMEZONE:Europe/Paris",
    ]
    for i, ev in enumerate(events):
        if ev.get("allday"):
            sy,sm,sd = ev["start"]
            ey,em,ed = ev["end"]
            dtstart = f"DTSTART;VALUE=DATE:{_fmt_date(sy,sm,sd)}"
            ny,nm,nd = _add_day(ey,em,ed)
            dtend   = f"DTEND;VALUE=DATE:{_fmt_date(ny,nm,nd)}"
            uid_date = f"{sy}{sm:02d}{sd:02d}"
        else:
            s = ev["start"]
            e = ev["end"]
            dtstart = f"DTSTART;TZID=Europe/Paris:{_fmt_dt(s.year,s.month,s.day,s.hour,s.minute)}"
            dtend   = f"DTEND;TZID=Europe/Paris:{_fmt_dt(e.year,e.month,e.day,e.hour,e.minute)}"
            uid_date = s.strftime("%Y%m%d")

        slug = ev.get("url","").rstrip("/").rsplit("/",1)[-1] or f"ev-{i}"
        uid  = f"tdb-{slug}-{uid_date}@tdb-cdn.com"

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_stamp}",
            dtstart, dtend,
            f"SUMMARY:{_esc('[TDB] ' + ev['title'])}",
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
    print("=== TDB — Théâtre Dijon Bourgogne ===", file=sys.stderr)

    events = try_tribe_api(session)
    if events is not None:
        print(f"  → API tribe : {len(events)} événements", file=sys.stderr)
    else:
        print("  → Pas d'API tribe, fallback HTML", file=sys.stderr)
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
