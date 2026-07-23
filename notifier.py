#!/usr/bin/env python3
"""Poll scrap.tf/raffles and post new raffle messages to a Discord webhook.

Posts a Components V2 message per new raffle (title, live countdown, item
image gallery, enter link), then edits it into a gray "ended" tombstone once
the raffle's end time passes. Stdlib only, Python 3.8+.

Env:
  DISCORD_WEBHOOK_URL  Discord webhook to post to. If unset, actions are
                       printed instead of sent (dry run).

State (seen.json):
  {"raffles": {"ABC123": {"msg": "123...", "end": 1784841481,
               "title": "...", "items": 4, "ended": false}, ...}}
"""

import html as html_lib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

RAFFLES_URL = "https://scrap.tf/raffles"
MEGARAFFLE_URL = "https://scrap.tf/megaraffle"
USER_AGENT = ("scraptf-raffle-notifier/1.0 "
              "(+https://github.com/ceeprus/scraptf-raffle-notifier; "
              "personal raffle notifications)")
MEGA_FETCH_MARGIN = 600  # only fetch megaraffle page this close to its end
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
PRUNE_AFTER = 7 * 86400  # drop tombstoned entries a week after they end

ID_RE = re.compile(r'/raffles/([A-Z0-9]{6})\b')
TITLE_RE = re.compile(
    r'<div class="raffle-name">.*?<a href="/raffles/[A-Z0-9]{6}">(.*?)</a>',
    re.DOTALL,
)
TIME_RE = re.compile(r'class="raffle-time-left"[^>]*data-time="(\d+)"')
ITEM_RE = re.compile(r'class="item hoverable')
PAIR_RE = re.compile(
    r'quality(\d+)[^>]*?background-image:url\((https://[^)]+)\)')

IS_COMPONENTS_V2 = 1 << 15  # Discord message flag


# ---------------------------------------------------------------------------
# MESSAGE STYLING — EDIT FREELY. Everything in this section is looks-only.
# ---------------------------------------------------------------------------

COLOR_NORMAL = 4718336    # green, raffles under BIG_RAFFLE_ITEMS items
COLOR_BIG = 16766720      # gold, big raffles
COLOR_MEGA = 10181046     # purple, megaraffle
COLOR_ENDED = 9807270     # gray tombstone
BIG_RAFFLE_ITEMS = 10
GALLERY_MAX = 4           # mosaic fallback: images shown when strip fails

# Item strip (composed PNG) look
STRIP_BG = (35, 32, 30)         # scrap.tf-ish dark brown
STRIP_CELL_BG = (48, 44, 41)
STRIP_CELL = 72                 # cell pixel size
STRIP_PAD = 8
STRIP_PER_ROW = 5
STRIP_SLOTS = 10                # 2 rows; last slot becomes +N badge if needed

# TF2 item quality colors, keyed by scrap.tf's qualityN class number
QUALITY_COLORS = {
    0: (178, 178, 178),   # Normal
    1: (77, 116, 85),     # Genuine
    3: (71, 98, 145),     # Vintage
    5: (134, 80, 172),    # Unusual
    6: (255, 215, 0),     # Unique
    7: (112, 176, 74),    # Community
    9: (112, 176, 74),    # Self-Made
    11: (207, 106, 50),   # Strange
    13: (56, 243, 171),   # Haunted
    14: (170, 0, 0),      # Collector's
    15: (250, 250, 250),  # Decorated
}


def build_live_message(raffle: dict, strip: bool = False) -> dict:
    url = f"https://scrap.tf/raffles/{raffle['id']}"
    color = COLOR_BIG if raffle["items"] >= BIG_RAFFLE_ITEMS else COLOR_NORMAL
    ends = f" · Ends <t:{raffle['end']}:R>" if raffle["end"] else ""
    inner = [{
        "type": 10,  # Text Display
        "content": f"## [{raffle['title']}]({url})\n"
                   f"\U0001F381 **{raffle['items']} items**{ends}",
    }]
    if strip:
        inner.append({
            "type": 12,  # Media Gallery holding the composed strip
            "items": [{"media": {"url": "attachment://items.png"}}],
        })
    elif raffle["item_data"]:
        inner.append({
            "type": 12,  # Media Gallery, mosaic fallback
            "items": [{"media": {"url": u}}
                      for _, u in raffle["item_data"][:GALLERY_MAX]],
        })
    inner.append({
        "type": 10,
        "content": f"**[\U0001F449 CLICK HERE]({url})**",
    })
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [
            {"type": 17, "accent_color": color, "components": inner},
        ],
    }


