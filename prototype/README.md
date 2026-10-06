# Local chat prototype

This is a terminal version of the SMS assistant. You type what a student would text, and it answers from your real Brightspace deadlines (the daily spike snapshots). It's for testing the conversation and tool design before Twilio and hosting.

```
you > what's due this week
bot > You've got 4 things this week, all EC120:
      • Ch. 5 EOC Problems — Tue 10 AM
      • Ch. 7 Podcast Assignment — Tue 10 AM
      • Ch. 6 EOC Problems — Thu 10 AM
      • Ch. 8 Decision Point — Thu 10 AM
you > anything for cs115?
bot > CS115 doesn't publish deadlines to your LEARN calendar. Check the course outline or course site.
```

## Setup (free)

1. **Get a free API key.** The default is Google Gemini:
   - go to https://aistudio.google.com/apikey, sign in with a Google account, and click **Create API key**
   - free tier: rate-limited, and Google may use free-tier prompts to improve its models. That's fine for testing with your own data, but not for real students later.
2. **Paste it into `prototype/.env`** (already created, and gitignored):
   ```
   LLM_PROVIDER=gemini
   LLM_API_KEY=<your key>
   ```
3. **Run it:** double-click `prototype/chat.bat`, or:
   ```
   cd prototype
   .venv\Scripts\python -m chat
   ```

Other free options (set `LLM_PROVIDER` in `.env`):

| Provider | Key | Notes |
|---|---|---|
| `gemini` (default) | https://aistudio.google.com/apikey | Best tool calling of the free options |
| `groq` | https://console.groq.com/keys | Very fast; free tier is rate-limited |
| `ollama` | none | Fully offline. Install https://ollama.com, run `ollama pull qwen2.5:7b`. Slower on a 4 GB GPU, and small models follow tool instructions less reliably |

Model names change over time. If you get a "model not found" error, set `LLM_MODEL=` to a current model from the provider's list.

## Using it

- Ask naturally: "what's due today", "what about tomorrow", "what's next", "anything bad this week", "what's due for BU111", "what exams do I have", "what's my busiest day", "I finished econ", "CS will take 3 hours", "what should I work on rn", "refresh".
- `/debug` shows every tool call and result, which is useful for seeing *why* it answered something.
- `/reset` forgets the conversation. `/quit` exits.
- `--now 2026-10-14T18:00` replays as if it were another time.

Done marks and time estimates are saved in `prototype/state.json` (gitignored). Brightspace re-syncs never overwrite them.

## How it stays accurate

- The assistant can only see the spike's cleaned items: real deliverables and exams from this term. Maintenance windows, residence events and stale copied-course dates are filtered out.
- The LLM never computes dates. Tools resolve "today", "this week" and "before Friday", and return preformatted times.
- **Grounding check:** if a reply mentions a date, time or weekday that no tool returned, the bot retries once and then falls back to "couldn't double-check that". It doesn't send an invented deadline.
- Ambiguous requests ("mark econ done" when two econ items are due soon) make the bot ask instead of guessing.

Not in this version: reminders, morning briefings, syllabus and grade questions. Those come with the SMS MVP.

## Tests

```
cd prototype
.venv\Scripts\python -m pytest
```

The tests use a scripted fake LLM, so no key or network is needed.
