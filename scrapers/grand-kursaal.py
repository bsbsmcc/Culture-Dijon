#!/usr/bin/env python3
"""
Grand Kursaal — DÉSACTIVÉ
==========================

Le Grand Kursaal est à BESANÇON, pas Dijon.
Son domaine grandkursaal.com ne résout pas (NXDOMAIN).
Ce scraper génère un ICS vide pour ne pas bloquer le workflow.

Pour retirer complètement : supprimer l'entrée grand-kursaal dans calendars.yaml.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "grand-kursaal.ics"


def main():
    print("=== Grand Kursaal ===", file=sys.stderr)
    print("  ⚠️  Le Grand Kursaal est à Besançon, domaine grandkursaal.com inexistant.", file=sys.stderr)
    print("  → ICS vide généré (0 événements)", file=sys.stderr)
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//grand-kursaal-scraper//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:Grand Kursaal",
        "END:VCALENDAR",
    ]
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\r\n".join(lines), encoding="utf-8")
    print(f"  → {OUTPUT_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
