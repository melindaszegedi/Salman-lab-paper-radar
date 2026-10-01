#!/usr/bin/env python3
"""Collect new papers for the Salman Lab Paper Radar.

Sources: PubMed (journal papers, incl. ahead-of-print) and the bioRxiv/medRxiv API (preprints).
Each paper is keyword-scored against config.yaml, flagged Q1 or not, merged into
data/papers.json (an ever-growing archive), and deduplicated by DOI / PMID.

Usage:  python scripts/fetch_papers.py [--days N]
Env:    NCBI_API_KEY (optional, raises PubMed rate limit)
        ANTHROPIC_API_KEY (optional, adds Claude-written relevance notes)
        SUPABASE_URL + SUPABASE_SERVICE_KEY (optional, also collects papers matching the
        keywords and authors people follow in their accounts)
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from personal import author_key, author_matches, fold, keyword_rx  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
ARCHIVE = ROOT / "data" / "papers.json"
ARCHIVE_JS = ROOT / "data" / "papers.js"  # same data as a script, so site/index.html also opens straight from disk
MANUAL = ROOT / "data" / "manual.json"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "salman-paper-radar/1.0 (+https://github.com)"
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(url, params=None, tries=4, post=False):
    for i in range(tries):
        try:
            r = SESSION.post(url, data=params, timeout=60) if post else SESSION.get(url, params=params, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except Exception as e:  # network hiccup: back off and retry
            log(f"  retry {i + 1}/{tries} for {url}: {e}")
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"Giving up on {url}")


# ---------------------------------------------------------------- scoring
TOPICS = {
    tid: {"name": t["name"],
          "terms": [(re.compile(rx, re.I), w) for rx, w in t["terms"]]}
    for tid, t in CFG["topics"].items()
}


def score(text):
    total, topics, hits = 0, [], []
    for tid, t in TOPICS.items():
        sub = 0
        for rx, w in t["terms"]:
            m = rx.search(text)
            if m:
                sub += w
                hits.append(m.group(0))
        if sub >= 3:
            topics.append(tid)
        total += sub
    return total, topics, sorted(set(h.lower() for h in hits))


def relevance_from(s):
    for threshold, rel in CFG["relevance_thresholds"]:
        if s >= threshold:
            return rel
    return 1


def norm_journal(name):
    name = (name or "").lower().split(" : ")[0]
    name = name.replace("&", "and")
    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"^the ", "", name.strip())
    return re.sub(r"[^a-z0-9]+", "", name)


def norm_issn(s):
    s = re.sub(r"[^0-9Xx]", "", s or "").upper()
    return s if len(s) == 8 else None


# Q1 = every SCImago Q1 journal (data/q1_journals.json, built by update_q1.py, matched by ISSN)
# plus the hand list in config.yaml (matched by name).
Q1 = {norm_journal(j) for j in CFG["q1_journals"]}
Q1_ISSN = set()
try:
    _q1 = json.loads((ROOT / "data" / "q1_journals.json").read_text(encoding="utf-8"))
    Q1_ISSN = set(_q1.get("issn", []))
    Q1 |= set(_q1.get("titles", []))
except FileNotFoundError:
    pass


def is_q1(*names, issns=()):
    if any(norm_issn(i) in Q1_ISSN for i in issns if i):
        return True
    return any(norm_journal(n) in Q1 for n in names if n)


def short_authors(names):
    names = [n for n in names if n]
    if len(names) <= 4:
        return ", ".join(names)
    return ", ".join(names[:3]) + ", \u2026 " + names[-1]


def first_sentences(text, limit=320):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut + "\u2026"


# ---------------------------------------------------------------- PubMed
def pubmed_ids(days, term=None, label="PubMed"):
    # Page through results: a 30-day backfill of this broad query can pass 2,000 records.
    ids, start, total = [], 0, None
    while True:
        params = {"db": "pubmed", "term": term or CFG["pubmed_query"], "datetype": "edat", "reldate": days,
                  "retstart": start, "retmax": 1000, "retmode": "json", "tool": "salman-paper-radar",
                  "email": CFG.get("ncbi_email", "")}
        if os.environ.get("NCBI_API_KEY"):
            params["api_key"] = os.environ["NCBI_API_KEY"]
        res = get(EUTILS + "esearch.fcgi", params, post=True).json().get("esearchresult", {})
        if "ERROR" in res:
            raise RuntimeError(f"PubMed search error: {res['ERROR']}")
        total = int(res.get("count", 0) or 0)
        batch = res.get("idlist", [])
        ids += batch
        start += len(batch)
        if not batch or start >= min(total, 9999):  # esearch cannot page past 9,999
            break
        time.sleep(0.4)
    log(f"{label}: {len(ids)} of {total} candidate records in the last {days} days")
    return ids


def pm_date(node):
    if node is None:
        return None
    y, m, d = node.findtext("Year"), node.findtext("Month") or "1", node.findtext("Day") or "1"
    if not y:
        return None
    m = MONTHS.get(m[:3].lower(), m) if not m.isdigit() else m
    try:
        return dt.date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def pubmed_records(ids):
    out = []
    for i in range(0, len(ids), 150):
        batch = ids[i:i + 150]
        params = {"db": "pubmed", "id": ",".join(batch), "retmode": "xml",
                  "tool": "salman-paper-radar", "email": CFG.get("ncbi_email", "")}
        if os.environ.get("NCBI_API_KEY"):
            params["api_key"] = os.environ["NCBI_API_KEY"]
        root = ET.fromstring(get(EUTILS + "efetch.fcgi", params, post=True).content)
        for pa in root.findall("PubmedArticle"):
            try:
                out.append(parse_pubmed(pa))
            except Exception as e:
                log("  skipped one PubMed record:", e)
        time.sleep(0.4)
    return out


def parse_pubmed(pa):
    mc = pa.find("MedlineCitation")
    art = mc.find("Article")
    pmid = mc.findtext("PMID")
    title = "".join(art.find("ArticleTitle").itertext()).strip()
    abstract = " ".join("".join(a.itertext()) for a in art.findall("Abstract/AbstractText"))
    journal = art.findtext("Journal/Title") or ""
    iso = art.findtext("Journal/ISOAbbreviation") or ""
    issns = [art.findtext("Journal/ISSN"), mc.findtext("MedlineJournalInfo/ISSNLinking")]
    authors = []
    for a in art.findall("AuthorList/Author"):
        if a.findtext("CollectiveName"):
            authors.append(a.findtext("CollectiveName"))
        elif a.findtext("LastName"):
            authors.append(f"{a.findtext('LastName')} {a.findtext('Initials') or ''}".strip())
    doi = None
    for aid in pa.findall("PubmedData/ArticleIdList/ArticleId"):
        if aid.get("IdType") == "doi":
            doi = (aid.text or "").strip().lower()
    date = None
    for d in pa.findall("PubmedData/History/PubMedPubDate"):
        if d.get("PubStatus") == "entrez":
            date = pm_date(d)
    date = date or pm_date(art.find("ArticleDate")) or dt.date.today().isoformat()
    ptypes = [p.text for p in art.findall("PublicationTypeList/PublicationType") if p.text]
    preprint = "Preprint" in ptypes or norm_journal(journal) in {"biorxiv", "medrxiv"}
    links = [{"label": "PubMed", "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"}]
    if doi:
        links.insert(0, {"label": "Journal" if not preprint else "Preprint", "url": f"https://doi.org/{doi}"})
    kind = "Review" if any("Review" in p for p in ptypes) else \
        "Clinical trial" if any("Clinical Trial" in p or "Randomized" in p for p in ptypes) else None
    return {
        "id": f"pmid-{pmid}", "pmid": pmid, "doi": doi, "title": title, "abstract": abstract,
        "authors": short_authors(authors), "journal": journal.split(" : ")[0], "journal_iso": iso,
        "date": date, "type": "preprint" if preprint else "journal", "kind": kind,
        "q1": (not preprint) and is_q1(journal, iso, issns=issns), "links": links,
        "au": author_keys(authors),
    }


def author_keys(names, limit=60):
    """Compact author list ('salman|m') so the site and email can match followed authors."""
    out = []
    for n in names[:limit] + names[limit:][-1:]:  # keep the last (senior) author even on huge papers
        k = author_key(n)
        if k and k not in out:
            out.append(k)
    return out


# ---------------------------------------------------------------- followed keywords/authors
def load_follows():
    """Keywords and authors followed in anyone's account (needs the Supabase service key)."""
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return [], []
    headers = {"apikey": key}
    if key.startswith("eyJ"):  # legacy JWT-style keys also go in Authorization
        headers["Authorization"] = f"Bearer {key}"
    try:
        r = SESSION.get(url.rstrip("/") + "/rest/v1/profiles", params={"select": "keywords,authors"},
                        headers=headers, timeout=60)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        log("Could not read followed keywords from Supabase:", e)
        return [], []
    kws, auths = {}, {}
    for row in rows:
        for k in row.get("keywords") or []:
            k = re.sub(r"[\"\[\]()]", " ", k or "").strip()[:60]
            if len(k) >= 2:
                kws.setdefault(fold(k), k)
        for a in row.get("authors") or []:
            ak = author_key(a)
            if ak:
                auths.setdefault(ak, a)
    log(f"Followed in accounts: {len(kws)} keywords, {len(auths)} authors")
    return list(kws.values()), list(auths.keys())


