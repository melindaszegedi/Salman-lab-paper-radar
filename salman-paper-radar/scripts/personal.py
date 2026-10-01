"""Personal matching and ranking, shared by the collector and the email digest.

site/index.html has a JavaScript copy of personal_match() / personal_score() so the
website and the morning email rank papers the same way. Change both together.
"""
import re
import unicodedata

# Weights (keep in sync with PERSONAL in site/index.html)
W_RELEVANCE = 2   # per relevance point (1-5)
W_KEYWORD = 4     # per matched keyword, up to 3
W_TOPIC = 3       # paper is in one of your topics
W_JOURNAL = 4     # paper is in one of your journals
W_AUTHOR = 6      # per followed author on the paper, up to 2


def fold(s):
    """Lowercase and strip accents: 'Müller' -> 'muller'."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def norm_journal(name):
    name = fold(name).split(" : ")[0].replace("&", "and")
    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"^the ", "", name.strip())
    return re.sub(r"[^a-z0-9]+", "", name)


def author_key(name):
    """Any common author spelling -> 'lastname|firstinitial'.

    'Salman MM', 'Salman, M. M.', 'Mootaz M. Salman' and 'salman m' all give 'salman|m'.
    """
    name = re.sub(r"\s+", " ", (name or "").replace(".", " ")).strip()
    if not name:
        return None
    if "," in name:
        last, first = [x.strip() for x in name.split(",", 1)]
    else:
        parts = name.split(" ")
        if len(parts) > 1 and re.fullmatch(r"[A-Z]{1,3}", parts[-1]):   # 'Salman MM'
            last, first = " ".join(parts[:-1]), parts[-1]
        elif len(parts) > 1:                                            # 'Mootaz Salman'
            last, first = parts[-1], " ".join(parts[:-1])
        else:
            last, first = parts[0], ""
    last = re.sub(r"[^a-z' -]", "", fold(last)).strip()
    if not last:
        return None
    init = fold(first)[:1]
    return f"{last}|{init}"


def author_matches(followed, paper_key):
    """A followed author with no initial matches any first initial."""
    if not followed or not paper_key:
        return False
    fl, fi = followed.split("|")
    pl, pi = paper_key.split("|")
    return fl == pl and (not fi or not pi or fi == pi)


def keyword_rx(kw):
    kw = fold(kw)
    if not kw:
        return None
    # word boundaries only where the keyword starts/ends with a letter or digit
    left = r"(?<![a-z0-9])" if re.match(r"[a-z0-9]", kw) else ""
    right = r"(?![a-z0-9])" if re.search(r"[a-z0-9]$", kw) else ""
    return re.compile(left + re.escape(kw) + right)


def paper_text(p):
    return fold(" ".join([p.get("title") or "", p.get("why") or "",
                          " ".join(p.get("keywords") or []), " ".join(p.get("fk") or [])]))


def personal_match(p, prefs):
    text = paper_text(p)
    kws = [k for k in prefs.get("keywords") or [] if k and (rx := keyword_rx(k)) and rx.search(text)]
    topics = set(prefs.get("topics") or [])
    topic = bool(topics & set(p.get("topics") or []))
    pj = norm_journal(p.get("journal"))
    journal = bool(pj) and any(norm_journal(j) == pj for j in prefs.get("journals") or [])
    paper_authors = p.get("au") or [author_key(a) for a in re.split(r",\s*", p.get("authors") or "")]
    authors = []
    tracked = [t.get("name") for t in prefs.get("tracked_authors") or [] if isinstance(t, dict) and t.get("name")]
    for a in list(prefs.get("authors") or []) + tracked:
        k = author_key(a)
        if k and any(author_matches(k, pa) for pa in paper_authors if pa):
            authors.append(a)
    return {"keywords": kws, "topic": topic, "journal": journal, "authors": authors,
            "any": bool(kws or topic or journal or authors)}


def has_prefs(prefs):
    return any(prefs.get(k) for k in ("keywords", "topics", "journals", "authors", "tracked_authors"))


def personal_score(p, m):
    return (W_RELEVANCE * (p.get("relevance") or 0) + W_KEYWORD * min(3, len(m["keywords"]))
            + (W_TOPIC if m["topic"] else 0) + (W_JOURNAL if m["journal"] else 0)
            + W_AUTHOR * min(2, len(m["authors"])))


def rank_for(papers, prefs):
    """Papers that match this person, best first. With no preferences set, everything by relevance."""
    out = []
    for p in papers:
        m = personal_match(p, prefs)
        if has_prefs(prefs) and not m["any"]:
            continue
        out.append((personal_score(p, m), p.get("date") or "", p, m))
    out.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [(p, m) for _, _, p, m in out]
