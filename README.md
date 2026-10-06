# SMS Academic Assistant

> Connect your university once, then text one phone number for your entire academic life.

An SMS-first academic assistant for university students. It starts with Ontario universities on D2L Brightspace. Students text one shared number to ask what's due, get reminders and morning briefings, track grades, and query their syllabi. There's no student dashboard.

The assistant is built on a reliable academic data layer (LMS feeds, syllabi, and what students tell it). The LLM is only the conversational interface. It never invents deadlines, grades, or weights.

## Status

**Phase 0: Brightspace iCal viability spike.** We're testing whether Brightspace calendar feeds from Ontario universities expose enough reliable data (courses, deadlines, stable IDs) to drive the product without university partnerships.

- Architecture: [docs/architecture.md](docs/architecture.md)
- Spike: `spikes/brightspace_ical/` (daily snapshots + auto-analysis)
- Local chat prototype: [prototype/](prototype/README.md). Chat with the assistant on your real deadlines (free LLM)
