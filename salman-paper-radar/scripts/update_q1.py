#!/usr/bin/env python3
"""Build data/q1_journals.json: every journal ranked Q1 by SCImago (SJR), across all fields.

SCImago publishes one ranked list of ~29,000 journals with each journal's best quartile.
We keep the ~8,000 whose best quartile is Q1 and match them to papers by ISSN, which is far
more reliable than matching journal names. The list is refreshed at most once a month; if the
download fails, the previous list (and the hand list in config.yaml) keeps working.

Usage:  python scripts/update_q1.py [--force]
"""
import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "q1_journals.json"
URL = "https://www.scimagojr.com/journalrank.php"
MAX_AGE_DAYS = 30


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def norm_issn(s):
    s = re.sub(r"[^0-9Xx]", "", s or "").upper()
    return s if len(s) == 8 else None


def norm_title(name):
    name = (name or "").lower().split(" : ")[0].replace("&", "and")
    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"^the ", "", name.strip())
    return re.sub(r"[^a-z0-9]+", "", name)


def parse(text):
    rows = csv.DictReader(io.StringIO(text), delimiter=";")
    cols = rows.fieldnames or []
    q_col = next((c for c in cols if "quartile" in c.lower()), None)
    if not q_col or "Issn" not in cols or "Title" not in cols:
        raise ValueError(f"unexpected SCImago columns: {cols[:12]}")
    issns, titles, n = set(), set(), 0
    for r in rows:
        if (r.get(q_col) or "").strip().upper() != "Q1":
            continue
        n += 1
        for part in (r.get("Issn") or "").split(","):
            i = norm_issn(part)
            if i:
                issns.add(i)
        t = norm_title(r.get("Title"))
        if t:
            titles.add(t)
    if n < 1000:  # the full list has thousands; fewer means we got an error page
        raise ValueError(f"only {n} Q1 journals parsed")
    return n, sorted(issns), sorted(titles)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    force = ap.parse_args().force
    if OUT.exists() and not force:
        try:
            age = (dt.date.today() - dt.date.fromisoformat(json.loads(OUT.read_text())["updated"])).days
            if age < MAX_AGE_DAYS:
                log(f"Q1 list is {age} days old; keeping it")
                return
        except Exception:
            pass
    try:
        r = requests.get(URL, params={"out": "xls", "type": "j"}, timeout=120, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/126.0 Safari/537.36 salman-paper-radar",
            "Accept": "text/csv,application/octet-stream,*/*"})
        r.raise_for_status()
        text = r.content.decode("utf-8-sig", errors="replace")
        n, issns, titles = parse(text)
    except Exception as e:
        log(f"Could not refresh the SCImago Q1 list ({e}); keeping the previous one")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "SCImago Journal Rank, best quartile Q1 in any subject category (all fields)",
        "updated": dt.date.today().isoformat(), "journals": n, "issn": issns, "titles": titles,
    }, separators=(",", ":")), encoding="utf-8")
    log(f"Saved {n} Q1 journals ({len(issns)} ISSNs)")


if __name__ == "__main__":
    main()
