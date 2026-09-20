#!/usr/bin/env python3
"""
Singapore Deals & Vouchers watcher.

Pulls Google News / Reddit / blog RSS+Atom feeds scoped to Singapore deals,
scores each item with regex heuristics, dedupes against a local SQLite
"seen" store, writes a markdown report, and (if configured) sends a
Telegram message for anything new.

Stdlib only - no third-party packages required.
"""

import gzip
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import xml.etree.ElementTree as ET

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

LOOKBACK_DAYS = float(os.environ.get("LOOKBACK_DAYS", "2"))
SCORE_THRESHOLD = float(os.environ.get("SCORE_THRESHOLD", "4"))
DB_PATH = os.environ.get("DB_PATH", "seen.sqlite3")
REPORT_PATH = os.environ.get("REPORT_PATH", "report.md")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

USER_AGENT = "Mozilla/5.0 (compatible; SGDealsBot/1.0; +https://github.com)"

GOOGLE_NEWS_QUERIES = [
    "Singapore voucher",
    "Singapore free voucher OR e-voucher OR gift card",
    "Singapore earn a voucher OR complete a challenge OR step challenge reward",
    "Singapore promo code OR discount code",
    "Singapore tech deal OR gadget sale",
    "Singapore 1-for-1 OR free meal OR F&B deal",
    "Singapore skincare deal OR beauty sale",
]

REDDIT_SUBS = ["singapore", "singaporefi", "askSingapore"]
REDDIT_QUERY = "voucher OR deal OR promo OR freebie OR discount OR giveaway"

BLOG_FEEDS = [
    ("Milelion", "https://milelion.com/feed/"),
    ("Mothership", "https://mothership.sg/feed/"),
]


def build_sources():
    sources = []
    for q in GOOGLE_NEWS_QUERIES:
        url = (
            "https://news.google.com/rss/search?q="
            + quote(q)
            + "&hl=en-SG&gl=SG&ceid=SG:en"
        )
        sources.append((f"Google News: {q}", url))
    for sub in REDDIT_SUBS:
        url = (
            f"https://www.reddit.com/r/{sub}/search.rss?q="
            + quote(REDDIT_QUERY)
            + "&restrict_sr=1&sort=new&t=week"
        )
        sources.append((f"r/{sub}", url))
    for name, url in BLOG_FEEDS:
        sources.append((name, url))
    return sources


# --------------------------------------------------------------------------
# Scoring heuristics
# --------------------------------------------------------------------------

# (regex pattern, weight) - heavily upweight "earn a voucher" style language
EARN_PATTERNS = [
    (r"\bearn\s+a\s+(free\s+)?voucher\b", 6),
    (r"\bearn\s+a\s*\$", 6),
    (r"\bcomplete\s+(the|a)\s+challenge\b", 6),
    (r"\bstep\s+challenge\b", 6),
    (r"\bhej\s*fit\b", 6),
    (r"\bsign[\s-]?up\s+(and\s+get|reward|bonus)\b", 5),
    (r"\brefer(ral)?\s+(a\s+)?friend\b", 4),
    (r"\bclaim\s+(your|a|the)\s+(free\s+)?voucher\b", 6),
    (r"\bredeem\s+(your|a|the)\s+(free\s+)?voucher\b", 6),
    (r"\bfree\s+e?-?voucher\b", 5),
    (r"\bcashback\s+when\s+you\b", 4),
    (r"\bspend\s+and\s+get\b", 4),
    (r"\bregister\s+and\s+get\b", 4),
    (r"\bdownload\s+the\s+app\s+and\s+get\b", 4),
    (r"\bcomplete\s+your\s+profile\b", 3),
    (r"\bwin\s+a\s+voucher\b", 5),
    (r"\breward\s+yourself\b", 3),
    (r"\btask\s+reward\b", 4),
    (r"\bquiz\s+reward\b", 4),
]

# Generic deal language - modest upweight
DEAL_PATTERNS = [
    (r"\bvoucher\b", 2),
    (r"\be-?voucher\b", 2),
    (r"\bgift\s*card\b", 2),
    (r"\bpromo\s*code\b", 2),
    (r"\bdiscount\s*code\b", 2),
    (r"\bcoupon\b", 2),
    (r"\d{1,2}\s*%\s*off\b", 2),
    (r"\$\s*\d+(\.\d+)?\s*off\b", 2),
    (r"\b1[\s-]for[\s-]1\b", 3),
    (r"\bsale\b", 1),
    (r"\bfreebie\b", 2),
    (r"\bbundle\s+deal\b", 1),
    (r"\bgiveaway\b", 2),
    (r"\bdeal(s)?\b", 1),
    (r"\bpromotion(s)?\b", 1),
]

