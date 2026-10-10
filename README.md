# SMS Academic Assistant

> Connect your university once, then text one phone number for your entire academic life.

A texting assistant for university students. It starts with Ontario universities on D2L Brightspace. Students text one shared number to ask what's due, get reminders and morning briefings, track grades, and ask about their syllabi. There's no app and no dashboard: the product is the text conversation. The only web page is a one-time "connect your school" link.

The assistant runs on a reliable academic data layer (LMS feeds, syllabi, and what students tell it). The LLM is only the conversational interface. It never invents deadlines, grades, or weights.

## Try it locally (fake phone)

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
run_dev.bat
```

`run_dev.bat` starts the server and turns the terminal into a phone:

```
you > hi
📱 Assistant: Hey 👋 I'm your school assistant. …
you > yes
you > laurier
📱 Assistant: Tap this link to connect MyLearningSpace: http://localhost:8000/s/…
```

- Open the link and paste your Brightspace calendar **Subscribe** link. You can also paste that link straight into the fake phone.
- After you connect, "Connected ✅ …" arrives in the phone. Then ask "what's due this week".
- Try "remind me in 2 minutes to stretch" or "move my morning text to 7:30". Reminders pop into the phone on their own.
- The AI needs a free key. Paste a Gemini key from https://aistudio.google.com/apikey into `.env` or `prototype/.env` as `LLM_API_KEY=…`.
- `/briefing` shows today's morning text now, `/tick` runs reminders and syncs without waiting, `/reset` starts sign-up over, and `/quit` exits.
- If it says your local database is from an older version, delete `data/app.db` (it only holds local test data).

Tests: `.venv\Scripts\python -m pytest`

## What students get

- **Sign-up by text:** consent, school, then a one-time link to connect their Brightspace calendar (or they paste the link into the thread).
- **Ask anything:** "what's due this week", "when's my econ midterm", "I finished BU111", "what should I work on". Answers come only from their calendar data.
- **Morning text** at 8 AM (or the time they set) with what's due today and in the next 2 days, plus anything overdue.
- **Due-soon heads-up** 3 hours before each unfinished deadline (they can change the timing or turn it off).
- **Change alerts** when a deadline is added or moved. Calendars re-sync every 3 hours.
- **Their own reminders:** "remind me at 8 to start the essay".
- **Commands:** HELP, STOP / START, LINK (reconnect the calendar), DELETE MY ACCOUNT (asks for a code, then erases everything).
- **Limits:** at most 6 automatic texts a day; heads-ups and alerts wait out 10 PM to 8 AM.

## Deploy (Railway + Twilio)

One Railway service runs everything: the web app, the Twilio webhook, and a background thread for morning texts, reminders and syncs. `railway.json` runs the database migrations before each start.

1. **Twilio:** create an account and buy a number. A trial account can only text numbers you verify in the console, and every text starts with a trial notice, which is fine for a few testers. To open it to more students, upgrade and submit toll-free verification (it takes days to 2 weeks, so start early).
2. **Railway:** create a project from this GitHub repo and add a **Postgres** database to it.
3. **Variables:** on the app service, set these (see `.env.example`):
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}`
   - `ENCRYPTION_KEY`: generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and keep a copy somewhere safe
   - `PUBLIC_BASE_URL`: the service's public `https://…up.railway.app` address (Settings → Networking → Generate domain)
   - `RUN_SCHEDULER=true`
   - `LLM_API_KEY` (Gemini; turn on billing so student data isn't used for training)
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`
4. **Point Twilio at the app:** in the Twilio console, under your number → Messaging → "A message comes in", set Webhook, `POST`, `https://<your-app>/webhooks/twilio/sms`.
5. Text "hi" to the number from your phone.

Keep the app at **one replica**: the scheduler thread isn't meant to run twice.

**Testing real texts from your laptop first:** run `ngrok http 8000`. Then start the app with `PUBLIC_BASE_URL` set to the ngrok address, `RUN_SCHEDULER=true` and the Twilio variables in `.env` (`.venv\Scripts\uvicorn app.main:app`), and point the Twilio webhook at `https://<ngrok-address>/webhooks/twilio/sms`.

## What's in the repo

| Path | What |
|---|---|
| `app/` | The texting assistant: sign-up by text, connect page, Brightspace sync, AI brain with tools and a date grounding check, scheduler, Twilio webhooks |
| `migrations/` | Alembic migrations for the deployed Postgres database |
| `docs/architecture.md` | Approved architecture and roadmap |
| `spikes/brightspace_ical/` | Phase 0 calendar-feed spike: daily snapshots, auto-analysis, findings |
| `spikes/brightspace_email/` | Notification-email spike (waiting on sample emails) |
| `prototype/` | Earlier local chat prototype |
| `design/` | Clickable setup-flow mockup |

## Status

- ✅ Phase 0: Brightspace calendar feeds work at Waterloo and Laurier. They're incomplete, though: some courses post nothing, and there are no exams. See `spikes/brightspace_ical/findings/FINDINGS.md`.
- ✅ Local assistant: sign-up by text, connect page, real sync, and AI answers, tested with a fake phone.
- ✅ MVP: morning texts, due-soon heads-ups, change alerts, custom reminders, settings by text, account deletion, Twilio texting, and a Railway deploy setup.
- ⏭️ Next: deploy, then a closed beta with about 20 students. After that: syllabus upload and grades.
