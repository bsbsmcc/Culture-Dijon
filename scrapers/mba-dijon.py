#!/usr/bin/env python3
"""
Scraper Dijon Musées — MBA & associés
======================================

Site correct : musees.dijon.fr (WordPress, CPT 'event')
API WP : /wp-json/wp/v2/event

Lance simplement :
    python scrapers/mba-dijon.py

Sortie : docs/mba-dijon.ics
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL     = "https://musees.dijon.fr"
WP_EVENT_API = BASE_URL + "/wp-json/wp/v2/event?per_page=50&_fields=id,title,date,link,acf&orderby=date&order=asc"
PROG_URLS    = [BASE_URL + "/agenda/", BASE_URL + "/agenda", BASE_URL + "/evenements"]
OUTPUT_PATH  = Path(__file__).resolve().parent.parent / "docs" / "mba-dijon.ics"
TIMEOUT      = 30
USER_AGENT   = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION     = "Musée des Beaux-Arts de Dijon — 1 Rue Rameau, 21000 Dijon"

MOIS = {
    "janvier":1,"janv":1,"février":2,"fevrier":2,"fév":2,"mars":3,
    "avril":4,"avr":4,"mai":5,"juin":6,"juillet":7,"juil":7,
    "août":8,"aout":8,"septembre":9,"sept":9,"octobre":10,"oct":10,
    "novembre":11,"nov":11,"décembre":12,"decembre":12,"déc":12,
}


def _esc(s):
    return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")

def _fmt_date(y,m,d):
    return f"{y:04d}{m:02d}{d:02d}"

def _add_day(y,m,d):
    dt = datetime(y,m,d)+timedelta(days=1)
    return dt.year,dt.month,dt.day


def try_wp_event_api(session):
    """WordPress custom post type 'event' — confirmed at musees.dijon.fr."""
    try:
        r = session.get(WP_EVENT_API, timeout=TIMEOUT)
        if r.status_code in (404, 400, 401, 403):
            return None
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    if not isinstance(data, list) or not data:
        return None

    now = datetime.now()
    events = []
    for post in data:
        title = (post.get("title") or {}).get("rendered", "(événement)")
        url_ev = post.get("link", "")
        date_s = post.get("date", "")
        acf = post.get("acf") or {}

        start_acf = (acf.get("date_debut") or acf.get("date") or
                     acf.get("start_date") or acf.get("event_date") or "")
        end_acf   = (acf.get("date_fin") or acf.get("end_date") or "")

        try:
            if start_acf:
                sd = datetime.fromisoformat(start_acf[:16]) if "T" in start_acf else datetime.strptime(start_acf[:10], "%Y-%m-%d")
            else:
                sd = datetime.fromisoformat(date_s[:16])
        except (ValueError, TypeError):
            continue

        if sd < now - timedelta(days=1):
            continue

        try:
            ed = datetime.fromisoformat(end_acf[:10]) if end_acf else sd
        except ValueError:
            ed = sd

        events.append({
            "title": title,
            "url": url_ev,
            "start": (sd.year, sd.month, sd.day),
            "end": (ed.year, ed.month, ed.day),
        })

    return events or None


def scrape_html(session):
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
                    events.append({"title":title,"url":item.get("url",""),
                                   "start":(sd.year,sd.month,sd.day),"end":(ed.year,ed.month,ed.day)})
                except ValueError:
                    continue

        if events:
            break

        # <time datetime>
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
                        title = t; break
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


def build_ics_from_events(events):
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
        uid  = f"mba-{slug}-{sy}{sm:02d}{sd_:02d}@musees.dijon.fr"
        ny,nm,nd_ = _add_day(ey,em,ed_)
        lines += [
            "BEGIN:VEVENT",f"UID:{uid}",f"DTSTAMP:{now_stamp}",
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


def main():
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    print("=== Dijon Musées (musees.dijon.fr) ===", file=sys.stderr)

    events = try_wp_event_api(session)
    if events is not None:
        print(f"  → WP API /event : {len(events)} événements", file=sys.stderr)
    else:
        print("  → WP API inaccessible, fallback HTML", file=sys.stderr)
        events = scrape_html(session)
        print(f"  → HTML : {len(events)} événements", file=sys.stderr)

    if not events:
        print("  ⚠️  0 événements — vérifier WP API ou sélecteurs HTML", file=sys.stderr)

    lines = build_ics_from_events(events or [])
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines), encoding="utf-8")
    print(f"  → {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
