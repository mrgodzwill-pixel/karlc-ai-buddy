# Karl C AI Buddy

A 24/7 AI-powered personal assistant for managing the "Karl C" Facebook Page, connected via Telegram.

## Features

- **Facebook DM Handler**: Gemini AI-powered auto-reply for student inquiries
- **Comment Monitoring**: Compiles comments and suggests keyword-based replies
- **Student Ticket System**: Tracks enrollment issues with pending/done states
- **Enrollment Checker**: Compares Xendit payments vs Systeme.io enrollments (requires Gmail IMAP)
- **SMS Follow-ups**: Sends manual unresolved-ticket follow-ups via Semaphore
- **Telegram Bot**: Full command interface + natural language Gemini AI chat
- **Scheduled Reports**: 7AM & 7PM Philippine Time, daily, via Telegram

## Architecture

```
Facebook Page DMs → Webhook (signed) → Background Worker → Telegram Notification
                                     → Gmail Check → Ticket Creation
Telegram Commands → Bot Listener → Command Handler → Response
Scheduler (PHT)   → 7AM/7PM     → Report Generator → Telegram
```

## Security features

- **HMAC webhook verification** — every Facebook POST is checked against `FB_APP_SECRET`.
- **Idempotent webhook** — duplicate Facebook message IDs are skipped.
- **Debug endpoints gated by `ADMIN_TOKEN`** — `/messages` is disabled unless a token is set.
- **Non-blocking webhook** — incoming DMs return 200 immediately and process in a background thread.
- **Conversation state TTL** — stale `waiting_email` states auto-reset after 1 hour.

## ⚠️ Important: data persistence

State (tickets, conversations, stored DMs, replied-comment IDs) lives in JSON files under `DATA_DIR`.

On **Railway**, **Render**, and default **Docker**, the filesystem is ephemeral — every redeploy wipes state. To keep data across deploys you **must** mount a persistent volume at `DATA_DIR`:

- Railway: add a volume, set `DATA_DIR=/data`.
- Render: add a disk, set `DATA_DIR=/var/data`.
- Docker: `-v karlc_data:/app/data`.

If you don't do this, Karl will lose ticket history, the bot will re-reply to old comments, and the `waiting_email` state of active conversations will be lost on each deploy.

## Deployment

### Railway (recommended)
1. Connect this repo to Railway.
2. Add a persistent volume (set `DATA_DIR` to the mount path).
3. Set all environment variables (see `.env.example`).
4. Deploy.

### Render
1. Connect this repo to Render.
2. Add a disk (set `DATA_DIR` to the mount path).
3. Set environment variables.
4. Deploy.

### Docker
```bash
docker build -t karlc-ai-buddy .
docker run -p 5000:5000 -v karlc_data:/app/data --env-file .env karlc-ai-buddy
```

## Environment variables

See `.env.example`. Required at minimum:

- `FB_PAGE_ID`, `FB_PAGE_ACCESS_TOKEN`, `FB_APP_SECRET`, `FB_VERIFY_TOKEN`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `GEMINI_API_KEY`

If any of these are missing, the app will log a warning on startup.

Optional for SMS follow-ups:

- `SEMAPHORE_API_KEY`
- `SEMAPHORE_SENDER_NAME` (optional)

## Telegram commands

| Command | Action |
|---------|--------|
| `/help` | Show all commands |
| `/report` | Generate report now |
| `/tickets` | View pending tickets |
| `/done 1` | Resolve ticket #1 |
| `/follow 12 \| Juan Dela Cruz \| 09171234567` | Send SMS follow-up for ticket #12 |
| `/enrollment` | Run enrollment check (requires Gmail IMAP env vars) |
| `/approve_all` | Approve all suggested replies |
| `/status` | Check agent status |
| Or just chat naturally! | AI-powered conversation |

## Known limitations

- **Enrollment checker** reads Gmail over IMAP. Set `GMAIL_USER` and `GMAIL_APP_PASSWORD` (a 16-char Google App Password, not your regular password — generate one at https://myaccount.google.com/apppasswords after turning on 2-Step Verification). If either env var is unset, the `/enrollment` command reports that Gmail is unavailable and skips the check.
- **No database** — JSON files only. Fine for Karl's volume, but see the data persistence warning above.
- **Single process** — this is not built to scale horizontally.

## License

Private - Karl C
