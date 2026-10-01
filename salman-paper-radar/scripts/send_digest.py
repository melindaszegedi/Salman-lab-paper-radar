#!/usr/bin/env python3
"""Morning email: each account gets its most relevant new papers at about 08:00 UK time.

GitHub Actions runs this twice every morning (06:58 and 07:58 UTC) because UK time moves
between GMT and BST. The script sends only once it is 07:55 or later in London, and each
account at most once per day (tracked in profiles.last_digest_on), so the earlier run does
the sending in summer and the later one in winter.

Usage:  python scripts/send_digest.py [--force] [--dry-run] [--only you@example.com]
Env:    SUPABASE_URL, SUPABASE_SERVICE_KEY, RESEND_API_KEY, DIGEST_FROM, SITE_URL
"""
import argparse
import datetime as dt
import html
import json
import os
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from personal import has_prefs, rank_for  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LONDON = ZoneInfo("Europe/London")
SEND_FROM = dt.time(7, 55)     # earliest London time to send
SEND_UNTIL = dt.time(14, 0)    # a badly delayed run after this skips the day rather than mailing at teatime
TOPIC_NAMES = {}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def env(name, required=True):
    v = (os.environ.get(name) or "").strip()
    if required and not v:
        sys.exit(f"Missing {name}. Add it under Settings -> Secrets and variables -> Actions.")
    return v


def sb_headers(key):
    h = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):  # legacy JWT-style service_role key
        h["Authorization"] = f"Bearer {key}"
    return h


def greeting(now):
    h = now.hour
    return "Good morning" if 5 <= h < 12 else "Good afternoon" if h < 18 else "Good evening"


def nice_date(s):
    try:
        d = dt.date.fromisoformat(s)
        return f"{d.day} {d:%b %Y}"
    except Exception:
        return s or ""


def esc(s):
    return html.escape(s or "", quote=True)


def paper_html(p, m):
    link = next((l["url"] for l in p.get("links") or [] if str(l.get("url", "")).startswith("http")), None)
    title = f'<a href="{esc(link)}" style="color:#4B3A8C;text-decoration:none">{esc(p["title"])}</a>' if link else esc(p["title"])
    if p.get("type") == "preprint":
        badge = ("Preprint", "#2A7F6A")
    elif p.get("type") in ("trial", "news"):
        badge = ("Trial update" if p["type"] == "trial" else "News", "#8B5A2B")
    else:
        badge = ("Q1", "#4B3A8C") if p.get("q1") else ("Journal", "#6B7280")
    why_matched = m["keywords"] + m["authors"] + (["your journal"] if m["journal"] else [])
    matched = (f'<div style="font-size:12px;color:#C9476C;margin-top:6px">Matches: {esc(", ".join(why_matched))}</div>'
               if why_matched else "")
    dots = "".join('<span style="display:inline-block;width:12px;height:4px;border-radius:2px;margin-right:2px;'
                   f'background:{"#C9476C" if i <= (p.get("relevance") or 0) else "#DDD5E6"}"></span>' for i in range(1, 6))
    return f'''
<tr><td style="padding:16px 0;border-bottom:1px solid #E7E1EE">
  <div style="font-size:12px;color:#6B6380;margin-bottom:4px">
    <span style="display:inline-block;border:1px solid {badge[1]};color:{badge[1]};border-radius:99px;padding:0 7px;font-weight:600">{badge[0]}</span>
    &nbsp;{esc(p.get("journal") if p.get("type") != "preprint" else "")} &middot; {esc(nice_date(p.get("date")))}
  </div>
  <div style="font-family:Georgia,'Times New Roman',serif;font-size:18px;line-height:1.3;font-weight:600;margin:2px 0 4px">{title}</div>
  <div style="font-size:13px;color:#6B6380;margin-bottom:6px">{esc(p.get("authors"))}</div>
  <div style="font-size:14px;line-height:1.5;color:#231B30">{esc(p.get("why"))}</div>
  <div style="margin-top:8px">{dots}</div>{matched}
</td></tr>'''


def email_html(first, now, items, site_url):
    hello = f"{greeting(now)}, {esc(first)}" if first else greeting(now)
    rows = "".join(paper_html(p, m) for p, m in items)
    settings = f"{site_url.rstrip('/')}/#settings" if site_url else ""
    open_site = (f'<a href="{esc(site_url)}" style="display:inline-block;background:#4B3A8C;color:#fff;text-decoration:none;'
                 f'padding:10px 16px;border-radius:8px;font-size:14px;margin-top:18px">Open your feed</a>') if site_url else ""
    manage = (f'<a href="{esc(settings)}" style="color:#6B6380">change your topics or turn these emails off</a>'
              if settings else "change your topics or turn these emails off in your account")
    return f'''<!doctype html><html><body style="margin:0;background:#F6F3F7;font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F6F3F7"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:#ffffff;border-radius:12px;overflow:hidden">
<tr><td style="background:#4B3A8C;color:#ffffff;padding:18px 24px;font-family:Georgia,'Times New Roman',serif;font-size:17px;letter-spacing:.02em">
  Salman Lab Paper Radar
</td></tr>
<tr><td style="padding:22px 24px 8px">
  <div style="font-family:Georgia,'Times New Roman',serif;font-size:26px;color:#4B3A8C">{hello}</div>
  <div style="font-size:14px;color:#6B6380;margin-top:4px">Here {"is the paper" if len(items) == 1 else f"are the {len(items)} papers"} most relevant to you that came in since your last email.</div>
</td></tr>
<tr><td style="padding:0 24px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>{open_site}</td></tr>
<tr><td style="padding:22px 24px;font-size:12px;color:#6B6380">
  Summaries are drawn from abstracts; check the original before citing. You get this because you have an account on the Salman Lab Paper Radar &mdash; {manage}.
</td></tr>
</table></td></tr></table></body></html>'''


