#!/usr/bin/env python3
"""
Scraper Hautes Côtes (https://www.hautescotes.com/en/art)
Publication culturelle franco-anglaise dédiée aux paysages viticoles et culturels
de Bourgogne. Site React/Next.js — JS-heavy.
Stratégie :
  1. Cherche un flux ICS ou JSON-LD Event dans le HTML initial (SSR Next.js)
  2. Cherche __NEXT_DATA__ (JSON embarqué Next.js) pour extraire des articles/events
  3. Fallback : scrape le HTML statique pour des dates et titres
Sortie : docs/hautes-cotes.ics
⚠️  Maintenance : si JS est indispensable, envisager un vrai headless browser
    ou remplacer par l'URL d'un flux ICS si le site en publie un.
"""
from __future__ import annotations
import json, re, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://www.hautescotes.com"
ART_URL     = BASE_URL + "/en/art"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "hautes-cotes.ics"
TIMEOUT     = 30
USER_AGENT  = "Mozilla/5.0 (compatible; ICS-Aggregator/1.0)"
LOCATION    = "Bourgogne – Hautes Côtes de Nuits/Beaune"

def _esc(s): return (s or "").replace("\\","\\\\").replace(",","\\,").replace(";","\\;").replace("\n","\\n")
def _fmt_date(y,m,d): return f"{y:04d}{m:02d}{d:02d}"
def _add_day(y,m,d):
    dt=datetime(y,m,d)+timedelta(days=1); return dt.year,dt.month,dt.day

def try_nextjs_data(soup):
    """Extrait __NEXT_DATA__ depuis le HTML de Next.js."""
    tag=soup.find("script",{"id":"__NEXT_DATA__"})
    if not tag or not tag.string: return []
    try: data=json.loads(tag.string)
    except: return []
    events=[]
    # Parcourt récursivement pour trouver des objets avec startDate / date
    def walk(obj,depth=0):
        if depth>10: return
        if isinstance(obj,dict):
            if obj.get("@type","") in ("Event","ExhibitionEvent"):
                name=obj.get("name","")
                start_s=obj.get("startDate",""); end_s=obj.get("endDate","")
                if name and start_s:
                    try:
                        sd=datetime.fromisoformat(start_s[:10])
                        ed=datetime.fromisoformat(end_s[:10]) if end_s else sd
                        events.append({"title":name,"url":obj.get("url",""),
                                        "start":(sd.year,sd.month,sd.day),
                                        "end":(ed.year,ed.month,ed.day)})
                    except: pass
            elif "date" in obj and "title" in obj:
                try:
                    d_=datetime.fromisoformat(str(obj["date"])[:10])
                    events.append({"title":str(obj["title"]),"url":obj.get("slug",""),
                                   "start":(d_.year,d_.month,d_.day),
                                   "end":(d_.year,d_.month,d_.day)})
                except: pass
            for v in obj.values(): walk(v,depth+1)
        elif isinstance(obj,list):
            for item in obj: walk(item,depth+1)
    walk(data)
    return events

def try_jsonld(soup):
    events=[]
    for script in soup.find_all("script",{"type":"application/ld+json"}):
        try: data=json.loads(script.string or "")
        except: continue
        items=data if isinstance(data,list) else [data]
        for item in items:
            if item.get("@type","") not in ("Event","ExhibitionEvent","Article"): continue
            name=item.get("name","") or item.get("headline","")
            start_s=item.get("startDate","") or item.get("datePublished","")
            end_s=item.get("endDate","")
            if not name or not start_s: continue
            try:
                sd=datetime.fromisoformat(start_s[:10])
                ed=datetime.fromisoformat(end_s[:10]) if end_s else sd
                events.append({"title":name,"url":item.get("url",""),
                                "start":(sd.year,sd.month,sd.day),
                                "end":(ed.year,ed.month,ed.day)})
            except: continue
    return events

def try_html_time(soup):
    events=[]
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
        events.append({"title":title or "(article)","url":"",
                        "start":(y,mo,d),"end":(y,mo,d)})
    return events

def scrape(session):
    print(f"  GET {ART_URL}",file=sys.stderr)
    try:
        r=session.get(ART_URL,timeout=TIMEOUT); r.raise_for_status()
    except Exception as e:
        print(f"  ⚠️  {e}",file=sys.stderr); return []
    soup=BeautifulSoup(r.text,"html.parser")

    evs=try_nextjs_data(soup)
    if evs: return evs
    evs=try_jsonld(soup)
    if evs: return evs
    evs=try_html_time(soup)
    if evs: return evs

    print("  ⚠️  Site JS-only, HTML statique vide — 0 événements extraits",file=sys.stderr)
    print("  💡  Envisager un ICS manuel ou un headless browser",file=sys.stderr)
    return []

def build_ics(events):
    now_stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//hautes-cotes-scraper//EN",
           "CALSCALE:GREGORIAN","METHOD:PUBLISH",
           "X-WR-CALNAME:Hautes Côtes – Agenda art","X-WR-TIMEZONE:Europe/Paris"]
    for i,ev in enumerate(events):
        sy,sm,sd_=ev["start"]; ey,em,ed_=ev["end"]
        ny,nm,nd=_add_day(ey,em,ed_)
        uid=f"hautes-cotes-{i}-{sy}{sm:02d}{sd_:02d}@hautescotes.com"
        url_=ev.get("url","")
        full_url=url_ if url_.startswith("http") else (BASE_URL+"/"+url_.lstrip("/") if url_ else "")
        lines+=["BEGIN:VEVENT",f"UID:{uid}",f"DTSTAMP:{now_stamp}",
                f"DTSTART;VALUE=DATE:{_fmt_date(sy,sm,sd_)}",
                f"DTEND;VALUE=DATE:{_fmt_date(ny,nm,nd)}",
                f"SUMMARY:{_esc('[Hautes Côtes] '+ev['title'])}",
                f"LOCATION:{_esc(LOCATION)}"]
        if full_url:
            lines.append(f"URL:{full_url}")
            lines.append(f"DESCRIPTION:{_esc(full_url)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return lines

def main():
    session=requests.Session(); session.headers["User-Agent"]=USER_AGENT
    print("=== Hautes Côtes ===",file=sys.stderr)
    events=scrape(session)
    print(f"  Événements : {len(events)}",file=sys.stderr)
    lines=build_ics(events)
    OUTPUT_PATH.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines),encoding="utf-8")
    print(f"  → {OUTPUT_PATH}",file=sys.stderr)
    return 0

if __name__=="__main__": sys.exit(main())
