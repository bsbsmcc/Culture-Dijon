#!/usr/bin/env python3
"""
Scraper Zutique Productions (Dijon)
Drupal 7 site — agenda à /fr/agenda
Dates en texte français : "Vendredi 3 Avril 2026 à 18h30 - Dijon"
Sortie : docs/zutique.ics
"""
from __future__ import annotations
import re, sys, unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://www.zutique.com"
AGENDA_URL  = BASE_URL + "/fr/agenda"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "zutique.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "Dijon, Côte-d'Or"

MOIS = {
    "janvier":1,"janv":1,"jan":1,
    "février":2,"fevrier":2,"fév":2,"fev":2,
    "mars":3,
    "avril":4,"avr":4,
    "mai":5,
    "juin":6,
    "juillet":7,"juil":7,
    "août":8,"aout":8,
    "septembre":9,"sept":9,
    "octobre":10,"oct":10,
    "novembre":11,"nov":11,
    "décembre":12,"decembre":12,"déc":12,"dec":12,
}

def _norm(s): return unicodedata.normalize("NFD",s.lower()).encode("ascii","ignore").decode()
def _esc(s): return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")
def _fmt_date(y,m,d): return f"{y:04d}{m:02d}{d:02d}"
def _fmt_dt(y,mo,d,h,mi): return f"{y:04d}{mo:02d}{d:02d}T{h:02d}{mi:02d}00"
def _add_day(y,m,d):
    dt = datetime(y,m,d)+timedelta(days=1); return dt.year,dt.month,dt.day

def _parse_french_date(text):
    t = _norm(text)
    m = re.search(r"(\d{1,2})\s+([a-zéûùàâêîôè]+)\s+(\d{4})(?:\s+a\s+(\d{1,2})h(\d{0,2}))?",t)
    if not m: return None
    day=int(m.group(1)); mon_s=_norm(m.group(2)); year=int(m.group(3))
    month=MOIS.get(mon_s)
    if not month: return None
    hour=int(m.group(4)) if m.group(4) else None
    minute=int(m.group(5)) if m.group(5) else 0
    return year,month,day,hour,minute

def scrape(session):
    print(f"  GET {AGENDA_URL}",file=sys.stderr)
    r=session.get(AGENDA_URL,timeout=TIMEOUT); r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    events=[]
    # Drupal 7 : blocs views-row, ou éléments article
    candidates = soup.find_all(["article","div"],class_=re.compile(r"views-row|event|evenement|agenda-item"))
    if not candidates:
        # Fallback : cherche les balises <time> directement
        candidates = [el.find_parent(["article","div","li"]) or el for el in soup.find_all("time",{"datetime":True})]
    for card in candidates:
        if card is None: continue
        text=card.get_text(separator=" ",strip=True)
        time_el=card.find("time",{"datetime":True})
        dt_parsed=None
        if time_el:
            ds=time_el.get("datetime","").strip()
            m2=re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?",ds)
            if m2:
                dt_parsed=(int(m2.group(1)),int(m2.group(2)),int(m2.group(3)),
                           int(m2.group(4)) if m2.group(4) else None,
                           int(m2.group(5)) if m2.group(5) else 0)
        if not dt_parsed: dt_parsed=_parse_french_date(text)
        if not dt_parsed: continue
        yr,mo_,d_,h_,mi_=dt_parsed
        title=""
        for tag in card.find_all(re.compile(r"^h[1-6]$")):
            t=tag.get_text(strip=True)
            if t: title=t; break
        if not title:
            a=card.find("a")
            if a: title=a.get_text(strip=True)
        if not title:
            title=re.sub(r"\b\d{1,2}\s+\w+\s+\d{4}.*","",text).strip()[:80]
        url=""
        link_el=card.find("a",href=True)
        if link_el:
            href=link_el.get("href","")
            url=href if href.startswith("http") else BASE_URL+href
        events.append({"title":title or "(événement)","url":url,
                        "year":yr,"month":mo_,"day":d_,"hour":h_,"minute":mi_ or 0})
    seen=set(); unique=[]
    for ev in events:
        key=(ev["title"],ev["year"],ev["month"],ev["day"])
        if key not in seen: seen.add(key); unique.append(ev)
    return unique

def build_ics(events):
    now_stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//zutique-scraper//EN",
           "CALSCALE:GREGORIAN","METHOD:PUBLISH",
           "X-WR-CALNAME:Zutique Productions – Agenda","X-WR-TIMEZONE:Europe/Paris"]
    for i,ev in enumerate(events):
        uid=f"zutique-{i}-{ev['year']}{ev['month']:02d}{ev['day']:02d}@zutique.com"
        if ev["hour"] is None:
            ny,nm,nd=_add_day(ev["year"],ev["month"],ev["day"])
            dtstart=f"DTSTART;VALUE=DATE:{_fmt_date(ev['year'],ev['month'],ev['day'])}"
            dtend=f"DTEND;VALUE=DATE:{_fmt_date(ny,nm,nd)}"
        else:
            end_h=(ev["hour"]+2)%24
            dtstart=f"DTSTART;TZID=Europe/Paris:{_fmt_dt(ev['year'],ev['month'],ev['day'],ev['hour'],ev['minute'])}"
            dtend=f"DTEND;TZID=Europe/Paris:{_fmt_dt(ev['year'],ev['month'],ev['day'],end_h,ev['minute'])}"
        lines+=["BEGIN:VEVENT",f"UID:{uid}",f"DTSTAMP:{now_stamp}",
                dtstart,dtend,f"SUMMARY:{_esc('[Zutique] '+ev['title'])}",
                f"LOCATION:{_esc(LOCATION)}"]
        if ev["url"]:
            lines.append(f"URL:{ev['url']}")
            lines.append(f"DESCRIPTION:{_esc(ev['url'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return lines

def main():
    session=requests.Session(); session.headers["User-Agent"]=USER_AGENT
    print("=== Zutique Productions ===",file=sys.stderr)
    events=scrape(session)
    print(f"  Événements : {len(events)}",file=sys.stderr)
    lines=build_ics(events)
    OUTPUT_PATH.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines),encoding="utf-8")
    print(f"  → {OUTPUT_PATH}",file=sys.stderr)
    return 0

if __name__=="__main__": sys.exit(main())