def email_text(first, now, items, site_url):
    lines = [f"{greeting(now)}{', ' + first if first else ''}", "", "Your most relevant new papers:", ""]
    for p, m in items:
        link = next((l["url"] for l in p.get("links") or []), "")
        lines += [f"- {p['title']}", f"  {p.get('journal', '')} · {p.get('date', '')}", f"  {link}", ""]
    if site_url:
        lines += [f"Your feed: {site_url}", f"Preferences / unsubscribe: {site_url.rstrip('/')}/#settings"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="ignore the 08:00 window and today's already-sent marks")
    ap.add_argument("--dry-run", action="store_true", help="build emails but do not send; writes them to _digest_preview/")
    ap.add_argument("--only", help="send only to this email address")
    a = ap.parse_args()

    now = dt.datetime.now(LONDON)
    today = now.date()
    if not a.force and not (SEND_FROM <= now.time() <= SEND_UNTIL):
        log(f"It is {now:%H:%M} in London; the digest goes out from {SEND_FROM:%H:%M}. Nothing to do.")
        return

    sb_url, sb_key = env("SUPABASE_URL").rstrip("/"), env("SUPABASE_SERVICE_KEY")
    resend_key = env("RESEND_API_KEY", required=not a.dry_run)
    sender = env("DIGEST_FROM", required=False) or "Paper Radar <onboarding@resend.dev>"
    site_url = env("SITE_URL", required=False)

    data = json.loads((ROOT / "data" / "papers.json").read_text(encoding="utf-8"))
    papers = data["papers"]
    TOPIC_NAMES.update((data.get("meta") or {}).get("topics") or {})

    r = requests.get(f"{sb_url}/rest/v1/profiles", headers=sb_headers(sb_key), timeout=60, params={
        "select": "id,email,first_name,keywords,topics,journals,authors,digest,digest_size,last_digest_on",
        "digest": "eq.true"})
    r.raise_for_status()
    profiles = r.json()
    log(f"{len(profiles)} accounts want the morning email")

    sent = 0
    for prof in profiles:
        email = (prof.get("email") or "").strip()
        if not email or (a.only and email.lower() != a.only.lower()):
            continue
        last = prof.get("last_digest_on")
        if last == today.isoformat() and not a.force:
            continue
        # new since the last email (or the last 2 days for a first email), published in the last 3 weeks
        since = last if (last and not a.force) else (today - dt.timedelta(days=2)).isoformat()
        recent_cut = (today - dt.timedelta(days=21)).isoformat()
        fresh = [p for p in papers if (p.get("addedAt") or "") > since and (p.get("date") or "") >= recent_cut]
        if a.force:
            fresh = [p for p in papers if (p.get("addedAt") or "") >= since and (p.get("date") or "") >= recent_cut]
        if not has_prefs(prof):
            fresh = [p for p in fresh if p.get("topics")]  # no preferences yet: the general feed
        items = rank_for(fresh, prof)[: int(prof.get("digest_size") or 8)]
        if not items:
            log(f"  {email}: nothing new today, no email")
            continue
        first = (prof.get("first_name") or "").strip()
        subject = f"{greeting(now)}{', ' + first if first else ''}: {len(items)} new paper{'s' if len(items) != 1 else ''} for you"
        body = email_html(first, now, items, site_url)

        if a.dry_run:
            out = ROOT / "_digest_preview"
            out.mkdir(exist_ok=True)
            (out / f"{email.replace('@', '_at_')}.html").write_text(body, encoding="utf-8")
            log(f"  {email}: {len(items)} papers (dry run, saved preview)")
            sent += 1
            continue

        try:
            rr = requests.post("https://api.resend.com/emails", timeout=60, headers={
                "Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                json={"from": sender, "to": [email], "subject": subject, "html": body,
                      "text": email_text(first, now, items, site_url),
                      "headers": {"List-Unsubscribe": f"<{site_url.rstrip('/')}/#settings>"} if site_url else {}})
            if rr.status_code >= 400:
                log(f"  {email}: Resend refused ({rr.status_code}): {rr.text[:300]}")
                continue
        except Exception as e:
            log(f"  {email}: sending failed: {e}")
            continue
        requests.patch(f"{sb_url}/rest/v1/profiles", headers={**sb_headers(sb_key), "Prefer": "return=minimal"},
                       params={"id": f"eq.{prof['id']}"}, json={"last_digest_on": today.isoformat()}, timeout=60)
        sent += 1
        log(f"  {email}: sent {len(items)} papers")
        time.sleep(0.6)  # Resend allows 2 requests a second
    log(f"Done: {sent} email(s)")


if __name__ == "__main__":
    main()
