# Shakopee Community Center Hours Bot (Discord Edition)

A small Python automation that checks the **official City of Shakopee Community Center page** every morning and posts the latest hours to a Discord channel via an incoming webhook.

Official source: <https://www.shakopeemn.gov/recreation/community_center/index.php>

## What it does

- Fetches the official Community Center webpage.
- Parses the normal weekly schedule.
- Gives **date-specific modified/holiday hours priority** over normal hours.
- Detects explicit dated closure ranges (for example, maintenance-week closures).
- Fails safely if the site changes and the normal schedule can no longer be parsed confidently.
- Posts a formatted Discord embed every day by default.
- Tracks the last successful result in `state.json` and can optionally post only when hours change.
- Runs automatically with GitHub Actions; no always-on server or bot process is required.

Discord was chosen over SMS/Twilio here because a webhook is free, requires no phone number or ongoing per-message cost, and this repo can grow into a multi-user bot (a shared `#shakopee-hours` channel) rather than a single personal notifier.

## Architecture

```text
Shakopee City Website
        ↓
   Python scraper
        ↓
Hours + holiday detection
        ↓
Change detection
        ↓
GitHub Actions
        ↓
Discord Webhook
        ↓
#shakopee-hours
        ↓
📱 Discord notification
```

## Repository layout

```text
shakopee-hours-bot/
├── .github/
│   └── workflows/
│       └── daily.yml
├── tests/
│   └── test_scraper.py
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
├── scraper.py
└── state.json
```

## 1. Create a Discord webhook

In the Discord channel you want notifications in (e.g. `#shakopee-hours`):

1. **Channel Settings → Integrations → Webhooks → New Webhook**
2. Name it something like "Shakopee Hours Bot" and optionally give it an avatar.
3. Click **Copy Webhook URL**. This is your `DISCORD_WEBHOOK_URL` — treat it like a password; anyone with the URL can post to that channel.

No Discord application, bot token, or always-on process is needed for this version.

## 2. Test locally

Python 3.12 is recommended.

```bash
python -m venv .venv
```

Activate it:

**macOS/Linux**

```bash
source .venv/bin/activate
```

**Windows PowerShell**

```powershell
.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Set the environment variables.

**macOS/Linux**

```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
export SEND_MODE="daily"
python scraper.py
```

**Windows PowerShell**

```powershell
$env:DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
$env:SEND_MODE="daily"
python scraper.py
```

Run tests with:

```bash
python -m unittest discover -s tests -v
```

## 3. Push to GitHub

Create a new GitHub repository, then from this project folder:

```bash
git init
git add .
git commit -m "Initial Shakopee hours bot (Discord)"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/shakopee-hours-bot.git
git push -u origin main
```

## 4. Add GitHub Actions secrets and variables

In your repository, go to:

**Settings → Secrets and variables → Actions → Secrets → New repository secret**

Create this secret:

| Secret | Example |
|---|---|
| `DISCORD_WEBHOOK_URL` | `https://discord.com/api/webhooks/123456789012345678/abcDEF...` |

Never commit this value to the repository.

Optionally, under **Settings → Secrets and variables → Actions → Variables → New repository variable**, you can set:

| Variable | Purpose | Default |
|---|---|---|
| `SEND_MODE` | `daily` or `changes_only` | `daily` |
| `DISCORD_USERNAME` | Display name for the webhook message | `Shakopee Hours Bot` |
| `DISCORD_AVATAR_URL` | Avatar image URL for the webhook message | (Discord webhook default) |
| `DISCORD_PING_ON_ALERT` | Mention text added only to modified/closed alerts, e.g. `@here` or `<@&ROLE_ID>` | (none — never pings) |

## 5. Daily vs change-only posts

By default the workflow uses:

```text
SEND_MODE=daily
```

That means the bot posts to Discord every time the scheduled workflow successfully verifies the hours. This is the recommended starting point, since it makes it easy to confirm the whole pipeline (scrape → parse → webhook) is actually working every morning.

Once you've verified it for a few days, you can switch to posting **only when the announced hours/status changes** by setting the `SEND_MODE` repository variable to:

```text
changes_only
```

The state file is committed back to the repository after each successful run so future workflow runs have durable history to compare against.

## 6. Schedule

The included workflow runs at:

```yaml
- cron: "0 12 * * *"
```

GitHub Actions cron is UTC. `12:00 UTC` is:

- 6:00 AM in Minnesota during Central Standard Time
- 7:00 AM in Minnesota during Central Daylight Time

For most personal uses, that is simple and reliable. If you require exactly 6:00 AM local time year-round, use a timezone-aware external scheduler or two seasonal cron rules with date/offset logic.

You can also trigger the bot manually from:

**GitHub → Actions → Daily Shakopee Community Center Hours → Run workflow**

## How special-hour detection works

The parser has a priority order:

1. **Modified / holiday / special / adjusted hours** with an explicit calendar date.
2. Explicit dated closure ranges, such as a maintenance closure.
3. Normal weekly schedule.

For example, if the page says both:

```text
Sunday: 8 a.m. to 6 p.m.
```

and a special announcement says:

```text
Sunday, Sep. 6 8:00am-5:00pm
```

then the bot uses `8:00am-5:00pm` for that date.

## Safe failure behavior

Scraping municipal websites is inherently brittle because their HTML can change without notice.

This bot deliberately requires all seven normal weekday entries to be parseable. If that confidence check fails, it does **not** invent or reuse hours as if they were current. Instead it posts an error embed to Discord saying the hours could not be verified and to check the city website manually. It also leaves the previous `state.json` untouched after a failed scrape.

## Example Discord messages

Normal day (green embed):

```text
🏊 Shakopee Community Center

📅 Monday, September 8

🕐 Today's Hours
5 AM – 8 PM

🔗 Official Source
ShakopeeMN.gov
```

Modified day (yellow embed):

```text
🏊 Shakopee Community Center

📅 Sunday, September 6

🕐 Today's Hours
8:00 AM – 5:00 PM

⚠️ MODIFIED HOURS
Labor Day Weekend Modified Hours

🔗 Official Source
ShakopeeMN.gov
```

Closed day (red embed):

```text
🏊 Shakopee Community Center

📅 Monday, September 7

🕐 Today's Hours
🚫 CLOSED today

🚨 CLOSED
Labor Day Weekend Modified Hours

🔗 Official Source
ShakopeeMN.gov
```

## Future upgrades

Good next additions would be:

- Move from a plain webhook to a full Discord bot (slash commands like `/hours today`, per-server configuration) if this becomes multi-user.
- Monitor the Shakopee Ice Arena and SandVenture as separate facilities, each with its own channel or embed section.
- Add an `OPEN / CLOSED / CLOSES IN X HOURS` API endpoint.
- Persist historical changes in SQLite/PostgreSQL instead of Git.
- Expose a small status page showing today's verified hours and when the source was last checked.
- Add a second-source sanity check if the City begins publishing facility alerts elsewhere.

## Disclaimer

This is an unofficial personal automation. The City of Shakopee website remains the source of truth. Website content and HTML can change, so treat automated notifications as a convenience rather than a guarantee.
