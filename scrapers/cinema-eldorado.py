#!/usr/bin/env python3
"""
Scraper Cinéma Eldorado (Dijon)
================================

Site correct : cinemaeldorado.com (WordPress)
API WP REST : /wp-json/wp/v2/posts

Lance simplement :
    python scrapers/cinema-eldorado.py

Sortie : docs/cinema-eldorado.ics
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://cinemaeldorado.com"
WP_API      = BASE_URL + "/wp-json/wp/v2/posts?per_page=50&_fields=id,title,date,link,status&status=publish&orderby=date&order=desc"
PROG_URLS   = [BASE_URL + "/les-films-2/a-laffiche/", BASE_URL + "/programmation", BASE_URL + "/agenda"]
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "cinema-eldorado.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "Cinéma Eldorado — 21 Rue Alfred de Musset, 21000 Dijon"


def _esc(s):
    return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")

def _fmt_date(y,m,d):
    return f"{y:04d}{m:02d}{d:02d}"

def _add_day(y,m,d):
    dt = datetime(y,m,d)+timedelta(days=1)
    return dt.year,dt.month,dt.day


def try_wp_api(session):
    """Get films via WordPress REST API (posts published recently = currently showing)."""
    try:
        r = session.get(WP_API, timeout=TIMEOUT)
        if r.status_code in (404, 400, 401, 403):
            return None
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    if not isinstance(data, list) or not data:
        return None

    now = datetime.now()
    cutoff = now - timedelta(days=30)
    events = []
    for post in data:
        title = (post.get("title") or {}).get("rendered", "(film)")
        url_ev = post.get("link", "")
        date_s = post.get("date", "")
        if not date_s:
            continue
        try:
            pub = datetime.fromisoformat(date_s[:16])
        except ValueError:
            continue
        if pub < cutoff:
            continue
        end = pub + timedelta(days=14)
        events.append({
            "title": title,
            "url": url_ev,
            "start": (pub.year, pub.month, pub.day),
            "end": (end.year, end.month, end.day),
            "allday": True,
        })
    return events or None


def try_schema_org(session):
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
                        ed = datetime.fromisoformat(end_s[:16]) if end_s and "T" in end_s else sd+timedelta(hours=2)
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


def try_html(session):
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
                        title = t; break
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


def build_ics(events):
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
            dtstart = f"DTSTART;TZID=Europe/Paris:{s.year:04d}{s.month:02d}{s.day:02d}T{s.hour:02d}{s.minute:02d}00"
            dtend   = f"DTEND;TZID=Europe/Paris:{e.year:04d}{e.month:02d}{e.day:02d}T{e.hour:02d}{e.minute:02d}00"
            uid_d   = s.strftime("%Y%m%d%H%M")
        slug = ev.get("url","").rstrip("/").rsplit("/",1)[-1] or f"ev-{i}"
        uid  = f"eldorado-{slug}-{uid_d}@cinemaeldorado.com"
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


def main():
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    print("=== Cinéma Eldorado (cinemaeldorado.com) ===", file=sys.stderr)

    events = try_wp_api(session)
    if events:
        print(f"  → WP API posts : {len(events)} films", file=sys.stderr)
    else:
        print("  → WP API vide, fallback Schema.org", file=sys.stderr)
        events = try_schema_org(session)
        if events:
            print(f"  → Schema.org : {len(events)} événements", file=sys.stderr)
        else:
            print("  → Fallback HTML", file=sys.stderr)
            events = try_html(session)
            print(f"  → HTML : {len(events)} événements", file=sys.stderr)

    if not events:
        print("  ⚠️  0 événements — vérifier WP API", file=sys.stderr)

    lines = build_ics(events or [])
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines), encoding="utf-8")
    print(f"  → {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
