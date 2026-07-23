#!/usr/bin/env python3
"""Poll scrap.tf/raffles and post new raffle embeds to a Discord webhook.

Posts a rich embed per new raffle, then edits it into a gray "ended"
tombstone once the raffle's end time passes. Stdlib only, Python 3.8+.

Env:
  DISCORD_WEBHOOK_URL  Discord webhook to post to. If unset, actions are
                       printed instead of sent (dry run).

State (seen.json):
  {"raffles": {"ABC123": {"msg": "123...", "end": 1784841481,
               "title": "...", "items": 4, "ended": false}, ...}}
"""

import html as html_lib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

RAFFLES_URL = "https://scrap.tf/raffles"
USER_AGENT = "scraptf-raffle-notifier (personal notification script)"
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
PRUNE_AFTER = 7 * 86400  # drop tombstoned entries a week after they end

ID_RE = re.compile(r'/raffles/([A-Z0-9]{6})\b')
TITLE_RE = re.compile(
    r'<div class="raffle-name">.*?<a href="/raffles/[A-Z0-9]{6}">(.*?)</a>',
    re.DOTALL,
)
TIME_RE = re.compile(r'class="raffle-time-left"[^>]*data-time="(\d+)"')
ITEM_RE = re.compile(r'class="item hoverable')


# ---------------------------------------------------------------------------
# EMBED STYLING — EDIT FREELY. Everything below in this section is looks-only.
# ---------------------------------------------------------------------------

COLOR_NORMAL = 4718336    # green, raffles under BIG_RAFFLE_ITEMS items
COLOR_BIG = 16766720      # gold, big raffles
COLOR_ENDED = 9807270     # gray tombstone
BIG_RAFFLE_ITEMS = 10


def build_embed(raffle: dict) -> dict:
    url = f"https://scrap.tf/raffles/{raffle['id']}"
    color = COLOR_BIG if raffle["items"] >= BIG_RAFFLE_ITEMS else COLOR_NORMAL
    ends = f" · Ends <t:{raffle['end']}:R>" if raffle["end"] else ""
    return {
        "title": raffle["title"],
        "url": url,
        "color": color,
        "description": (
            f"\U0001F381 **{raffle['items']} items**{ends}\n\n"
            f"**[\U0001F449 CLICK HERE]({url})**"
        ),
    }


def build_ended_embed(entry: dict) -> dict:
    return {
        "title": "\U0001F3C1 Raffle ended",
        "color": COLOR_ENDED,
    }


# ---------------------------------------------------------------------------
# Plumbing below — no styling here.
# ---------------------------------------------------------------------------


def fetch_page() -> str:
    req = urllib.request.Request(RAFFLES_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    if "Just a moment" in body or "cf-chl" in body:
        sys.exit("Blocked by Cloudflare challenge — this IP can't scrape scrap.tf.")
    return body


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment)
    return html_lib.unescape(text).strip()


def extract_raffles(page: str) -> list:
    """Parse each raffle panel into {id, title, end, items}."""
    raffles = []
    seen_ids = set()
    chunks = page.split('class="panel-raffle ')[1:]
    for chunk in chunks:
        m = ID_RE.search(chunk)
        if not m:
            continue
        rid = m.group(1)
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        tm = TITLE_RE.search(chunk)
        title = strip_tags(tm.group(1)) if tm else ""
        em = TIME_RE.search(chunk)
        raffles.append({
            "id": rid,
            "title": title or "Untitled raffle",
            "end": int(em.group(1)) if em else None,
            "items": len(ITEM_RE.findall(chunk)),
        })
    return raffles


def load_state() -> dict:
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"raffles": {}}
    if isinstance(data, list):
        # Migrate old format (plain id list): known but untracked, no tombstone.
        return {"raffles": {rid: {"msg": None, "end": None, "ended": True}
                            for rid in data}}
    return data


def save_state(state: dict) -> None:
    now = int(time.time())
    state["raffles"] = {
        rid: e for rid, e in state["raffles"].items()
        if not (e.get("ended") and e.get("end") and now - e["end"] > PRUNE_AFTER)
    }
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
        f.write("\n")


def webhook_request(url: str, payload: dict, method: str = "POST") -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


def main() -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    raffles = extract_raffles(fetch_page())
    if not raffles:
        sys.exit("Parsed 0 raffles — page layout may have changed.")

    state = load_state()
    tracked = state["raffles"]
    first_run = not tracked
    now = int(time.time())
    posted = tombstoned = 0

    for raffle in raffles:
        rid = raffle["id"]
        if rid in tracked:
            continue
        entry = {"msg": None, "end": raffle["end"], "title": raffle["title"],
                 "items": raffle["items"], "ended": False}
        if first_run:
            entry["ended"] = True  # seed silently, nothing to tombstone
        elif webhook:
            try:
                msg = webhook_request(webhook + "?wait=true",
                                      {"embeds": [build_embed(raffle)]})
                entry["msg"] = msg.get("id")
                posted += 1
                time.sleep(1)
            except urllib.error.HTTPError as e:
                print(f"Post failed for {rid}: HTTP {e.code}")
        else:
            print(f"[dry run] new raffle: https://scrap.tf/raffles/{rid}")
        tracked[rid] = entry

    for rid, entry in tracked.items():
        if entry.get("ended") or not entry.get("msg"):
            continue
        if entry.get("end") and entry["end"] <= now:
            if webhook:
                try:
                    webhook_request(f"{webhook}/messages/{entry['msg']}",
                                    {"embeds": [build_ended_embed(entry)]},
                                    method="PATCH")
                except urllib.error.HTTPError as e:
                    if e.code != 404:  # message deleted by hand: fine, move on
                        print(f"Tombstone failed for {rid}: HTTP {e.code}")
                time.sleep(1)
            else:
                print(f"[dry run] tombstone: {rid}")
            entry["ended"] = True
            tombstoned += 1

    save_state(state)
    if first_run:
        print(f"Seeded {len(raffles)} existing raffles, nothing posted.")
    else:
        print(f"{len(raffles)} raffles on page, {posted} posted, "
              f"{tombstoned} tombstoned.")


if __name__ == "__main__":
    main()
