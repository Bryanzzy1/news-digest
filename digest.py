#!/usr/bin/env python3
"""AI/tech news digest: fetch free RSS -> rank+summarize with one LLM call -> email via Resend.

Two runs/day (US Eastern):
  9AM  digest -> covers 5PM previous day .. 9AM  (overnight, flagship)
  5PM  digest -> covers 9AM .. 5PM                (workday)

Which run this is comes from the DIGEST_SLOT env var ("morning" | "afternoon"),
set by the workflow after it checks the real Eastern hour (so DST is automatic).
"""
import os
import sys
import json
import html
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

ET_ZONE = ZoneInfo("America/New_York")
UA = "Mozilla/5.0 (ai-news-digest; +https://github.com)"
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_ITEMS_TO_MODEL = 60          # cap sent to the model to keep tokens minimal
DESC_CHARS = 320                 # truncate each description (feeds the summary)
HN_MIN_POINTS = 30               # floor so we cover HN broadly, not just front page
HN_MAX_ITEMS = 40                # keep the top-scoring HN stories in the window


# ------------------------------- window ------------------------------------
def compute_window(slot: str, now_et: datetime):
    """Return (start, end) in UTC for the coverage window."""
    today = now_et.date()
    if slot == "morning":
        # 5PM previous day .. 9AM today
        start = datetime.combine(today - timedelta(days=1), datetime.min.time(),
                                 tzinfo=ET_ZONE).replace(hour=17)
        end = datetime.combine(today, datetime.min.time(), tzinfo=ET_ZONE).replace(hour=9)
    elif slot == "afternoon":
        # 9AM .. 5PM today
        start = datetime.combine(today, datetime.min.time(), tzinfo=ET_ZONE).replace(hour=9)
        end = datetime.combine(today, datetime.min.time(), tzinfo=ET_ZONE).replace(hour=17)
    else:
        raise SystemExit(f"unknown DIGEST_SLOT: {slot!r}")
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


# ------------------------------- fetch -------------------------------------
def load_feeds(path="feeds.txt"):
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f
                if ln.strip() and not ln.lstrip().startswith("#")]


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return html.unescape(re.sub(r"\s+", " ", s)).strip()


def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)  # RFC822 (RSS)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))  # ISO (Atom)
        except ValueError:
            return None
    if d and d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def parse_feed(raw, source_host):
    """Yield dicts from RSS or Atom without external deps."""
    items = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return items
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    # RSS
    for it in root.iter("item"):
        title = it.findtext("title")
        link = it.findtext("link")
        desc = it.findtext("description") or it.findtext(
            "{http://purl.org/rss/1.0/modules/content/}encoded")
        date = parse_date(it.findtext("pubDate") or it.findtext(
            "{http://purl.org/dc/elements/1.1/}date"))
        if title:
            items.append({"title": strip_tags(title), "link": link or "",
                          "desc": strip_tags(desc)[:DESC_CHARS], "date": date,
                          "source": source_host})
    # Atom
    for it in root.iter("{http://www.w3.org/2005/Atom}entry"):
        title = it.findtext("atom:title", namespaces=ns)
        link_el = it.find("atom:link", namespaces=ns)
        link = link_el.get("href") if link_el is not None else ""
        desc = (it.findtext("atom:summary", namespaces=ns)
                or it.findtext("atom:content", namespaces=ns))
        date = parse_date(it.findtext("atom:updated", namespaces=ns)
                          or it.findtext("atom:published", namespaces=ns))
        if title:
            items.append({"title": strip_tags(title), "link": link or "",
                          "desc": strip_tags(desc)[:DESC_CHARS], "date": date,
                          "source": source_host})
    return items


def host_of(url):
    m = re.search(r"https?://([^/]+)/?", url)
    return (m.group(1).replace("www.", "") if m else url)


