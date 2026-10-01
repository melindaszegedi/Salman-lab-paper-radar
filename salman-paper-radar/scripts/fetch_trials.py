#!/usr/bin/env python3
"""Collect recently updated clinical trials from ClinicalTrials.gov into data/trials.json.

The searches are the `trials.groups` in config.yaml. Each run replaces the file (the registry
itself is the archive); trials keep a `firstSeen` date so the site can mark new ones.
"""
import datetime as dt
import json
import sys
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
OUT = ROOT / "data" / "trials.json"
API = "https://clinicaltrials.gov/api/v2/studies"
FIELDS = ("NCTId,BriefTitle,OverallStatus,Phase,Condition,InterventionName,InterventionType,LeadSponsorName,"
          "StartDate,PrimaryCompletionDate,LastUpdatePostDate,StudyFirstPostDate,EnrollmentCount,StudyType")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(params, tries=4):
    for i in range(tries):
        try:
            r = requests.get(API, params=params, timeout=60, headers={"User-Agent": "salman-paper-radar/1.0"})
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log(f"  retry {i + 1}/{tries}: {e}")
            time.sleep(3 * (i + 1))
    raise RuntimeError("ClinicalTrials.gov did not answer")


def parse(study):
    ps = study.get("protocolSection") or {}
    ident, status = ps.get("identificationModule") or {}, ps.get("statusModule") or {}
    design = ps.get("designModule") or {}
    nct = ident.get("nctId")
    if not nct:
        return None
    d = lambda k: ((status.get(k) or {}).get("date")) or None
    return {
        "id": nct,
        "title": ident.get("briefTitle") or "",
        "status": status.get("overallStatus") or "",
        "phases": design.get("phases") or [],
        "type": design.get("studyType") or "",
        "enrollment": ((design.get("enrollmentInfo") or {}).get("count")),
        "conditions": ((ps.get("conditionsModule") or {}).get("conditions") or [])[:6],
        "interventions": [i.get("name") for i in ((ps.get("armsInterventionsModule") or {}).get("interventions") or [])
                          if i.get("name") and (i.get("name") or "").lower() != "placebo"][:6],
        "sponsor": (((ps.get("sponsorCollaboratorsModule") or {}).get("leadSponsor")) or {}).get("name") or "",
        "start": d("startDateStruct"),
        "primaryCompletion": d("primaryCompletionDateStruct"),
        "firstPosted": d("studyFirstPostDateStruct"),
        "lastUpdate": d("lastUpdatePostDateStruct"),
        "url": f"https://clinicaltrials.gov/study/{nct}",
    }


def main():
    tc = CFG.get("trials") or {}
    groups = tc.get("groups") or []
    if not groups:
        log("No trial searches configured")
        return
    since = (dt.date.today() - dt.timedelta(days=int(tc.get("days", 365)))).isoformat()
    cap = int(tc.get("max_per_group", 300))
    try:
        old = {t["id"]: t for t in json.loads(OUT.read_text(encoding="utf-8")).get("trials", [])}
    except Exception:
        old = {}
    trials, ok = {}, 0
    for g in groups:
        params = {"pageSize": 100, "sort": "LastUpdatePostDate:desc", "fields": FIELDS, "format": "json"}
        for k, p in (("cond", "query.cond"), ("intr", "query.intr"), ("term", "query.term")):
            if g.get(k):
                params[p] = g[k]
        flt = f"AREA[LastUpdatePostDate]RANGE[{since},MAX]"
        params["filter.advanced"] = f"{g['filter']} AND {flt}" if g.get("filter") else flt
        n, token = 0, None
        try:
            while n < cap:
                if token:
                    params["pageToken"] = token
                j = get(params)
                for s in j.get("studies") or []:
                    t = parse(s)
                    if not t:
                        continue
                    n += 1
                    if t["id"] in trials:
                        trials[t["id"]]["groups"].append(g["id"])
                    else:
                        t["groups"] = [g["id"]]
                        t["firstSeen"] = (old.get(t["id"]) or {}).get("firstSeen") or dt.date.today().isoformat()
                        trials[t["id"]] = t
                    if n >= cap:
                        break
                token = j.get("nextPageToken")
                if not token:
                    break
                time.sleep(0.4)
            ok += 1
            log(f"Trials · {g['name']}: {n}")
        except Exception as e:
            log(f"Trials · {g['name']} failed: {e}")
    if not ok:
        log("No trial search worked; keeping the previous file")
        return
    out = sorted(trials.values(), key=lambda t: t.get("lastUpdate") or "", reverse=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "meta": {"updated": dt.date.today().isoformat(), "since": since, "total": len(out),
                 "groups": [{"id": g["id"], "name": g["name"]} for g in groups]},
        "trials": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"Saved {len(out)} trials")


if __name__ == "__main__":
    main()
