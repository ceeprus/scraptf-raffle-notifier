# scraptf-raffle-notifier

Watches [scrap.tf](https://scrap.tf) raffles and posts them to a Discord
channel via webhook. Runs on GitHub Actions — no bot, no server.

For each new raffle it posts a Components V2 message: clickable title, live
countdown, a composed PNG strip of the item icons (quality-colored borders,
`+N` badge when the raffle holds more than fits), and an enter link. Big
raffles (10+ items) get a gold accent, normal ones green. The megaraffle is
watched too and posts in purple. When a raffle ends, its message is edited
into a gray `🏁 Raffle ended` tombstone.

## Setup

1. Create a Discord webhook: channel settings → Integrations → Webhooks →
   New Webhook → Copy URL. Keep the URL private.
2. In this repo: Settings → Secrets and variables → Actions →
   New repository secret → name `DISCORD_WEBHOOK_URL`, value = the webhook URL.
3. Actions tab → `raffle-notify` → Run workflow. The first run seeds state and
   posts nothing.

## Scheduling

The workflow has a `*/5` cron, but GitHub delays or skips cron on young/quiet
repos. For reliable 5-minute polling this repo is triggered externally: a
[cron-job.org](https://cron-job.org) job POSTs to
`https://api.github.com/repos/<owner>/<repo>/actions/workflows/notify.yml/dispatches`
every 5 minutes with a fine-grained PAT (this repo only, Actions read+write).
Both triggers can coexist — a concurrency group serializes runs.

## How it works

- `notifier.py` (stdlib + Pillow) scrapes the public raffle list: id, title,
  end timestamp, item count, and item icon URLs with quality tiers.
- New raffles are posted with `?with_components=true&wait=true`; the returned
  message id is stored in `seen.json` so the message can be edited later.
- The item strip is composed with Pillow and uploaded as a multipart
  attachment referenced by the message's media gallery. If composition fails
  for any reason, the message falls back to a plain image mosaic.
- Each run also checks tracked raffles: past their end time, the stored
  message is edited into the tombstone (and its attachment removed).
- State lives in `seen.json`, committed back by the workflow. Ended entries
  are pruned a week after their end time, so the file stays small.
- Without `DISCORD_WEBHOOK_URL` set, actions are printed instead (dry run).

## Notes

- Item names and puzzle raffles are not available without a Steam login, so
  messages show item images and counts only, and only public raffles are
  watched.
- The raffle list shows at most 50 items per raffle, so the item count of
  bigger raffles reads as 50.
- If runs fail with `Parsed 0 raffles`, the page layout changed and the
  parser regexes need updating.
