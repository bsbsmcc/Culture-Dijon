#!/usr/bin/env python3
"""
Scraper La Karrière / Vill'Art (Villars-Fontaine, près Dijon)
Site : villart.fr/actu/
Lieu d'art contemporain dans une ancienne carrière.
Site HTML statique minimaliste — cherche des dates et titres dans le contenu.
Fallback : extrait les articles/sections avec Schema.org ou texte daté.
Sortie : docs/la-karriere.ics
"""
from __future__ import annotations
import json, re, sys, unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://villart.fr"
PROG_URLS   = [BASE_URL+"/actu/", BASE_URL+"/programmation/", BASE_URL+"/"]
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "la-karriere.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "La Karrière – RD 35, 21700 Villars-Fontaine"

MOIS = {
    "janvier":1,"janv":1,"février":2,"fevrier":2,"mars":3,
    "avril":4,"avr":4,"mai":5,"juin":6,"juillet":7,"juil":7,
    "août":8,"aout":8,"septembre":9,"sept":9,"octobre":10,"oct":10,
    "novembre":11,"nov":11,"décembre":12,"decembre":12,
}

def _norm(s): return unicodedata.normalize("NFD",s.lower()).encode("ascii","ignore").decode()
def _esc(s): return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")
def _fmt_date(y,m,d): return f"{y:04d}{m:02d}{d:02d}"
def _add_day(y,m,d):
    dt=datetime(y,m,d)+timedelta(days=1); return dt.year,dt.month,dt.day

def _parse_date_fr(text):
    t=_norm(text)
    # "du DD mois au DD mois YYYY" → prend la date de début
    m=re.search(r"du\s+(\d{1,2})\s+([a-z]+)(?:\s+au\s+\d+\s+[a-z]+)?\s+(\d{4})",t)
    if m:
        day=int(m.group(1)); mon_s=m.group(2); year=int(m.group(3))
        month=MOIS.get(mon_s)
        if month: return year,month,day
    m=re.search(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})",t)
    if m:
        day=int(m.group(1)); mon_s=m.group(2); year=int(m.group(3))
        month=MOIS.get(mon_s)
        if month: return year,month,day
    return None

def scrape(session):
    events=[]
    for url in PROG_URLS:
        try:
            print(f"  GET {url}",file=sys.stderr)
            r=session.get(url,timeout=TIMEOUT)
            if r.status_code==404: continue
            r.raise_for_status()
        except Exception as e:
            print(f"  ⚠️  {url}: {e}",file=sys.stderr); continue
        soup=BeautifulSoup(r.text,"html.parser")

        # JSON-LD
        for script in soup.find_all("script",{"type":"application/ld+json"}):
            try: data=json.loads(script.string or "")
            except: continue
            items=data if isinstance(data,list) else [data]
            for item in items:
                if item.get("@type","") not in ("Event","ExhibitionEvent","VisualArtsEvent"): continue
                name=item.get("name","(exposition)")
                start_s=item.get("startDate",""); end_s=item.get("endDate","")
                if not start_s: continue
                try:
                    sd=datetime.fromisoformat(start_s[:10])
                    ed=datetime.fromisoformat(end_s[:10]) if end_s else sd
                    events.append({"title":name,"url":item.get("url",""),
                                   "start":(sd.year,sd.month,sd.day),"end":(ed.year,ed.month,ed.day)})
                except: continue

        # Balises <time>
        for time_el in soup.find_all("time",{"datetime":True}):
            ds=time_el.get("datetime","").strip()
            m=re.match(r"^(\d{4})-(\d{2})-(\d{2})",ds)
            if not m: continue
            y,mo,d=int(m.group(1)),int(m.group(2)),int(m.group(3))
            card=time_el.find_parent(["article","section","div"])
            title=""
            if card:
                for tag in card.find_all(re.compile(r"^h[1-6]$")):
                    t=tag.get_text(strip=True)
                    if t: title=t; break
            events.append({"title":title or "(événement)","url":"",
                           "start":(y,mo,d),"end":(y,mo,d)})

        # Texte libre : cherche des sections avec titre + date française
        for section in soup.find_all(["section","article","div"],
                                     class_=re.compile(r"event|prog|actu|news|block")):
            text=section.get_text(separator=" ",strip=True)
            date_=_parse_date_fr(text)
            if not date_: continue
            title=""
            for tag in section.find_all(re.compile(r"^h[1-6]$")):
                t=tag.get_text(strip=True)
                if t: title=t; break
            if not title: title=text[:60].strip()
            url=""
            a=section.find("a",href=True)
            if a:
                href=a.get("href","")
                url=href if href.startswith("http") else BASE_URL+href
            events.append({"title":title,"url":url,
                           "start":date_,"end":date_})

        if events: break

    # Déduplique
    seen=set(); unique=[]
    for ev in events:
        key=(ev["title"],ev["start"])
        if key not in seen: seen.add(key); unique.append(ev)
    return unique

def build_ics(events):
    now_stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//la-karriere-scraper//EN",
           "CALSCALE:GREGORIAN","METHOD:PUBLISH",
           "X-WR-CALNAME:La Karrière – Agenda","X-WR-TIMEZONE:Europe/Paris"]
    for i,ev in enumerate(events):
        sy,sm,sd_=ev["start"]; ey,em,ed_=ev["end"]
        ny,nm,nd=_add_day(ey,em,ed_)
        uid=f"karriere-{i}-{sy}{sm:02d}{sd_:02d}@lakarriere.fr"
        lines+=["BEGIN:VEVENT",f"UID:{uid}",f"DTSTAMP:{now_stamp}",
                f"DTSTART;VALUE=DATE:{_fmt_date(sy,sm,sd_)}",
                f"DTEND;VALUE=DATE:{_fmt_date(ny,nm,nd)}",
                f"SUMMARY:{_esc('[La Karrière] '+ev['title'])}",
                f"LOCATION:{_esc(LOCATION)}"]
        if ev.get("url"):
            lines.append(f"URL:{ev['url']}")
            lines.append(f"DESCRIPTION:{_esc(ev['url'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return lines

def main():
    session=requests.Session(); session.headers["User-Agent"]=USER_AGENT
    print("=== La Karrière (Vill'Art) ===",file=sys.stderr)
    events=scrape(session)
    print(f"  Événements : {len(events)}",file=sys.stderr)
    if not events:
        print("  ⚠️  0 événements — site peut être fermé ou JS requis",file=sys.stderr)
    lines=build_ics(events)
    OUTPUT_PATH.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines),encoding="utf-8")
    print(f"  → {OUTPUT_PATH}",file=sys.stderr)
    return 0

if __name__=="__main__": sys.exit(main())
