# scraptf-raffle-notifier

Polls [scrap.tf/raffles](https://scrap.tf/raffles) every 5 minutes with GitHub
Actions and posts new raffle links to a Discord channel via webhook. No bot to
host, no server to run.

## Setup

1. Create a Discord webhook: channel settings → Integrations → Webhooks →
   New Webhook → Copy URL. Keep the URL private.
2. In this repo: Settings → Secrets and variables → Actions →
   New repository secret → name `DISCORD_WEBHOOK_URL`, value = the webhook URL.
3. Actions tab → `raffle-notify` → Run workflow. The first run seeds state and
   posts nothing; after that it runs every ~5 minutes on its own.

## How it works

- `notifier.py` fetches the raffle list, extracts raffle ids, and compares them
  against `seen.json`. New ids are posted to the webhook, then committed back
  to `seen.json` by the workflow.
- Without `DISCORD_WEBHOOK_URL` set, new raffles are printed in the run log
  instead of posted (dry run).
- Delete `seen.json` to re-seed: the next run marks all current raffles as
  seen without posting.

## Notes

- Scheduled runs can lag a few minutes — GitHub queues cron jobs, exact
  5-minute spacing is not guaranteed.
- GitHub disables the schedule after 60 days without repo activity. The
  workflow's own `seen.json` commits count as activity; if it ever gets
  disabled, one click in the Actions tab re-enables it.
- If runs start failing with `Parsed 0 raffle ids`, the page layout changed
  and the parser needs updating.
