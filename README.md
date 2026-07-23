# scraptf-raffle-notifier

Polls [scrap.tf/raffles](https://scrap.tf/raffles) every 5 minutes and posts new
raffle links to a Discord channel via webhook. No bot, no server — runs free on
GitHub Actions (public repo = unlimited minutes).

## Setup (GitHub Actions)

1. Create a Discord webhook: channel settings → Integrations → Webhooks →
   New Webhook → Copy URL. Keep the URL private.
2. Create a **public** GitHub repo and push this folder to it.
3. In the repo: Settings → Secrets and variables → Actions →
   New repository secret → name `DISCORD_WEBHOOK_URL`, value = the webhook URL.
4. Actions tab → `raffle-notify` → Run workflow (first run seeds state,
   posts nothing). After that it runs every ~5 minutes on its own.

If a run fails with a Cloudflare-challenge error, GitHub's datacenter IPs are
being blocked by scrap.tf — fall back to running it on home hardware (below).

Note: GitHub disables the schedule after 60 days without repo activity. The
workflow's own `seen.json` commits count as activity, so during active raffle
periods it keeps itself alive; if it ever gets disabled, one click in the
Actions tab re-enables it.

## Running on a Pi / any machine instead

```
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." python3 notifier.py
```

Cron every 5 minutes (`crontab -e`):

```
*/5 * * * * DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." /usr/bin/python3 /home/pi/scraptf-raffle-notifier/notifier.py >> /home/pi/raffle.log 2>&1
```

State is stored in `seen.json` next to the script. Delete it to re-seed
(current raffles are marked seen without posting).

Without `DISCORD_WEBHOOK_URL` set, the script prints new raffles instead of
posting them (dry run).
