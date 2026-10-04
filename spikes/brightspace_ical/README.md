# Brightspace iCal viability spike (Phase 0)

This is throwaway spike code. Its job is to answer one question: **do Brightspace calendar (iCal) feeds from Ontario universities expose enough reliable information (courses, deadlines, types, stable IDs) to power the SMS assistant without university partnerships?**

Pipeline: real Brightspace feed → snapshot → parsed events → normalized JSON → inspection report + diff against the previous snapshot. Findings go in [findings/FINDINGS.md](findings/FINDINGS.md).

## Setup

```bash
cd spikes/brightspace_ical
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux
cp feeds.local.example.yaml feeds.local.yaml               # then paste your feed URL(s)
.venv/Scripts/python -m spike list
.venv/Scripts/python -m pytest
```

## Getting a feed URL (any Brightspace school)

1. Log in to the school's Brightspace (LEARN, MyLearningSpace, Avenue, CourseLink, ...).
2. Open **Calendar**. It's usually in the top navbar on the homepage, or under a course's navbar (sometimes inside "Course Tools" or "Resources"). The homepage calendar widget may have a "Go to calendar" link.
3. Look for **Subscribe**. It's usually a link or icon near the top-right of the calendar view, and sometimes it's inside a ⋯ menu.
   - If you can't see it, open the calendar's **Settings** (often a link at the bottom of the calendar page) and look for **"Enable Calendar Feeds"**. Tick it, save, and look again.
4. In the subscribe dialog, pick **All Courses** if it asks, then copy the URL. It usually looks like `https://<host>/d2l/le/calendar/feed/user/feed.ics?token=…`.
5. Paste it into `feeds.local.yaml` under the matching `institution` slug. **Don't paste it anywhere else.** The token works like a password.

If there's no Subscribe option anywhere and no "Enable Calendar Feeds" setting, the school has probably turned feeds off. Record that in FINDINGS.md, since it's an important result.

## Commands

| Command | What it does |
|---|---|
| `python -m spike list` | institutions + configured feeds |
| `python -m spike fetch [--label L]` | download feeds into `data/raw/<inst>/<label>/<UTC>.ics` |
| `python -m spike run-all [--label L] [--offline]` | fetch → normalize → report → diff for every feed (this is the daily command) |
| `python -m spike inspect FILE.ics --institution X [--out F]` | markdown report of what a feed contains |
| `python -m spike normalize FILE.ics --institution X [--out F]` | normalized items as JSON |
| `python -m spike diff OLD.ics NEW.ics --institution X` / `diff --label L` | UID stability + item changes |
| `python -m spike anonymize FILE.ics --institution X --out tests/fixtures/real_X.ics` | commit-safe fixture |

Outputs (all gitignored): `data/out/all_items.json`, `data/out/<inst>/<label>.json`, `data/reports/<inst>/<label>/<ts>.md` and `<ts>.diff.md`.

Use `--offline` to re-run the latest saved snapshots after changing rules, without fetching again.

## Daily snapshots (about 2 weeks)

UID stability, deadline changes and deletions only show up over time. Run `run-all` once or twice a day. On Windows, run this once to schedule it:

```bat
schtasks /Create /SC DAILY /ST 09:00 /TN "BrightspaceSpike" /TR "\"%CD%\run_daily.bat\""
```

Run it from `spikes\brightspace_ical`. Logs go to `data\run_log.txt`. Remove the schedule with `schtasks /Delete /TN "BrightspaceSpike"`.

## Controlled experiments

Log results in FINDINGS.md:

1. Create a personal event in the Brightspace calendar → `run-all`
2. Change its time → `run-all` (does the UID stay the same?)
3. Delete it → `run-all` (does it disappear?)
4. Submit an assignment or quiz → `run-all` the next day (does it stay in the feed?)
5. When an instructor moves a real deadline → check the latest `.diff.md`

## Adding another school

1. Check the school's entry in `institutions.yaml`: the host, plus `rules` overrides if its course codes look different (see McMaster and Guelph).
2. Add a feed under that slug in `feeds.local.yaml` and run `run-all`.
3. Read the report's "role=plain", "other", and warnings sections, and add rules (in config, not code) until the items look right.
4. Set `verified: true` and fill in the school's column in FINDINGS.md.

## Committing real data safely

- Never commit `feeds.local.yaml` or anything in `data/`. Both are gitignored, but check `git status` anyway.
- To turn a real snapshot into a test fixture, run `anonymize`, **read the output yourself**, and only then commit it to `tests/fixtures/`.