def fetch_hn(start_utc, end_utc):
    """Every Hacker News story in the window via the Algolia API, ranked by points.

    Uses numericFilters on created_at_i (unix seconds) so the window is exact,
    then keeps the top HN_MAX_ITEMS by points. Covers all of HN, not just the
    front page. HN 'title' is the story headline; description is empty (HN links
    out), so we tag the source as news.ycombinator.com and let the LLM summarize
    from the title plus its own knowledge.
    """
    start_i, end_i = int(start_utc.timestamp()), int(end_utc.timestamp())
    url = ("https://hn.algolia.com/api/v1/search_by_date?tags=story"
           f"&numericFilters=created_at_i>{start_i},created_at_i<{end_i},"
           f"points>={HN_MIN_POINTS}&hitsPerPage=200")
    try:
        data = json.loads(fetch(url))
    except Exception as e:  # noqa: BLE001
        print(f"  ! skip hacker news: {e}", file=sys.stderr)
        return []
    hits = sorted(data.get("hits", []),
                  key=lambda h: h.get("points") or 0, reverse=True)[:HN_MAX_ITEMS]
    out = []
    for h in hits:
        title = h.get("title")
        if not title:
            continue
        pts, ncom = h.get("points") or 0, h.get("num_comments") or 0
        out.append({
            "title": strip_tags(title),
            "link": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
            "desc": f"Hacker News: {pts} points, {ncom} comments."[:DESC_CHARS],
            "date": datetime.fromtimestamp(h["created_at_i"], tz=timezone.utc),
            "source": "news.ycombinator.com",
        })
    print(f"  hacker news: {len(out)} stories in window (>= {HN_MIN_POINTS} pts)")
    return out


def collect(start_utc, end_utc):
    seen, out = set(), []
    for it in fetch_hn(start_utc, end_utc):
        key = it["title"].lower()[:80]
        if key not in seen:
            seen.add(key)
            out.append(it)
    for feed in load_feeds():
        host = host_of(feed)
        try:
            raw = fetch(feed)
        except Exception as e:  # noqa: BLE001 - never let one bad feed kill the run
            print(f"  ! skip {host}: {e}", file=sys.stderr)
            continue
        for it in parse_feed(raw, host):
            d = it["date"]
            if d is None or not (start_utc <= d <= end_utc):
                continue
            key = (it["title"].lower()[:80])
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


# ------------------------------- rank via LLM ------------------------------
SYSTEM = (
    "You are a sharp tech-news editor. You rank AI/agents/ML/tech news by real "
    "importance to a technical reader. Signal over hype. Return STRICT JSON only."
)


def build_prompt(items, slot, start_et, end_et):
    lines = [f"[{i}] {it['title']} | {it['source']} | {it['desc']}"
             for i, it in enumerate(items)]
    window = (f"{start_et:%a %b %-d %-I%p} to {end_et:%a %b %-d %-I%p} ET"
              if os.name != "nt" else
              f"{start_et:%a %b %d %I%p} to {end_et:%a %b %d %I%p} ET")
    return (
        f"Coverage window: {window} ({slot}).\n"
        f"Below are {len(items)} candidate stories, one per line as "
        f"[index] title | source | description.\n\n"
        + "\n".join(lines)
        + "\n\nSelect and rank the most important, deduping near-identical stories. "
        "Prioritize AI, agents, and ML; then broader tech. "
        "For each, write a self-contained summary of 2-3 sentences so the reader "
        "understands what happened and why it matters WITHOUT clicking anything. "
        "Use only the given title/description plus your own knowledge; never invent "
        "specifics you are unsure of. Return JSON:\n"
        '{"headline":"<=8-word overall vibe of the window",'
        '"items":[{"i":<index>,"cat":"<AI|Agents|ML|Tech>",'
        '"summary":"2-3 sentence self-contained summary"}]}\n'
        "Include at most 12 items. JSON only, no prose."
    )