# Negative-context language that should suppress "deal" scoring for what
# is really scam/policy/finance news, not an actual deal to claim.
NEGATIVE_PATTERNS = [
    (r"\bscam(mer|s|med)?\b", 6),
    (r"\bphishing\b", 6),
    (r"\barrest(ed)?\b", 6),
    (r"\bcharged\s+in\s+court\b", 6),
    (r"\bjail(ed)?\b", 6),
    (r"\bfined\b", 5),
    (r"\bconned\b", 6),
    (r"\bcheated\b", 5),
    (r"\bvictim(s)?\b", 4),
    (r"\bponzi\b", 6),
    (r"\bfraud(ulent)?\b", 5),
    (r"\bstock\s+price\b", 5),
    (r"\bshare\s+price\b", 5),
    (r"\bshares?\s+(fell|rose|surged|plunged)\b", 5),
    (r"\binflation\b", 4),
    (r"\bcost\s+of\s+living\b", 4),
    (r"\bbudget\s+20\d\d\b", 5),
    (r"\bparliament\b", 5),
    (r"\bministry\s+of\s+finance\b", 5),
    (r"\biras\b", 4),
    (r"\bassurance\s+package\b", 5),
    (r"\bu-save\b", 4),
    (r"\bs&cc\s+rebate\b", 4),
    (r"\beligibility\s+criteria\b", 3),
    (r"\bpayout\s+date\b", 4),
    (r"\belection\b", 4),
]

CATEGORY_PATTERNS = {
    "Tech": [
        r"\biphone\b", r"\bsamsung\b", r"\blaptop\b", r"\bgadget\b",
        r"\belectronics?\b", r"\btech\s+deal\b", r"\bcourts\b",
        r"\bharvey\s+norman\b", r"\bgaming\b", r"\bps5\b", r"\bplaystation\b",
        r"\bxbox\b", r"\bnintendo\b", r"\bswitch\b", r"\btablet\b",
        r"\bipad\b", r"\bmacbook\b", r"\bearbuds?\b", r"\bairpods\b",
        r"\bsmartwatch\b", r"\bappliance\b",
    ],
    "F&B": [
        r"\brestaurant\b", r"\bcafe\b", r"\bbuffet\b", r"\bfree\s+meal\b",
        r"\bdining\b", r"\bfood\s*delivery\b", r"\bfoodpanda\b",
        r"\bgrabfood\b", r"\bdeliveroo\b", r"\bbak\s+kut\s+teh\b",
        r"\bhawker\b", r"\bbubble\s+tea\b", r"\bcoffee\b", r"\bkfc\b",
        r"\bmcdonald'?s\b", r"\bburger\s+king\b", r"\bpizza\b", r"\bsushi\b",
        r"\bramen\b", r"\b1[\s-]for[\s-]1\b",
    ],
    "Skincare": [
        r"\bskincare\b", r"\bbeauty\b", r"\bcosmetics?\b", r"\bserum\b",
        r"\bmoisturi[sz]er\b", r"\bsunscreen\b", r"\bsephora\b",
        r"\bwatsons\b", r"\bguardian\b", r"\bk-beauty\b", r"\binnisfree\b",
        r"\blaneige\b", r"\bthe\s+ordinary\b", r"\bfacial\b", r"\bspa\b",
    ],
    "Lifestyle": [
        r"\btravel\b", r"\bstaycation\b", r"\bhotel\b", r"\bflight\b",
        r"\bshopping\b", r"\bmall\b", r"\bfashion\b", r"\bapparel\b",
        r"\buniqlo\b", r"\bzara\b", r"\blifestyle\b",
    ],
}

_EARN_RE = [(re.compile(p, re.I), w) for p, w in EARN_PATTERNS]
_DEAL_RE = [(re.compile(p, re.I), w) for p, w in DEAL_PATTERNS]
_NEG_RE = [(re.compile(p, re.I), w) for p, w in NEGATIVE_PATTERNS]
_CAT_RE = {
    cat: [re.compile(p, re.I) for p in pats]
    for cat, pats in CATEGORY_PATTERNS.items()
}


