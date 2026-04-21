# Karl C AI Buddy

A 24/7 AI-powered personal assistant for managing the "Karl C" Facebook Page, connected via Telegram.

## Features

- **Facebook DM Handler**: Gemini AI-powered auto-reply for student inquiries
- **Comment Monitoring**: Compiles comments and suggests keyword-based replies
- **Student Ticket System**: Tracks enrollment issues with pending/done states
- **Enrollment Checker**: Compares Xendit payments vs Systeme.io enrollments using Xendit API when configured, with Gmail fallback
- **Local Xendit Store**: Persists Xendit invoice/payment webhook and sync data to JSON for reuse
- **Xendit Webhooks**: Accepts legacy invoice callbacks and new Payments API callbacks
- **SMS Follow-ups**: Sends manual unresolved-ticket follow-ups via Semaphore
- **Support Inbox Watcher**: Monitors emails sent to the support mailbox and alerts Karl in Telegram
- **Telegram Bot**: Full command interface + natural language Gemini AI chat
- **Scheduled Reports**: 7AM & 7PM Philippine Time, daily, via Telegram

## Architecture

```
Facebook Page DMs        → Webhook (signed)         → Background Worker → Telegram Notification
Xendit Invoice Webhooks  → /webhook/xendit/invoice  → Local Payment Store
Xendit Payment Webhooks  → /webhook/xendit/payment  → Customer Enrichment → Local Payment Store
Telegram Commands        → Bot Listener             → Command Handler     → Response
Scheduler (PHT)          → 7AM/7PM                  → Report Generator    → Telegram
```

## Security features

- **HMAC webhook verification** — every Facebook POST is checked against `FB_APP_SECRET`.
- **Idempotent webhook** — duplicate Facebook message IDs are skipped.
- **Debug endpoints gated by `ADMIN_TOKEN`** — `/messages` is disabled unless a token is set.
- **Non-blocking webhook** — incoming DMs return 200 immediately and process in a background thread.
- **Conversation state TTL** — stale `waiting_email` states auto-reset after 1 hour.

## ⚠️ Important: data persistence

State (tickets, conversations, stored DMs, replied-comment IDs) lives in JSON files under `DATA_DIR`.
That now includes the local `xendit_payments.json` store used for payment lookups and manual verification.

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
- `SUPPORT_EMAIL` (optional; defaults to `course@karlcomboy.com`)

Optional for direct Systeme.io API backfill:

- `SYSTEME_API_KEY` for historical contacts/enrollment import
- `SYSTEME_API_BASE_URL` if you need to override the default API host

Optional for direct Xendit integration:

- `XENDIT_SECRET_KEY` for API sync and customer enrichment
- `XENDIT_INVOICE_WEBHOOK_TOKEN` for legacy invoice/payment-link callbacks
- `XENDIT_PAYMENT_WEBHOOK_TOKEN` for Payments API callbacks
- `XENDIT_WEBHOOK_TOKEN` as a shared fallback if both webhook types use the same token

## Xendit setup

Register these URLs in Xendit Dashboard / Webhook Settings:

- Legacy invoice / payment link callback: `/webhook/xendit/invoice`
- Payments API callback: `/webhook/xendit/payment`

The app verifies `x-callback-token` against your configured webhook token and stores webhook data in `xendit_payments.json`.

## Telegram commands

| Command | Action |
|---------|--------|
| `/help` | Show all commands |
| `/report` | Generate report now |
| `/tickets` | View pending tickets |
| `/done 1` | Resolve ticket #1 |
| `/follow 12 \| Juan Dela Cruz \| 09171234567` | Send SMS follow-up for ticket #12 |
| `/support` | Show recent emails sent to the support inbox |
| `/enrollment` | Run enrollment check (prefers Xendit API, falls back to Gmail IMAP) |
| `/systeme_sync` | Import older enrolled students from Systeme.io Public API |
| `/systeme_add 12` | Create a Systeme contact from ticket #12 |
| `/systeme_enroll 12` | Create/add contact then enroll ticket #12 in Systeme |
| `/approve_all` | Approve all suggested replies |
| `/status` | Check agent status |
| Or just chat naturally! | AI-powered conversation |

Natural-language payment lookups are supported in Telegram chat, for example:

- `May payment ba si Juan Dela Cruz?`
- `Check payment for juan@example.com`
- `Hanapin mo yung payment ng 09171234567`

## Known limitations

- **Legacy invoice callbacks** from Xendit reliably include invoice status, amount, payer email, payment method/channel, and invoice/payment IDs. According to Xendit’s legacy invoice webhook docs, they do not guarantee payer full name or phone number in that payload.
- **Full name and phone number** are only guaranteed when your Xendit flow provides customer data, such as Payments API callbacks with `customer_id` plus a retrievable customer object, or when your own checkout flow already sends those customer fields to Xendit.
- **Gmail fallback** is still useful if your current Xendit setup is email-heavy and not all payment flows are wired to webhooks yet. Set `GMAIL_USER` and `GMAIL_APP_PASSWORD` (a 16-char Google App Password, not your regular password — generate one at https://myaccount.google.com/apppasswords after turning on 2-Step Verification).
- **No database** — JSON files only. Fine for Karl's volume, but see the data persistence warning above.
- **Single process** — this is not built to scale horizontally.

## License

Private - Karl C
