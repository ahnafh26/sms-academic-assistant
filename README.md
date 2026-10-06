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
- The AI needs a free key. Paste a Gemini key from https://aistudio.google.com/apikey into `.env` or `prototype/.env` as `LLM_API_KEY=…`.
- `/reset` starts sign-up over, and `/quit` exits.

Tests: `.venv\Scripts\python -m pytest`

## What's in the repo

| Path | What |
|---|---|
| `app/` | The texting assistant: sign-up by text, connect page, Brightspace sync, AI brain with tools and a date grounding check |
| `docs/architecture.md` | Approved architecture and roadmap |
| `spikes/brightspace_ical/` | Phase 0 calendar-feed spike: daily snapshots, auto-analysis, findings |
| `spikes/brightspace_email/` | Notification-email spike (waiting on sample emails) |
| `prototype/` | Earlier local chat prototype |
| `design/` | Clickable setup-flow mockup |

## Status

- ✅ Phase 0: Brightspace calendar feeds work at Waterloo and Laurier. They're incomplete, though: some courses post nothing, and there are no exams. See `spikes/brightspace_ical/findings/FINDINGS.md`.
- ✅ Local assistant: sign-up by text, connect page, real sync, and AI answers, tested with a fake phone.
- ⏭️ Next: real texting with Twilio, background syncs, change alerts, morning texts and reminders, then deployment.
