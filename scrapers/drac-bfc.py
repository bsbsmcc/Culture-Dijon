#!/usr/bin/env python3
"""
Scraper DRAC Bourgogne-Franche-Comté
Site : culture.gouv.fr/regions/drac-bourgogne-franche-comte/actualite-a-la-une
Stratégie :
  1. OpenAgenda ICS si disponible (DRAC BFC y publie des agendas)
  2. Scrape de la page actualités avec dates dans les meta/article
Sortie : docs/drac-bfc.ics
"""
from __future__ import annotations
import re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://www.culture.gouv.fr"
ACTU_URL    = BASE_URL + "/regions/drac-bourgogne-franche-comte/actualite-a-la-une"
# OpenAgenda : le DRAC BFC a un agenda sur OpenAgenda
OA_ICS_URL  = "https://openagenda.com/agendas/drac-bourgogne-franche-comte/events.v2.ics?relative[0]=current&relative[1]=upcoming"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "drac-bfc.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "DRAC BFC – 39 rue Vannerie, 21000 Dijon"

MOIS = {
    "janvier":1,"janv":1,"février":2,"fevrier":2,"mars":3,
    "avril":4,"avr":4,"mai":5,"juin":6,"juillet":7,"juil":7,
    "août":8,"aout":8,"septembre":9,"sept":9,"octobre":10,"oct":10,
    "novembre":11,"nov":11,"décembre":12,"decembre":12,
}

def _esc(s): return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")
def _fmt_date(y,m,d): return f"{y:04d}{m:02d}{d:02d}"
def _add_day(y,m,d):
    dt=datetime(y,m,d)+timedelta(days=1); return dt.year,dt.month,dt.day

def try_openagenda(session):
    """Tente de récupérer un ICS OpenAgenda pour la DRAC BFC."""
    try:
        r=session.get(OA_ICS_URL,timeout=TIMEOUT)
        if r.status_code in (404,400,403): return None
        r.raise_for_status()
        if b"BEGIN:VCALENDAR" in r.content:
            print(f"  ✓ Flux ICS OpenAgenda trouvé",file=sys.stderr)
            return r.content
    except Exception:
        pass
    return None

def scrape_html(session):
    events=[]
    print(f"  GET {ACTU_URL}",file=sys.stderr)
    try:
        r=session.get(ACTU_URL,timeout=TIMEOUT); r.raise_for_status()
    except Exception as e:
        print(f"  ⚠️  {e}",file=sys.stderr); return events
    soup=BeautifulSoup(r.text,"html.parser")

    # Les articles de actualite-a-la-une ont un meta "Publié le DD mois YYYY"
    for card in soup.find_all(["article","div","li"],
                               class_=re.compile(r"card|item|article|result|news")):
        text=card.get_text(separator=" ",strip=True)
        # Cherche "Publié le DD mois YYYY" ou "DD mois YYYY"
        m=re.search(r"(?:Publié le\s+)?(\d{1,2})\s+(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+(\d{4})",
                    text,re.IGNORECASE)
        if not m: continue
        day=int(m.group(1))
        month=MOIS.get(m.group(2).lower(),MOIS.get(m.group(2).replace("é","e").replace("û","u").lower()))
        year=int(m.group(3))
        if not month: continue
        title=""
        for tag in card.find_all(re.compile(r"^h[1-6]$")):
            t=tag.get_text(strip=True)
            if t: title=t; break
        if not title:
            a=card.find("a"); 
            if a: title=a.get_text(strip=True)
        url=""
        link_el=card.find("a",href=True)
        if link_el:
            href=link_el.get("href","")
            url=href if href.startswith("http") else BASE_URL+href
        events.append({"title":title or "(actualité DRAC)","url":url,
                        "start":(year,month,day),"end":(year,month,day)})

    seen=set(); unique=[]
    for ev in events:
        key=(ev["title"],ev["start"])
        if key not in seen: seen.add(key); unique.append(ev)
    return unique

def build_ics_from_events(events):
    now_stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//drac-bfc-scraper//EN",
           "CALSCALE:GREGORIAN","METHOD:PUBLISH",
           "X-WR-CALNAME:DRAC Bourgogne-Franche-Comté – Agenda",
           "X-WR-TIMEZONE:Europe/Paris"]
    for i,ev in enumerate(events):
        sy,sm,sd_=ev["start"]; ey,em,ed_=ev["end"]
        ny,nm,nd=_add_day(ey,em,ed_)
        uid=f"drac-bfc-{i}-{sy}{sm:02d}{sd_:02d}@culture.gouv.fr"
        lines+=["BEGIN:VEVENT",f"UID:{uid}",f"DTSTAMP:{now_stamp}",
                f"DTSTART;VALUE=DATE:{_fmt_date(sy,sm,sd_)}",
                f"DTEND;VALUE=DATE:{_fmt_date(ny,nm,nd)}",
                f"SUMMARY:{_esc('[DRAC BFC] '+ev['title'])}",
                f"LOCATION:{_esc(LOCATION)}"]
        if ev.get("url"):
            lines.append(f"URL:{ev['url']}")
            lines.append(f"DESCRIPTION:{_esc(ev['url'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return lines

def main():
    session=requests.Session(); session.headers["User-Agent"]=USER_AGENT
    print("=== DRAC Bourgogne-Franche-Comté ===",file=sys.stderr)

    raw_ics=try_openagenda(session)
    if raw_ics:
        OUTPUT_PATH.parent.mkdir(parents=True,exist_ok=True)
        OUTPUT_PATH.write_bytes(raw_ics)
        print(f"  → {OUTPUT_PATH} (ICS direct)",file=sys.stderr)
        return 0

    events=scrape_html(session)
    print(f"  Événements : {len(events)}",file=sys.stderr)
    lines=build_ics_from_events(events)
    OUTPUT_PATH.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines),encoding="utf-8")
    print(f"  → {OUTPUT_PATH}",file=sys.stderr)
    return 0

if __name__=="__main__": sys.exit(main())