def score_item(text):
    earn_score = sum(w for pat, w in _EARN_RE if pat.search(text))
    deal_score = sum(w for pat, w in _DEAL_RE if pat.search(text))
    neg_score = sum(w for pat, w in _NEG_RE if pat.search(text))
    categories = [cat for cat, pats in _CAT_RE.items() if any(p.search(text) for p in pats)]
    total = earn_score + deal_score - neg_score
    return {
        "earn_score": earn_score,
        "deal_score": deal_score,
        "neg_score": neg_score,
        "total": total,
        "categories": categories,
        "is_earn": earn_score > 0,
    }


# --------------------------------------------------------------------------
# Fetching & parsing
# --------------------------------------------------------------------------

def fetch(url):
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        data = resp.read()
        if resp.info().get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data


def strip_tags(text):
    return re.sub(r"<[^>]+>", " ", text or "")


def clean_text(text):
    text = strip_tags(text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def el_text(el):
    if el is None or el.text is None:
        return ""
    return el.text.strip()


ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def parse_feed(xml_bytes):
    """Parse RSS <item> or Atom <entry> elements into raw dicts."""
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return items

    for item in root.findall(".//item"):
        items.append(
            {
                "title": clean_text(el_text(item.find("title"))),
                "link": el_text(item.find("link")),
                "summary": clean_text(el_text(item.find("description"))),
                "date_raw": el_text(item.find("pubDate")),
                "date_kind": "rss",
            }
        )

    for entry in root.findall(".//atom:entry", ATOM_NS):
        link_el = entry.find("atom:link", ATOM_NS)
        link = link_el.get("href") if link_el is not None else ""
        summary = el_text(entry.find("atom:summary", ATOM_NS)) or el_text(
            entry.find("atom:content", ATOM_NS)
        )
        date_raw = el_text(entry.find("atom:updated", ATOM_NS)) or el_text(
            entry.find("atom:published", ATOM_NS)
        )
        items.append(
            {
                "title": clean_text(el_text(entry.find("atom:title", ATOM_NS))),
                "link": link,
                "summary": clean_text(summary),
                "date_raw": date_raw,
                "date_kind": "atom",
            }
        )

    return items


def parse_date(date_raw, date_kind):
    if not date_raw:
        return None
    try:
        if date_kind == "rss":
            dt = parsedate_to_datetime(date_raw)
        else:
            dt = datetime.fromisoformat(date_raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def normalize_title(title):
    t = title.lower()
    t = re.sub(r"[^a-z0-9\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def title_hash(title):
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def open_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen (
            hash TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            source TEXT,
            first_seen TEXT
        )
        """
    )
    conn.commit()
    return conn


def already_seen(conn, hash_):
    row = conn.execute("SELECT 1 FROM seen WHERE hash = ?", (hash_,)).fetchone()
    return row is not None


def mark_seen(conn, hash_, title, url, source):
    conn.execute(
        "INSERT OR IGNORE INTO seen (hash, title, url, source, first_seen) VALUES (?, ?, ?, ?, ?)",
        (hash_, title, url, source, datetime.now(timezone.utc).isoformat()),
    )


# --------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------

def collect_candidates():
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    seen_hashes_this_run = set()
    candidates = []

    for source_name, url in build_sources():
        try:
            raw = fetch(url)
        except (URLError, HTTPError, TimeoutError, OSError) as exc:
            print(f"[warn] failed to fetch {source_name}: {exc}", file=sys.stderr)
            continue

        for raw_item in parse_feed(raw):
            title = raw_item["title"]
            if not title or not raw_item["link"]:
                continue

            dt = parse_date(raw_item["date_raw"], raw_item["date_kind"])
            if dt is not None and dt < cutoff:
                continue

            h = title_hash(title)
            if h in seen_hashes_this_run:
                continue
            seen_hashes_this_run.add(h)

            combined_text = f"{title} {raw_item['summary']}"
            scores = score_item(combined_text)
            if scores["total"] < SCORE_THRESHOLD:
                continue

            candidates.append(
                {
                    "hash": h,
                    "title": title,
                    "link": raw_item["link"],
                    "source": source_name,
                    "date": dt,
                    **scores,
                }
            )

    candidates.sort(key=lambda c: c["total"], reverse=True)
    return candidates


# --------------------------------------------------------------------------
# Report + Telegram
# --------------------------------------------------------------------------

SECTION_ORDER = ["Tech", "F&B", "Skincare", "Lifestyle"]
SECTION_EMOJI = {
    "earn": "🎁",
    "Tech": "💻",
    "F&B": "🍜",
    "Skincare": "🧴",
    "Lifestyle": "🛍",
}


def bucket_candidates(new_items):
    """Split into earn-a-voucher bucket plus one bucket per category."""
    earn = [c for c in new_items if c["is_earn"]]
    rest = [c for c in new_items if not c["is_earn"]]

    buckets = {"earn": earn}
    for cat in SECTION_ORDER:
        buckets[cat] = []

    for c in rest:
        cats = c["categories"] or ["Lifestyle"]
        placed = False
        for cat in SECTION_ORDER:
            if cat in cats:
                buckets[cat].append(c)
                placed = True
                break
        if not placed:
            buckets["Lifestyle"].append(c)

    return buckets


def write_report(new_items, path):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"# SG Deals Report — {today}", ""]
    lines.append(f"Generated at {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    if not new_items:
        lines.append("No new deals found today.")
    else:
        buckets = bucket_candidates(new_items)
        if buckets["earn"]:
            lines.append("## 🎁 Earn a Voucher / Freebies")
            for c in buckets["earn"]:
                lines.append(f"- [{c['title']}]({c['link']}) — score {c['total']} — {c['source']}")
            lines.append("")
        for cat in SECTION_ORDER:
            if buckets[cat]:
                lines.append(f"## {SECTION_EMOJI[cat]} {cat}")
                for c in buckets[cat]:
                    lines.append(f"- [{c['title']}]({c['link']}) — score {c['total']} — {c['source']}")
                lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def build_telegram_messages(new_items):
    buckets = bucket_candidates(new_items)

    all_lines = []
    if buckets["earn"]:
        all_lines.append(f"{SECTION_EMOJI['earn']} <b>Earn a Voucher / Freebies</b>")
        for c in buckets["earn"]:
            title = html.escape(c["title"])
            all_lines.append(f'• <a href="{html.escape(c["link"])}">{title}</a> ({html.escape(c["source"])})')
        all_lines.append("")

    for cat in SECTION_ORDER:
        if buckets[cat]:
            all_lines.append(f"{SECTION_EMOJI[cat]} <b>{cat}</b>")
            for c in buckets[cat]:
                title = html.escape(c["title"])
                all_lines.append(f'• <a href="{html.escape(c["link"])}">{title}</a> ({html.escape(c["source"])})')
            all_lines.append("")

    header = "🇸🇬 <b>New Singapore deals found</b>\n\n"

    chunks = []
    current = header
    for line in all_lines:
        candidate = current + line + "\n"
        if len(candidate) > 4000:
            chunks.append(current.rstrip())
            current = line + "\n"
        else:
            current = candidate
    if current.strip():
        chunks.append(current.rstrip())

    return chunks


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read()


def notify_telegram(new_items):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[info] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set - skipping Telegram send.")
        return

    for chunk in build_telegram_messages(new_items):
        try:
            send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, chunk)
        except (URLError, HTTPError, TimeoutError, OSError) as exc:
            print(f"[warn] failed to send Telegram message: {exc}", file=sys.stderr)


def send_test_ping():
    """Send a fixed test message, bypassing feeds/dedup, to verify Telegram wiring."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[error] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set - cannot send test ping.", file=sys.stderr)
        return 1
    try:
        send_telegram(
            TELEGRAM_BOT_TOKEN,
            TELEGRAM_CHAT_ID,
            "✅ <b>SG Deals Bot</b> is wired up correctly. "
            "You'll get a message here whenever a new Singapore deal is found.",
        )
        print("[info] test ping sent successfully.")
        return 0
    except (URLError, HTTPError, TimeoutError, OSError) as exc:
        print(f"[error] failed to send test ping: {exc}", file=sys.stderr)
        return 1


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    if os.environ.get("SEND_TEST_PING", "").strip().lower() in ("1", "true", "yes"):
        return send_test_ping()

    always_notify = os.environ.get("ALWAYS_NOTIFY", "").strip().lower() in ("1", "true", "yes")

    conn = open_db(DB_PATH)

    all_candidates = collect_candidates()
    new_items = [c for c in all_candidates if not already_seen(conn, c["hash"])]

    write_report(new_items, REPORT_PATH)

    if new_items:
        notify_telegram(new_items)
        for c in new_items:
            mark_seen(conn, c["hash"], c["title"], c["link"], c["source"])
        conn.commit()
        print(f"[info] found {len(new_items)} new item(s); report written to {REPORT_PATH}")
    else:
        print("[info] no new deals found; staying quiet.")
        if always_notify and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            try:
                send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, "No new deals right now. I'll keep watching.")
            except (URLError, HTTPError, TimeoutError, OSError) as exc:
                print(f"[warn] failed to send Telegram message: {exc}", file=sys.stderr)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