def follow_queries(keywords, authors, chunk=25):
    """PubMed searches for followed terms, a few dozen terms per query."""
    terms = [f'"{k}"[tiab]' for k in keywords]
    for a in authors:
        last, init = a.split("|")
        terms.append(f'"{last} {init}"[au]' if init else f'"{last}"[au]')
    for i in range(0, len(terms), chunk):
        yield "(" + " OR ".join(terms[i:i + chunk]) + ") AND english[lang] NOT (comment[pt] OR erratum[pt])"


# ---------------------------------------------------------------- bioRxiv / medRxiv
def rxiv_records(server, days):
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    out, cursor = [], 0
    while True:
        url = f"https://api.biorxiv.org/details/{server}/{start}/{end}/{cursor}"
        j = get(url).json()
        coll = j.get("collection") or []
        msg = (j.get("messages") or [{}])[0]
        total = int(msg.get("total", 0) or 0)
        for c in coll:
            doi = (c.get("doi") or "").lower()
            authors = [a.strip() for a in (c.get("authors") or "").split(";") if a.strip()]
            keys = author_keys(authors)
            out.append({
                "id": f"{server}-{doi.replace('/', '_')}", "doi": doi, "title": (c.get("title") or "").strip(),
                "abstract": c.get("abstract") or "", "authors": short_authors(authors),
                "journal": "bioRxiv" if server == "biorxiv" else "medRxiv", "date": c.get("date"),
                "type": "preprint", "kind": None, "q1": False, "category": c.get("category"),
                "version": c.get("version"), "au": keys,
                "links": [{"label": "bioRxiv" if server == "biorxiv" else "medRxiv",
                           "url": f"https://www.{server}.org/content/{doi}v{c.get('version') or 1}"}],
            })
        cursor += len(coll)
        if not coll or cursor >= total:
            break
        time.sleep(0.5)
    # keep the latest version of each DOI only
    latest = {}
    for r in out:
        latest[r["doi"]] = r
    log(f"{server}: {len(out)} records, {len(latest)} unique in the last {days} days")
    return list(latest.values())