def compose_strip(item_data: list, total: int) -> bytes:
    """Render item icons into one PNG: quality-colored cells, +N badge in the
    last slot when the raffle holds more than fits. Raises on any failure —
    caller falls back to the plain mosaic."""
    from PIL import Image, ImageDraw, ImageFont

    icons = []
    for quality, url in item_data[:STRIP_SLOTS]:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            img = Image.open(io.BytesIO(r.read())).convert("RGBA")
        icons.append((quality, img))
    if not icons:
        raise ValueError("no item images")

    if total <= len(icons):
        shown, badge = icons[:total], 0
    else:
        shown, badge = icons[:STRIP_SLOTS - 1], total - (STRIP_SLOTS - 1)

    slots = len(shown) + (1 if badge else 0)
    per_row = min(slots, STRIP_PER_ROW)
    rows = (slots + STRIP_PER_ROW - 1) // STRIP_PER_ROW
    cell, pad = STRIP_CELL, STRIP_PAD
    im = Image.new("RGBA", (pad + (cell + pad) * per_row,
                            pad + (cell + pad) * rows), (0, 0, 0, 0))

    def cell_at(i):
        r, c = divmod(i, STRIP_PER_ROW)
        return (pad + c * (cell + pad), pad + r * (cell + pad))

    for i, (quality, icon) in enumerate(shown):
        tile = Image.new("RGBA", (cell, cell), STRIP_CELL_BG + (255,))
        tile.alpha_composite(icon.resize((cell - 8, cell - 8)), (4, 4))
        d = ImageDraw.Draw(tile)
        d.rounded_rectangle([0, 0, cell - 1, cell - 1], radius=8, width=2,
                            outline=QUALITY_COLORS.get(quality, (255, 232, 168)))
        im.alpha_composite(tile, cell_at(i))

    if badge:
        tile = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
        d = ImageDraw.Draw(tile)
        font = None
        for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(name, 20)
                break
            except OSError:
                continue
        font = font or ImageFont.load_default()
        text = f"+{badge}"
        box = d.textbbox((0, 0), text, font=font)
        d.text(((cell - (box[2] - box[0])) // 2,
                (cell - (box[3] - box[1])) // 2 - 2),
               text, font=font, fill=(255, 255, 255, 255),
               stroke_width=2, stroke_fill=(0, 0, 0, 200))
        im.alpha_composite(tile, cell_at(len(shown)))

    out = io.BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def build_mega_message(num: int, end) -> dict:
    url = "https://scrap.tf/megaraffle"
    ends = f"\nEnds <t:{end}:R>" if end else ""
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [
            {"type": 17, "accent_color": COLOR_MEGA, "components": [
                {"type": 10,
                 "content": f"## [\U0001F389 Megaraffle #{num}]({url}){ends}\n\n"
                            f"**[\U0001F449 CLICK HERE]({url})**"},
            ]},
        ],
    }


def build_ended_message() -> dict:
    return {
        "flags": IS_COMPONENTS_V2,
        "components": [
            {"type": 17, "accent_color": COLOR_ENDED, "components": [
                {"type": 10, "content": "**\U0001F3C1 Raffle ended**"},
            ]},
        ],
        "attachments": [],  # drop the uploaded item strip, if any
    }


def build_ended_embed_legacy() -> dict:
    """Fallback for messages posted before the Components V2 switch —
    Discord refuses to add the V2 flag when editing an old message."""
    return {"embeds": [{"title": "\U0001F3C1 Raffle ended",
                        "color": COLOR_ENDED}],
            "attachments": []}


# ---------------------------------------------------------------------------
# Plumbing below — no styling here.
# ---------------------------------------------------------------------------


def fetch_page(url: str = RAFFLES_URL) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 429 or e.code >= 500:
            # Site busy or rate limiting: skip this run quietly, retry next.
            print(f"scrap.tf returned HTTP {e.code}, skipping this run.")
            sys.exit(0)
        raise
    if "Just a moment" in body or "cf-chl" in body:
        sys.exit("Blocked by Cloudflare challenge — this IP can't scrape scrap.tf.")
    return body


def extract_megaraffle(page: str):
    """Return {num, end} for the currently running megaraffle, or None."""
    hist = re.search(r"/megaraffle/history/(\d+)", page)
    if not hist:
        return None
    em = TIME_RE.search(page)
    return {"num": int(hist.group(1)) + 1,
            "end": int(em.group(1)) if em else None}


def strip_tags(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment)
    return html_lib.unescape(text).strip()


def upsize(url: str) -> str:
    # List page serves small 96px icons; ask the CDN for a larger render.
    return re.sub(r"/96fx96f$", "/330x192", url)


