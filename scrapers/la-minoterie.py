#!/usr/bin/env python3
"""
Scraper La Minoterie (Dijon)
=============================

La Minoterie est une scène dédiée à la danse contemporaine.
Site correct : laminoterie-jeunepublic.fr (Drupal)

Lance simplement :
    python scrapers/la-minoterie.py

Sortie : docs/la-minoterie.ics
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL     = "https://www.laminoterie-jeunepublic.fr"
PROG_URLS   = [BASE_URL + "/agenda", BASE_URL + "/programmation", BASE_URL]
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "la-minoterie.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "La Minoterie — 12 Rue Jules Mercier, 21000 Dijon"

MOIS_FR = {
    "jan":1,"janv":1,"janvier":1,
    "fév":2,"fev":2,"févr":2,"fevr":2,"février":2,
    "mar":3,"mars":3,
    "avr":4,"avril":4,
    "mai":5,
    "jun":6,"juin":6,
    "jul":7,"juil":7,"juillet":7,
    "aoû":8,"aou":8,"août":8,"aout":8,
    "sep":9,"sept":9,"septembre":9,
    "oct":10,"octobre":10,
    "nov":11,"novembre":11,
    "déc":12,"dec":12,"décembre":12,
}


def _esc(s):
    return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")

def _fmt_date(y,m,d):
    return f"{y:04d}{m:02d}{d:02d}"

def _fmt_dt(y,mo,d,h,mi):
    return f"{y:04d}{mo:02d}{d:02d}T{h:02d}{mi:02d}00"

def _add_day(y,m,d):
    dt = datetime(y,m,d)+timedelta(days=1)
    return dt.year,dt.month,dt.day


def _parse_fr_abbr(text, ref_year):
    """Parse dates like '01 oct.' or '15 novembre 2025'."""
    text = text.lower().strip().rstrip(".")
    m = re.match(r"(\d{1,2})\s+([a-zûéèà]+)\.?\s*(\d{4})?", text)
    if not m:
        return None
    day = int(m.group(1))
    mo_s = m.group(2).rstrip(".")
    year = int(m.group(3)) if m.group(3) else None
    month = MOIS_FR.get(mo_s)
    if not month:
        return None
    if year is None:
        now = datetime.now()
        if month < now.month or (month == now.month and day < now.day):
            year = now.year + 1
        else:
            year = now.year
    return (year, month, day)


def scrape_drupal(session):
    """Scrape the Drupal agenda at laminoterie-jeunepublic.fr."""
    for url in PROG_URLS:
        print(f"  GET {url}", file=sys.stderr)
        try:
            r = session.get(url, timeout=TIMEOUT)
            if r.status_code == 404:
                continue
            r.raise_for_status()
        except Exception as exc:
            print(f"  ⚠️  {exc}", file=sys.stderr)
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        events = []
        ref_year = datetime.now().year

        # Drupal agenda rows
        for row in soup.select(".agenda-list__row, .view-row, article.node--type-evenement"):
            title_el = row.select_one("h2, h3, .titre-24, .field--name-title")
            title = title_el.get_text(strip=True) if title_el else ""
            date_el = row.select_one(".text-narrow-24, .date-display-single, time, .field--name-field-date")
            date_text = date_el.get_text(strip=True) if date_el else ""
            if date_el and date_el.name == "time" and date_el.get("datetime"):
                m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?", date_el["datetime"])
                if m:
                    y,mo,d = int(m.group(1)),int(m.group(2)),int(m.group(3))
                    h = int(m.group(4)) if m.group(4) else None
                    mi_ = int(m.group(5)) if m.group(5) else 0
                    link_el = row.select_one("a[href*='/evenement/'], a[href]")
                    href = ""
                    if link_el:
                        lh = link_el.get("href","")
                        href = lh if lh.startswith("http") else BASE_URL+lh
                    if h is not None:
                        sd = datetime(y,mo,d,h,mi_)
                        events.append({"title":title or "(spectacle)","url":href,
                                       "start":sd,"end":sd+timedelta(hours=2),"allday":False})
                    else:
                        events.append({"title":title or "(spectacle)","url":href,
                                       "start":(y,mo,d),"end":(y,mo,d),"allday":True})
                    continue
            parsed = _parse_fr_abbr(date_text, ref_year)
            if not parsed or not title:
                continue
            link_el = row.select_one("a[href*='/evenement/'], a[href]")
            href = ""
            if link_el:
                lh = link_el.get("href","")
                href = lh if lh.startswith("http") else BASE_URL+lh
            events.append({"title":title,"url":href,"start":parsed,"end":parsed,"allday":True})

        if events:
            print(f"  → Drupal rows : {len(events)} événements", file=sys.stderr)
            return events

        # Generic <time datetime>
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
                        title = t; break
            link_el = card.find("a", href=True) if card else None
            href = (lambda lh: lh if lh.startswith("http") else BASE_URL+lh)(
                link_el.get("href","") if link_el else "")
            if h is not None:
                sd = datetime(y,mo,d,h,mi_)
                events.append({"title":title or "(spectacle)","url":href,
                               "start":sd,"end":sd+timedelta(hours=2),"allday":False})
            else:
                events.append({"title":title or "(spectacle)","url":href,
                               "start":(y,mo,d),"end":(y,mo,d),"allday":True})
        if events:
            return events
    return []


def build_ics(events):
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
            "BEGIN:VEVENT",f"UID:{uid}",f"DTSTAMP:{now_stamp}",
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


def main():
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    print("=== La Minoterie (laminoterie-jeunepublic.fr) ===", file=sys.stderr)
    events = scrape_drupal(session)
    if not events:
        print("  ⚠️  0 événements — vérifier sélecteurs Drupal", file=sys.stderr)
    lines = build_ics(events or [])
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines), encoding="utf-8")
    print(f"  → {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
