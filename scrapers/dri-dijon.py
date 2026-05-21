#!/usr/bin/env python3
"""
Scraper Direction des Relations Internationales – Ville de Dijon & Dijon Métropole
Événement phare : "Les Internationales de Dijon" (annuel, automne)
Stratégie :
  1. OpenAgenda Dijon Métropole (ICS direct)
  2. Scrape dijon.fr/toute-lactu-de-dijon/agenda/ avec filtre international
  3. Fallback : événement annuel connu (Les Internationales, octobre)
Sortie : docs/dri-dijon.ics
"""
from __future__ import annotations
import re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

BASE_URL     = "https://www.dijon.fr"
AGENDA_URL   = BASE_URL + "/toute-lactu-de-dijon/agenda/"
# OpenAgenda de Dijon Métropole
OA_ICS_URL   = "https://openagenda.com/agendas/dijon-metropole/events.v2.ics?relative[0]=current&relative[1]=upcoming"
OUTPUT_PATH  = Path(__file__).resolve().parent.parent / "docs" / "dri-dijon.ics"
TIMEOUT      = 30
USER_AGENT   = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION     = "Dijon, Côte-d'Or"

MOIS = {
    "janvier":1,"janv":1,"février":2,"fevrier":2,"mars":3,
    "avril":4,"avr":4,"mai":5,"juin":6,"juillet":7,"juil":7,
    "août":8,"aout":8,"septembre":9,"sept":9,"octobre":10,"oct":10,
    "novembre":11,"nov":11,"décembre":12,"decembre":12,
}

KEYWORDS = ["international","mondial","diplomati","jumelage","coopération","cooperation",
            "kumamoto","solidarité","solidarite","monde","iris"]

def _esc(s): return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")
def _fmt_date(y,m,d): return f"{y:04d}{m:02d}{d:02d}"
def _add_day(y,m,d):
    dt=datetime(y,m,d)+timedelta(days=1); return dt.year,dt.month,dt.day

def try_openagenda(session):
    try:
        r=session.get(OA_ICS_URL,timeout=TIMEOUT)
        if r.status_code in (404,400,403): return None
        r.raise_for_status()
        if b"BEGIN:VCALENDAR" in r.content:
            print("  ✓ Flux OpenAgenda Dijon Métropole",file=sys.stderr)
            return r.content
    except Exception: pass
    return None

def scrape_dijon_fr(session):
    events=[]
    print(f"  GET {AGENDA_URL}",file=sys.stderr)
    try:
        r=session.get(AGENDA_URL,timeout=TIMEOUT); r.raise_for_status()
    except Exception as e:
        print(f"  ⚠️  {e}",file=sys.stderr); return events
    soup=BeautifulSoup(r.text,"html.parser")
    for card in soup.find_all(["article","div","li"],class_=re.compile(r"card|item|event|agenda")):
        text=card.get_text(separator=" ",strip=True)
        # Filtre : garder seulement les événements internationaux
        if not any(kw in text.lower() for kw in KEYWORDS): continue
        # Cherche une date <time> ou texte
        time_el=card.find("time",{"datetime":True})
        date_=None
        if time_el:
            ds=time_el.get("datetime","").strip()
            m=re.match(r"^(\d{4})-(\d{2})-(\d{2})",ds)
            if m: date_=(int(m.group(1)),int(m.group(2)),int(m.group(3)))
        if not date_:
            m=re.search(r"(\d{1,2})\s+(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+(\d{4})",
                        text,re.IGNORECASE)
            if m:
                month=MOIS.get(m.group(2).lower())
                if month: date_=(int(m.group(3)),month,int(m.group(1)))
        if not date_: continue
        title=""
        for tag in card.find_all(re.compile(r"^h[1-6]$")):
            t=tag.get_text(strip=True)
            if t: title=t; break
        url=""
        a=card.find("a",href=True)
        if a:
            href=a.get("href","")
            url=href if href.startswith("http") else BASE_URL+href
        events.append({"title":title or "(événement international)","url":url,
                        "start":date_,"end":date_})
    return events

def fallback_internationales():
    """Les Internationales de Dijon : événement annuel en octobre."""
    now=datetime.now()
    year=now.year if now.month<=10 else now.year+1
    return [{"title":f"Les Internationales de Dijon {year}",
             "url":"https://www.dijon.fr",
             "start":(year,10,1),"end":(year,10,3)}]

def build_ics_from_events(events):
    now_stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//dri-dijon-scraper//EN",
           "CALSCALE:GREGORIAN","METHOD:PUBLISH",
           "X-WR-CALNAME:Relations Internationales Dijon – Agenda",
           "X-WR-TIMEZONE:Europe/Paris"]
    for i,ev in enumerate(events):
        sy,sm,sd_=ev["start"]; ey,em,ed_=ev["end"]
        ny,nm,nd=_add_day(ey,em,ed_)
        uid=f"dri-dijon-{i}-{sy}{sm:02d}{sd_:02d}@dijon.fr"
        lines+=["BEGIN:VEVENT",f"UID:{uid}",f"DTSTAMP:{now_stamp}",
                f"DTSTART;VALUE=DATE:{_fmt_date(sy,sm,sd_)}",
                f"DTEND;VALUE=DATE:{_fmt_date(ny,nm,nd)}",
                f"SUMMARY:{_esc('[DRI Dijon] '+ev['title'])}",
                f"LOCATION:{_esc(LOCATION)}"]
        if ev.get("url"):
            lines.append(f"URL:{ev['url']}")
            lines.append(f"DESCRIPTION:{_esc(ev['url'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return lines

def main():
    session=requests.Session(); session.headers["User-Agent"]=USER_AGENT
    print("=== Direction Relations Internationales – Dijon ===",file=sys.stderr)

    raw_ics=try_openagenda(session)
    if raw_ics:
        OUTPUT_PATH.parent.mkdir(parents=True,exist_ok=True)
        OUTPUT_PATH.write_bytes(raw_ics)
        print(f"  → {OUTPUT_PATH} (ICS direct)",file=sys.stderr)
        return 0

    events=scrape_dijon_fr(session)
    if not events:
        print("  ⚠️  0 événements filtrés — utilisation de l'événement connu",file=sys.stderr)
        events=fallback_internationales()

    print(f"  Événements : {len(events)}",file=sys.stderr)
    lines=build_ics_from_events(events)
    OUTPUT_PATH.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines),encoding="utf-8")
    print(f"  → {OUTPUT_PATH}",file=sys.stderr)
    return 0

if __name__=="__main__": sys.exit(main())
