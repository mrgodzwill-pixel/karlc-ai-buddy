# Karl C AI Buddy

A 24/7 AI-powered personal assistant for managing the "Karl C" Facebook Page, connected via Telegram.

## Features

- **Facebook DM Handler**: AI-powered auto-reply for student inquiries
- **Comment Monitoring**: Compiles comments and suggests keyword-based replies
- **Student Ticket System**: Tracks enrollment issues with pending/done states
- **Enrollment Checker**: Compares Xendit payments vs Systeme.io enrollments
- **Telegram Bot**: Full command interface + natural language AI chat
- **Scheduled Reports**: 7AM & 7PM daily reports via Telegram

## Architecture

```
Facebook Page DMs → Webhook Server → AI Buddy → Telegram Notification
                                   → Gmail Check → Ticket Creation
Telegram Commands → Bot Listener → Command Handler → Response
Scheduler → 7AM/7PM → Report Generator → Telegram
```

## Deployment

### Railway (Recommended)
1. Connect this repo to Railway
2. Set environment variables (see `.env.example`)
3. Deploy!

### Render
1. Connect this repo to Render
2. Set environment variables
3. Deploy!

### Docker
```bash
docker build -t karlc-ai-buddy .
docker run -p 5000:5000 --env-file .env karlc-ai-buddy
```

## Environment Variables

See `.env.example` for all required variables.

## Telegram Commands

| Command | Action |
|---------|--------|
| `/help` | Show all commands |
| `/report` | Generate report now |
| `/tickets` | View pending tickets |
| `/done 1` | Resolve ticket #1 |
| `/enrollment` | Run enrollment check |
| `/approve_all` | Approve all suggested replies |
| `/status` | Check agent status |
| Or just chat naturally! | AI-powered conversation |

## License

Private - Karl C
