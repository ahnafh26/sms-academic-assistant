# Brightspace iCal viability findings

**Question:** Do Brightspace calendar feeds from Ontario universities expose enough reliable information to power the SMS assistant without university partnerships?

**Go criteria** (from the plan):
- For each school tested, at least 90% of real assessments come out with the correct course code, type and due time, with no duplicates
- UIDs are stable across fetches, or the fingerprint fallback catches the cases where they aren't
- A moved deadline is detected as a single change, not as a delete plus a create
- The same normalizer works across schools with only config overrides

Status legend: ✅ confirmed · ⚠️ partially / with caveats · ❌ no · ❓ not tested yet

## Matrix

| # | Question | Waterloo (LEARN) | Laurier (MyLearningSpace) | McMaster | Guelph | TMU | Western | Queen's |
|---|---|---|---|---|---|---|---|---|
| 1 | Calendar subscription / iCal available? | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 2 | How the student gets the feed URL | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 3 | What VEVENTs contain | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 4 | Course codes available? | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 5 | How assignments are represented | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 6 | How quizzes are represented | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 7 | How exams/midterms are represented | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 8 | How due dates are represented | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 9 | Separate Due / Starts / Availability Ends events? | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 10 | UIDs stable across repeated fetches? | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 11 | Changed deadline keeps the same UID? | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 12 | Timezone information provided | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 13 | Past/future window of the feed | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 14 | Completed/submitted items stay in the feed? | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 15 | Deleted items disappear? | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 16 | Course names/codes consistent enough to normalize? | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 17 | Same normalization logic works across schools? | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |

Where each answer comes from: the `inspect` report sections are tagged with Q-numbers. Q10, Q11 and Q15 come from `diff` reports across days. Q14 and Q15 also need the controlled experiments below.

## Accuracy against ground truth

| School | Real assessments checked | Correct course | Correct type | Correct due time | Duplicates | Missing |
|---|---:|---:|---:|---:|---:|---:|
| Waterloo | | | | | | |
| Laurier | | | | | | |

## Controlled experiments log

| Date | School | Experiment | Expected | Observed | Snapshot(s) |
|---|---|---|---|---|---|
| | | Create a personal calendar event, then fetch | New UID appears | | |
| | | Edit the event's time, then fetch | Same UID, new DTSTART | | |
| | | Delete the event, then fetch | UID disappears | | |
| | | Submit an assignment/quiz, then fetch | Does it stay? | | |
| | | Real instructor deadline change observed | Same UID, new time | | |

## Per-school notes

### Waterloo (LEARN)

- Feed URL path:
- Observed suffix patterns:
- Course code location:
- Rule overrides needed:
- Surprises:

### Laurier (MyLearningSpace)

- Feed URL path:
- Observed suffix patterns:
- Course code location:
- Rule overrides needed:
- Surprises:

## Verdict

_Pending real feeds._
