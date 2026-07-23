#!/usr/bin/env python3
"""Poll scrap.tf/raffles and post new raffle links to a Discord webhook.

Stdlib only — runs on GitHub Actions, a Raspberry Pi, or any machine with
Python 3.8+. State lives in seen.json next to this script.

Env:
  DISCORD_WEBHOOK_URL  Discord webhook to post to. If unset, new raffles are
                       printed instead of posted (dry run).
"""

import json
import os
import re
import sys
import time
import urllib.request

RAFFLES_URL = "https://scrap.tf/raffles"
USER_AGENT = "scraptf-raffle-notifier (personal notification script)"
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
SEEN_CAP = 500  # keep the most recent N ids so the file never grows unbounded
ID_RE = re.compile(r'/raffles/([A-Z0-9]{6})\b')


def fetch_page() -> str:
    req = urllib.request.Request(RAFFLES_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    if "Just a moment" in body or "cf-chl" in body:
        sys.exit("Blocked by Cloudflare challenge — this IP can't scrape scrap.tf.")
    return body


def extract_ids(html: str) -> list:
    ids = []
    for m in ID_RE.finditer(html):
        rid = m.group(1)
        if rid not in ids:
            ids.append(rid)
    return ids


def load_seen() -> list:
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_seen(ids: list) -> None:
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(ids[:SEEN_CAP], f, indent=0)
        f.write("\n")


def post_webhook(url: str, raffle_id: str) -> None:
    payload = json.dumps(
        {"content": f"New scrap.tf raffle: https://scrap.tf/raffles/{raffle_id}"}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def main() -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    current = extract_ids(fetch_page())
    if not current:
        sys.exit("Parsed 0 raffle ids — page layout may have changed.")

    seen = load_seen()
    if not seen:
        # First run: seed state silently so the channel isn't flooded.
        save_seen(current)
        print(f"Seeded {len(current)} existing raffles, nothing posted.")
        return

    new = [rid for rid in current if rid not in seen]
    for rid in new:
        if webhook:
            post_webhook(webhook, rid)
            time.sleep(1)  # be gentle with Discord rate limits
        else:
            print(f"[dry run] new raffle: https://scrap.tf/raffles/{rid}")

    if new:
        save_seen(new + seen)
    print(f"{len(current)} raffles on page, {len(new)} new.")


if __name__ == "__main__":
    main()
