#!/usr/bin/env python3
"""
Scraper Golden Coast Festival (Dijon)
Festival de rap annuel, fin août, à Corcelles-les-Monts (près Dijon).
Site : goldencoastfestival.com
Stratégie :
  1. Schema.org JSON-LD (type Event)
  2. Balises <time datetime>
  3. Fallback : édition connue hard-codée (3 jours fin août)
Sortie : docs/golden-coast.ics
"""
from __future__ import annotations
import json, re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://goldencoastfestival.com"
PROG_URLS   = [BASE_URL+"/", BASE_URL+"/programme", BASE_URL+"/billetterie"]
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "golden-coast.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "Parc de la Combe à la Serpent, Corcelles-les-Monts, 21160"

def _esc(s): return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")
def _fmt_date(y,m,d): return f"{y:04d}{m:02d}{d:02d}"
def _add_day(y,m,d):
    dt=datetime(y,m,d)+timedelta(days=1); return dt.year,dt.month,dt.day

def try_jsonld(soup):
    events=[]
    for script in soup.find_all("script",{"type":"application/ld+json"}):
        try: data=json.loads(script.string or "")
        except: continue
        items=data if isinstance(data,list) else [data]
        for item in items:
            if item.get("@type","") not in ("Event","MusicEvent","Festival"): continue
            name=item.get("name","Golden Coast Festival")
            start_s=item.get("startDate",""); end_s=item.get("endDate","")
            if not start_s: continue
            try:
                sd=datetime.fromisoformat(start_s[:10])
                ed=datetime.fromisoformat(end_s[:10]) if end_s else sd
                events.append({"title":name,"start":(sd.year,sd.month,sd.day),
                                "end":(ed.year,ed.month,ed.day),"url":item.get("url","")})
            except: continue
    return events

def try_time_tags(soup):
    events=[]
    for time_el in soup.find_all("time",{"datetime":True}):
        ds=time_el.get("datetime","").strip()
        m=re.match(r"^(\d{4})-(\d{2})-(\d{2})",ds)
        if not m: continue
        y,mo,d=int(m.group(1)),int(m.group(2)),int(m.group(3))
        card=time_el.find_parent(["article","div","li"])
        title=""
        if card:
            for tag in card.find_all(re.compile(r"^h[1-6]$")):
                t=tag.get_text(strip=True)
                if t: title=t; break
        events.append({"title":title or "Golden Coast Festival",
                        "start":(y,mo,d),"end":(y,mo,d),"url":""})
    return events

def fallback_hardcoded():
    """Édition 2026 connue : 28-30 août."""
    now=datetime.now()
    year=now.year if now.month<=8 else now.year+1
    return [{"title":f"Golden Coast Festival {year}",
             "start":(year,8,28),"end":(year,8,30),"url":BASE_URL}]

def scrape(session):
    for url in PROG_URLS:
        try:
            print(f"  GET {url}",file=sys.stderr)
            r=session.get(url,timeout=TIMEOUT,allow_redirects=True)
            if r.status_code==404: continue
            r.raise_for_status()
        except Exception as e:
            print(f"  ⚠️  {e}",file=sys.stderr); continue
        soup=BeautifulSoup(r.text,"html.parser")
        evs=try_jsonld(soup)
        if evs: return evs
        evs=try_time_tags(soup)
        if evs: return evs
    print("  ⚠️  Impossible de scraper — utilisation de la date connue",file=sys.stderr)
    return fallback_hardcoded()

def build_ics(events):
    now_stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//golden-coast-scraper//EN",
           "CALSCALE:GREGORIAN","METHOD:PUBLISH",
           "X-WR-CALNAME:Golden Coast Festival – Agenda","X-WR-TIMEZONE:Europe/Paris"]
    for i,ev in enumerate(events):
        sy,sm,sd_=ev["start"]; ey,em,ed_=ev["end"]
        ny,nm,nd=_add_day(ey,em,ed_)
        uid=f"golden-coast-{sy}{sm:02d}{sd_:02d}@goldencoastfestival.com"
        lines+=["BEGIN:VEVENT",f"UID:{uid}",f"DTSTAMP:{now_stamp}",
                f"DTSTART;VALUE=DATE:{_fmt_date(sy,sm,sd_)}",
                f"DTEND;VALUE=DATE:{_fmt_date(ny,nm,nd)}",
                f"SUMMARY:{_esc('[Golden Coast] '+ev['title'])}",
                f"LOCATION:{_esc(LOCATION)}"]
        if ev.get("url"):
            lines.append(f"URL:{ev['url']}")
            lines.append(f"DESCRIPTION:{_esc(ev['url'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return lines

def main():
    session=requests.Session(); session.headers["User-Agent"]=USER_AGENT
    print("=== Golden Coast Festival ===",file=sys.stderr)
    events=scrape(session)
    print(f"  Événements : {len(events)}",file=sys.stderr)
    lines=build_ics(events)
    OUTPUT_PATH.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines),encoding="utf-8")
    print(f"  → {OUTPUT_PATH}",file=sys.stderr)
    return 0

if __name__=="__main__": sys.exit(main())