def extract_raffles(page: str) -> list:
    """Parse each raffle panel into {id, title, end, items, images}."""
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
        em = TIME_RE.search(chunk)
        title = strip_tags(tm.group(1)) if tm else ""
        raffles.append({
            "id": rid,
            "title": title or "Untitled raffle",
            "end": int(em.group(1)) if em else None,
            "items": len(ITEM_RE.findall(chunk)),
            "item_data": [(int(q), upsize(u)) for q, u
                          in PAIR_RE.findall(chunk)[:STRIP_SLOTS]],
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


def save_state(state: dict, on_page=()) -> None:
    now = int(time.time())

    def stale(e, rid):
        if not e.get("ended"):
            return False
        if e.get("end"):
            return now - e["end"] > PRUNE_AFTER
        # No end time recorded (entries migrated from the old list format):
        # prune once the raffle has left the page.
        return rid not in on_page

    state["raffles"] = {
        rid: e for rid, e in state["raffles"].items() if not stale(e, rid)
    }
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
        f.write("\n")


def tombstone_message(webhook: str, msg_id: str, label: str) -> None:
    msg_url = f"{webhook}/messages/{msg_id}"
    try:
        webhook_request(msg_url + "?with_components=true",
                        build_ended_message(), method="PATCH")
    except urllib.error.HTTPError as e:
        if e.code == 400:
            # Pre-V2 message: edit its embed instead.
            try:
                webhook_request(msg_url, build_ended_embed_legacy(),
                                method="PATCH")
            except urllib.error.HTTPError as e2:
                if e2.code != 404:
                    print(f"Tombstone failed for {label}: HTTP {e2.code}")
        elif e.code != 404:  # 404: deleted by hand, fine
            print(f"Tombstone failed for {label}: HTTP {e.code}")
    time.sleep(1)


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


def webhook_post_with_file(url: str, payload: dict, filename: str,
                           filebytes: bytes) -> dict:
    boundary = f"----raffle{uuid.uuid4().hex}"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; '
        f'name="payload_json"\r\nContent-Type: application/json\r\n\r\n'
        .encode() + json.dumps(payload).encode("utf-8") + b"\r\n",
        f'--{boundary}\r\nContent-Disposition: form-data; '
        f'name="files[0]"; filename="{filename}"\r\n'
        f'Content-Type: image/png\r\n\r\n'.encode() + filebytes + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    req = urllib.request.Request(
        url,
        data=b"".join(parts),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": USER_AGENT,
        },
        method="POST",
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
            post_url = webhook + "?with_components=true&wait=true"
            try:
                try:
                    png = compose_strip(raffle["item_data"], raffle["items"])
                    msg = webhook_post_with_file(
                        post_url, build_live_message(raffle, strip=True),
                        "items.png", png)
                except Exception as e:
                    # Strip failed (missing Pillow, CDN hiccup, Discord
                    # rejecting the upload): degrade to the plain mosaic.
                    print(f"Strip failed for {rid} ({e}), using mosaic.")
                    msg = webhook_request(post_url, build_live_message(raffle))
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
                tombstone_message(webhook, entry["msg"], rid)
            else:
                print(f"[dry run] tombstone: {rid}")
            entry["ended"] = True
            tombstoned += 1

    # Megaraffle: one rolling raffle on its own page. Its end time is known,
    # so skip fetching the page entirely until the end draws near — saves a
    # request per run. Never let its failure break raffle notifications.
    tracked_mega = state.get("megaraffle")
    mega_fetch_needed = (
        not tracked_mega
        or tracked_mega.get("ended")
        or not tracked_mega.get("end")
        or tracked_mega["end"] <= now + MEGA_FETCH_MARGIN
    )
    mega = None
    if mega_fetch_needed:
        try:
            time.sleep(1)  # small gap between the two page fetches
            mega = extract_megaraffle(fetch_page(MEGARAFFLE_URL))
        except Exception as e:
            print(f"Megaraffle check failed: {e}")
    if mega:
        entry = state.get("megaraffle") or {}
        if first_run and not entry:
            # Seed silently, same as raffles on a fresh state.
            state["megaraffle"] = {"num": mega["num"], "end": mega["end"],
                                   "msg": None, "ended": False}
        elif entry.get("num") != mega["num"]:
            # Number rolled over: previous megaraffle finished.
            if entry.get("msg") and not entry.get("ended"):
                if webhook:
                    tombstone_message(webhook, entry["msg"],
                                      f"megaraffle #{entry.get('num')}")
                else:
                    print(f"[dry run] tombstone: megaraffle #{entry.get('num')}")
                tombstoned += 1
            new_entry = {"num": mega["num"], "end": mega["end"],
                         "msg": None, "ended": False}
            if webhook:
                try:
                    msg = webhook_request(
                        webhook + "?with_components=true&wait=true",
                        build_mega_message(mega["num"], mega["end"]))
                    new_entry["msg"] = msg.get("id")
                    posted += 1
                    time.sleep(1)
                except urllib.error.HTTPError as e:
                    print(f"Post failed for megaraffle: HTTP {e.code}")
            else:
                print(f"[dry run] new megaraffle #{mega['num']}")
            state["megaraffle"] = new_entry
        elif (not entry.get("ended") and entry.get("msg")
                and entry.get("end") and entry["end"] <= now):
            if webhook:
                tombstone_message(webhook, entry["msg"],
                                  f"megaraffle #{entry['num']}")
            else:
                print(f"[dry run] tombstone: megaraffle #{entry['num']}")
            entry["ended"] = True
            tombstoned += 1

    save_state(state, on_page={r["id"] for r in raffles})
    if first_run:
        print(f"Seeded {len(raffles)} existing raffles, nothing posted.")
    else:
        print(f"{len(raffles)} raffles on page, {posted} posted, "
              f"{tombstoned} tombstoned.")


if __name__ == "__main__":
    main()
