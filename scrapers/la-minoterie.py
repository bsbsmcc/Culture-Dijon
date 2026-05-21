#!/usr/bin/env python3
"""
Scraper La Minoterie (Dijon)
=============================

La Minoterie est une scène dédiée à la danse contemporaine.
Le site (laminoterie.com) tourne probablement sous WordPress.

Stratégies dans l'ordre :
  1. API REST WordPress wp/v2/evenement ou tribe/events/v1
     → si The Events Calendar est installé
  2. API wp/v2/posts avec ACF (champs date personnalisés)
  3. Fallback HTML BeautifulSoup sur /saison ou /programmation

Lance simplement :
    python scrapers/la-minoterie.py

Sortie : docs/la-minoterie.ics

⚠️  Maintenance : si 0 événements, inspecter
    https://www.laminoterie.com/wp-json/wp/v2/ dans le navigateur
    pour lister les types de posts disponibles.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL     = "https://www.laminoterie.com"
# APIs à tenter dans l'ordre
WP_APIS = [
    BASE_URL + "/wp-json/tribe/events/v1/events?per_page=50&page={page}",
    BASE_URL + "/wp-json/wp/v2/evenement?per_page=100&_fields=id,title,slug,link,acf&page={page}",
    BASE_URL + "/wp-json/wp/v2/spectacle?per_page=100&_fields=id,title,slug,link,acf&page={page}",
]
PROG_URLS   = [BASE_URL + "/saison", BASE_URL + "/programmation", BASE_URL + "/agenda"]
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "la-minoterie.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "La Minoterie — 2 Rue Gagnereaux, 21000 Dijon"


def _esc(s: str) -> str:
    return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")

def _fmt_date(y:int,m:int,d:int) -> str:
    return f"{y:04d}{m:02d}{d:02d}"

def _fmt_dt(y:int,mo:int,d:int,h:int,mi:int) -> str:
    return f"{y:04d}{mo:02d}{d:02d}T{h:02d}{mi:02d}00"

def _add_day(y:int,m:int,d:int) -> tuple[int,int,int]:
    dt = datetime(y,m,d)+timedelta(days=1)
    return dt.year,dt.month,dt.day


def try_tribe_api(session: requests.Session, url_tpl: str) -> list[dict] | None:
    url = url_tpl.format(page=1)
    try:
        r = session.get(url, timeout=TIMEOUT)
        if r.status_code in (404,400,401):
            return None
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    # tribe/events/v1 → {"events": [...], "total_pages": n}
    if isinstance(data, dict) and "events" in data:
        events_raw = data["events"]
        for page in range(2, data.get("total_pages",1)+1):
            r2 = session.get(url_tpl.format(page=page), timeout=TIMEOUT)
            if r2.ok:
                events_raw.extend(r2.json().get("events",[]))
        result = []
        for ev in events_raw:
            title = ev.get("title","(sans titre)")
            url_ev = ev.get("url","")
            try:
                sd = datetime.strptime(ev.get("start_date",""), "%Y-%m-%d %H:%M:%S")
                ed_s = ev.get("end_date","")
                ed = datetime.strptime(ed_s, "%Y-%m-%d %H:%M:%S") if ed_s else sd+timedelta(hours=2)
            except ValueError:
                continue
            result.append({"title":title,"url":url_ev,"start":sd,"end":ed,"allday":False})
        return result

    # wp/v2/evenement ou spectacle → liste de posts avec ACF
    if isinstance(data, list):
        result = []
        for ev in data:
            title = (ev.get("title") or {}).get("rendered","(sans titre)")
            url_ev = ev.get("link","")
            acf = ev.get("acf") or {}
            # Cherche les champs date habituels dans ACF
            date_str = acf.get("date") or acf.get("date_debut") or acf.get("start_date","")
            if not date_str:
                continue
            try:
                if len(date_str) == 8:  # YYYYMMDD
                    sd = datetime.strptime(date_str, "%Y%m%d")
                elif "T" in date_str:
                    sd = datetime.fromisoformat(date_str[:16])
                else:
                    sd = datetime.strptime(date_str[:10], "%Y-%m-%d")
            except ValueError:
                continue
            hour_str = acf.get("hour","")
            if hour_str:
                try:
                    parts = hour_str.split(":")
                    h, mi = int(parts[0]), int(parts[1])
                    sd = sd.replace(hour=h, minute=mi)
                    ed = sd + timedelta(hours=2)
                    result.append({"title":title,"url":url_ev,"start":sd,"end":ed,"allday":False})
                except Exception:
                    result.append({"title":title,"url":url_ev,
                                   "start":(sd.year,sd.month,sd.day),
                                   "end":(sd.year,sd.month,sd.day),"allday":True})
            else:
                result.append({"title":title,"url":url_ev,
                               "start":(sd.year,sd.month,sd.day),
                               "end":(sd.year,sd.month,sd.day),"allday":True})
        return result if result else None

    return None


def try_html(session: requests.Session) -> list[dict]:
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
        events = []

        for time_el in soup.find_all("time", {"datetime": True}):
            dt_str = time_el.get("datetime","").strip()
            m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?", dt_str)
            if not m:
                continue
            y,mo,d = int(m.group(1)),int(m.group(2)),int(m.group(3))
            h = int(m.group(4)) if m.group(4) else None
            mi_ = int(m.group(5)) if m.group(5) else 0

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
                href = lh if lh.startswith("http") else BASE_URL + lh

            if h is not None:
                sd = datetime(y,mo,d,h,mi_)
                ed = sd + timedelta(hours=2)
                events.append({"title":title or "(spectacle)","url":href,"start":sd,"end":ed,"allday":False})
            else:
                events.append({"title":title or "(spectacle)","url":href,
                               "start":(y,mo,d),"end":(y,mo,d),"allday":True})

        if events:
            return events

    return []


def build_ics(events: list[dict]) -> list[str]:
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR","VERSION:2.0",
        "PRODID:-//la-minoterie-scraper//EN",
        "CALSCALE:GREGORIAN","METHOD:PUBLISH",
        "X-WR-CALNAME:La Minoterie — Saison danse",
        "X-WR-TIMEZONE:Europe/Paris",
    ]
    for i, ev in enumerate(events):
        if ev.get("allday"):
            sy,sm,sd_ = ev["start"]
            ey,em,ed_ = ev["end"]
            dtstart = f"DTSTART;VALUE=DATE:{_fmt_date(sy,sm,sd_)}"
            ny,nm,nd_ = _add_day(ey,em,ed_)
            dtend   = f"DTEND;VALUE=DATE:{_fmt_date(ny,nm,nd_)}"
            uid_d   = f"{sy}{sm:02d}{sd_:02d}"
        else:
            s,e = ev["start"],ev["end"]
            dtstart = f"DTSTART;TZID=Europe/Paris:{_fmt_dt(s.year,s.month,s.day,s.hour,s.minute)}"
            dtend   = f"DTEND;TZID=Europe/Paris:{_fmt_dt(e.year,e.month,e.day,e.hour,e.minute)}"
            uid_d   = s.strftime("%Y%m%d")

        slug = ev.get("url","").rstrip("/").rsplit("/",1)[-1] or f"ev-{i}"
        uid  = f"minoterie-{slug}-{uid_d}@laminoterie.com"

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_stamp}",
            dtstart,dtend,
            f"SUMMARY:{_esc('[Minoterie] ' + ev['title'])}",
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
    print("=== La Minoterie ===", file=sys.stderr)

    events = None
    for api_tpl in WP_APIS:
        events = try_tribe_api(session, api_tpl)
        if events is not None:
            print(f"  → API WordPress ({api_tpl.split('/wp-json/')[1].split('?')[0]}) : {len(events)} événements", file=sys.stderr)
            break

    if events is None:
        print("  → Pas d'API WP disponible, fallback HTML", file=sys.stderr)
        events = try_html(session)
        print(f"  → HTML : {len(events)} événements", file=sys.stderr)

    if not events:
        print("  ⚠️  0 événements — vérifier /wp-json/wp/v2/ pour lister les types", file=sys.stderr)

    lines = build_ics(events or [])
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines), encoding="utf-8")
    print(f"  → {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