def rank(items, slot, start_et, end_et):
    key = os.environ["OPENAI_API_KEY"]
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": build_prompt(items, slot, start_et, end_et)}],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            return json.loads(data["choices"][0]["message"]["content"])
        except Exception as e:  # noqa: BLE001
            print(f"  ! LLM attempt {attempt+1} failed: {e}", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    raise SystemExit("LLM ranking failed after retries")


# ------------------------------- render + send -----------------------------
CAT_COLOR = {"AI": "#6366f1", "Agents": "#0ea5e9", "ML": "#8b5cf6", "Tech": "#64748b"}


def render(ranked, items, slot, start_et, end_et):
    label = "Morning digest" if slot == "morning" else "Afternoon digest"
    win = f"{start_et:%b %d, %I:%M %p} to {end_et:%b %d, %I:%M %p} ET"
    rows = []
    for n, r in enumerate(ranked.get("items", []), 1):
        it = items[r["i"]]
        cat = r.get("cat", "Tech")
        color = CAT_COLOR.get(cat, "#64748b")
        rows.append(f"""
      <tr><td style="padding:16px 0;border-bottom:1px solid #eee;">
        <div style="margin-bottom:6px;">
          <span style="display:inline-block;min-width:22px;color:#94a3b8;font-weight:700;">{n}</span>
          <span style="background:{color};color:#fff;font-size:11px;padding:2px 8px;border-radius:10px;">{html.escape(cat)}</span>
          <span style="color:#94a3b8;font-size:12px;">{html.escape(it['source'])}</span>
        </div>
        <div style="color:#0f172a;font-weight:700;font-size:16px;margin-left:22px;">{html.escape(it['title'])}</div>
        <div style="color:#334155;font-size:14px;line-height:1.55;margin:6px 0 0 22px;">{html.escape(r.get('summary',''))}</div>
      </td></tr>""")
    body = "".join(rows) or '<tr><td style="padding:24px;color:#64748b;">No new stories in this window.</td></tr>'
    return f"""<!doctype html><html><body style="margin:0;background:#f1f5f9;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:640px;margin:0 auto;padding:24px;">
    <div style="background:#fff;border-radius:14px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.08);">
      <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#6366f1;font-weight:700;">{label}</div>
      <h1 style="margin:6px 0 2px;font-size:22px;color:#0f172a;">{html.escape(ranked.get('headline','AI &amp; Tech News'))}</h1>
      <div style="color:#94a3b8;font-size:13px;margin-bottom:8px;">{win}</div>
      <table width="100%" cellpadding="0" cellspacing="0">{body}</table>
      <div style="color:#cbd5e1;font-size:11px;margin-top:20px;">Ranked by {MODEL} from {len(items)} stories across Hacker News and free RSS feeds.</div>
    </div>
  </div></body></html>"""


def send(subject, html_body):
    """Send via your own Gmail over SMTP (from you, to you).

    GMAIL_USER = your full Gmail address (this is also the From address).
    GMAIL_APP_PASSWORD = a 16-char Google App Password (NOT your login password).
    MAIL_TO = where to deliver (usually the same Gmail address).
    """
    import smtplib
    from email.mime.text import MIMEText

    user = os.environ["GMAIL_USER"]
    pw = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    to = os.environ.get("MAIL_TO", user)

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, pw)
        s.sendmail(user, [to], msg.as_string())
    print(f"  sent to {to}")


# ------------------------------- main --------------------------------------
def main():
    slot = os.environ.get("DIGEST_SLOT", "morning")
    now_et = datetime.now(ET_ZONE)
    start_utc, end_utc = compute_window(slot, now_et)
    start_et, end_et = start_utc.astimezone(ET_ZONE), end_utc.astimezone(ET_ZONE)
    print(f"slot={slot} window={start_et} .. {end_et}")

    items = collect(start_utc, end_utc)
    print(f"  collected {len(items)} items in window")
    if len(items) > MAX_ITEMS_TO_MODEL:
        items = items[:MAX_ITEMS_TO_MODEL]

    label = "Morning" if slot == "morning" else "Afternoon"
    if not items:
        html_body = render({"headline": "Quiet window", "items": []}, [], slot, start_et, end_et)
        send(f"[{label}] AI & Tech digest - nothing new", html_body)
        return

    ranked = rank(items, slot, start_et, end_et)
    # guard indexes
    ranked["items"] = [r for r in ranked.get("items", [])
                       if isinstance(r.get("i"), int) and 0 <= r["i"] < len(items)]
    html_body = render(ranked, items, slot, start_et, end_et)
    top = ranked.get("headline", "AI & Tech digest")
    send(f"[{label}] {top}", html_body)


if __name__ == "__main__":
    main()