# ---------------------------------------------------------------- Claude notes (optional)
def claude_notes(papers):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not papers:
        return
    llm = CFG.get("llm", {})
    todo = sorted(papers, key=lambda p: -p["_score"])[: llm.get("max_papers_per_run", 40)]
    for p in todo:
        prompt = (
            f"Lab profile: {llm.get('lab_profile', '')}\n\n"
            f"Paper: {p['title']}\nJournal: {p['journal']}\nAbstract: {p['abstract'][:3500]}\n\n"
            "Reply with JSON only, no other text: {\"relevance\": 1-5 integer for how useful this is to the lab, "
            "\"why\": one or two plain sentences (max 60 words) saying what the paper found and why it matters "
            "for this lab. Do not invent results beyond the abstract.}"
        )
        try:
            r = SESSION.post("https://api.anthropic.com/v1/messages", timeout=90, headers={
                "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": llm.get("model"), "max_tokens": 300,
                      "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            data = json.loads(re.sub(r"```(json)?", "", text).strip())
            p["why"] = str(data.get("why", "")).strip() or p["why"]
            rel = int(data.get("relevance", p["relevance"]))
            p["relevance"] = max(1, min(5, rel))
            p["why_by"] = "claude"
        except Exception as e:
            log("  Claude note skipped:", e)
        time.sleep(0.3)


# ---------------------------------------------------------------- main
def dedupe_key(p):
    return p.get("doi") or p.get("pmid") or p["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=CFG.get("days_back", 3))
    days = ap.parse_args().days

    archive = json.loads(ARCHIVE.read_text(encoding="utf-8"))["papers"] if ARCHIVE.exists() else []
    known = {dedupe_key(p) for p in archive}

    follow_kws, follow_authors = load_follows()
    kw_rx = [(k, keyword_rx(k)) for k in follow_kws]
    kw_rx = [(k, rx) for k, rx in kw_rx if rx]

    candidates = []
    try:
        ids = pubmed_ids(days)
        for q in follow_queries(follow_kws, follow_authors):
            try:
                ids += pubmed_ids(days, term=q, label="PubMed (followed terms)")
            except Exception as e:
                log("Followed-terms search failed:", e)
        candidates += pubmed_records(list(dict.fromkeys(ids)))
    except Exception as e:
        log("PubMed failed:", e)
    for server in CFG.get("preprint_servers", []):
        try:
            candidates += rxiv_records(server, days)
        except Exception as e:
            log(f"{server} failed:", e)

    new = []
    for p in candidates:
        k = dedupe_key(p)
        if not p.get("title") or not p.get("date") or k in known:
            continue
        s, topics, hits = score(f"{p['title']} {p['title']} {p['abstract']}")  # title counts double
        # followed keywords/authors found in this paper (used for personal feeds and emails)
        text = fold(f"{p['title']} {p['abstract']}")
        fk = [k for k, rx in kw_rx if rx.search(text)]
        fa = any(author_matches(f, pa) for pa in p.get("au") or [] for f in follow_authors)
        needed = CFG["min_score"] if (p["q1"] or p["type"] == "preprint") else CFG["min_score_non_q1"]
        on_topic = s >= needed and topics
        followed = fa or (fk and (p["q1"] or p["type"] == "preprint"))
        if not on_topic and not followed:
            continue
        if not on_topic:
            topics = []  # shown only in the personal feeds of people who follow it
        p.update(_score=s, topics=topics, keywords=hits, relevance=relevance_from(s), fk=fk,
                 why=first_sentences(p["abstract"]), addedAt=dt.date.today().isoformat(), source="auto")
        known.add(k)
        new.append(p)

    claude_notes(new)
    for p in new:
        p.pop("_score", None)
        p.pop("abstract", None)  # keep the archive small; the link has the full text
        if not p.get("fk"):
            p.pop("fk", None)
    log(f"Added {len(new)} new papers")

    # merge hand-curated entries (trial updates, news, things the feeds miss)
    manual = json.loads(MANUAL.read_text(encoding="utf-8"))["papers"] if MANUAL.exists() else []
    def tnorm(t):
        return re.sub(r"[^a-z0-9]+", "", (t or "").lower())
    manual_titles = {tnorm(m["title"]) for m in manual}
    by_key = {dedupe_key(p): p for p in archive + new if tnorm(p["title"]) not in manual_titles
              or p.get("source") == "manual"}
    for m in manual:
        by_key[dedupe_key(m)] = m
    papers = sorted(by_key.values(), key=lambda p: (p["date"], p.get("relevance", 0)), reverse=True)

    payload = json.dumps({
        "meta": {"lastCurated": dt.date.today().isoformat(), "lastRunAdded": len(new),
                 "total": len(papers), "topics": {k: v["name"] for k, v in CFG["topics"].items()}},
        "papers": papers}, ensure_ascii=False, indent=0)
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE.write_text(payload, encoding="utf-8")
    ARCHIVE_JS.write_text("window.PAPER_RADAR_DATA = " + payload.replace("</", "<\\/") + ";\n", encoding="utf-8")


if __name__ == "__main__":
    main()
