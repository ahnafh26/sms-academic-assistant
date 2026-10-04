# Brightspace iCal viability findings

**Question:** Do Brightspace calendar feeds from Ontario universities expose enough reliable information to power the SMS assistant without university partnerships?

**Go criteria** (from the plan):
- For each school tested, at least 90% of real assessments come out with the correct course code, type and due time, with no duplicates
- UIDs are stable across fetches, or the fingerprint fallback catches the cases where they aren't
- A moved deadline is detected as a single change, not as a delete plus a create
- The same normalizer works across schools with only config overrides

Status legend: ✅ confirmed · ⚠️ partially / with caveats · ❌ no · ❓ not tested yet

## Preliminary verdict (2026-10-04, after the first real fetch)

**Technically viable: yes. Sufficient on its own: no.**

- Feeds are available at both schools and need nothing more than one copied URL. Structure is clean and consistent (same D2L generator). One normalizer handles both schools with **zero** school-specific overrides.
- The issue is **completeness and noise**:
  - At Waterloo, only 1 of 4 current courses (MATH 135) puts deadlines in the LEARN calendar.
  - Neither feed contains a single real current-term exam or midterm.
  - Courses copied from old offerings carry stale dates (2018, 2021, 2024), including a fake "midterm" in each feed.
- **Consequence for the product:** the iCal feed is a good *primary* source but can't be the only one. Syllabus ingestion and student confirmation become core MVP features, not extras. Stale-date filtering and noise filtering (maintenance windows, residence and program org units) must be in the production normalizer from day one.
- **Still open:** the longitudinal questions (Q10, Q11, Q14, Q15) need about 2 weeks of daily snapshots. Accuracy against ground truth also needs the student's real course list and deadlines.

## Matrix

| # | Question | Waterloo (LEARN) | Laurier (MyLearningSpace) | McMaster | Guelph | TMU | Western | Queen's |
|---|---|---|---|---|---|---|---|---|
| 1 | Calendar subscription / iCal available? | ✅ | ✅ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 2 | How the student gets the feed URL | ✅ Calendar → Subscribe; `/d2l/le/calendar/feed/user/feed.ics?token=…` | ✅ same path | ❓ | ❓ | ❓ | ❓ | ❓ |
| 3 | What VEVENTs contain | ✅ UID, SUMMARY, LOCATION, DTSTART/DTEND, SEQUENCE, LAST-MODIFIED, CLASS (100%); DESCRIPTION 29% | ✅ same; DESCRIPTION 94%; RRULE 5% (maintenance only) | ❓ | ❓ | ❓ | ❓ | ❓ |
| 4 | Course codes available? | ✅ in LOCATION: `CS 115 - Fall 2026` (code + term, no name) | ✅ in LOCATION: `EC-120-A-B-C - Intro To Microeconomics` (code + sections + name, no term) | ❓ | ❓ | ❓ | ❓ | ❓ |
| 5 | How assignments are represented | ⚠️ `<title> - Due` point events; most current CS/MATH work isn't in the calendar | ✅ `<title> - Due`; publisher homework (Achieve: Decision Point / EOC Problems / LearningCurve) synced in; Dropbox items | ❓ | ❓ | ❓ | ❓ | ❓ |
| 6 | How quizzes are represented | ✅ `<title> - Due`, description prefix `Quizzes:` | ✅ `<title> - Due` | ❓ | ❓ | ❓ | ❓ | ❓ |
| 7 | How exams/midterms are represented | ❌ no current exams; only a stale Oct 2025 "Write Midterm" and 2018 institutional exam-period dates | ❌ no current exams; only a stale 2024 "Midterm" | ❓ | ❓ | ❓ | ❓ | ❓ |
| 8 | How due dates are represented | ✅ point event (DTSTART = DTEND) in UTC `Z`; the due instant is DTSTART | ✅ same | ❓ | ❓ | ❓ | ❓ | ❓ |
| 9 | Separate Due / Starts / Availability Ends events? | ⚠️ only `- Due` markers seen | ✅ `- Due` ×62, `- Available` ×3, `- Availability Ends` ×2; pairs merged correctly | ❓ | ❓ | ❓ | ❓ | ❓ |
| 10 | UIDs stable across repeated fetches? | ❓ (format `<root org>-<event id>@host`, looks like stable DB ids) | ❓ (same format) | ❓ | ❓ | ❓ | ❓ | ❓ |
| 11 | Changed deadline keeps the same UID? | ❓ (SEQUENCE is >0 on 23 events, which suggests edits bump SEQUENCE but keep UID) | ❓ (SEQUENCE >0 on 98 events) | ❓ | ❓ | ❓ | ❓ | ❓ |
| 12 | Timezone information provided | ✅ timed events in UTC `Z`; all-day as `VALUE=DATE`; no VTIMEZONE | ✅ mostly UTC `Z`; 9 TZID=America/Toronto with VTIMEZONE; 5 all-day | ❓ | ❓ | ❓ | ❓ | ❓ |
| 13 | Past/future window of the feed | ✅ no past cutoff (back to 2012); future through end of term (+64 days) | ✅ no past cutoff (back to 2010); through +71 days | ❓ | ❓ | ❓ | ❓ | ❓ |
| 14 | Completed/submitted items stay in the feed? | ❓ (likely yes: no past cutoff observed) | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 15 | Deleted items disappear? | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ | ❓ |
| 16 | Course names/codes consistent enough to normalize? | ✅ one label format for all courses | ✅ one label format for all courses | ❓ | ❓ | ❓ | ❓ | ❓ |
| 17 | Same normalization logic works across schools? | ✅ no overrides needed | ✅ no overrides needed | ❓ | ❓ | ❓ | ❓ | ❓ |

## What the first real fetch taught us (and what changed in the normalizer)

| Observation | Impact | Fix |
|---|---|---|
| LOCATION is present on 100% of events and is the org unit label, or `<venue> (<label>)` | Searching every field for codes produced false courses (room `LH1001`, `MATH 137` taken from a title) | LOCATION label is authoritative; venue split off (`a5104fc`) |
| Description starts with the owning tool: `Quizzes:`, `Dropbox:`, `Checklists:`, `Materials:`, `Modules:` | Strong type and relevance signal | `source_tool`, plus `category` = deliverable / exam / content / checklist / event / non_course (`134ad08`) |
| Root org (maintenance windows back to 2010) and community orgs (Residence, BBA Program Info, Math Ready) are mixed in | Laurier 84/151 and Waterloo 44/124 items aren't course work | `org_kind` = course / community / institution; non-course items are never deadlines (`134ad08`) |
| Courses copied from old offerings keep stale dates (CS 115 has 2018 dates; BU 111 has 2021–2024 items, including "Midterm") | These would be reported as real (overdue) deadlines and as **the student's midterm** | Term window from the label (`8c40482`) and a course-median outlier fallback (`6b9b6e4`) |
| Publisher homework titles (LearningCurve, Decision Point, EOC Problems) | 125/151 Laurier items were typed `other` | Type rules extended; admin pages (forms/rubrics/instructions) typed `other` (`d72efc4`) |
| An invalid token returns HTTP 200 with an HTML login page | Broken feeds can't be detected from the status code | Content check for `BEGIN:VCALENDAR` (`fe9c74f`) |

After the fixes, the items the product would treat as this term's deadlines are:
- **Waterloo: 10**, all MATH 135. Five are onboarding tasks ("Log in to Piazza", "Read the Course Outline"), four are bonus integrity quizzes, and one is an assignment. CS 115, MATH 137 and MTHEL 99 have **no** current-term deadlines in the calendar.
- **Laurier: 52**: EC 120 has 40 (Achieve homework, podcast assignments, syllabus quizzes), and BU 111 has 12 (CIIP slidedocs and presentation, peer evaluations, live case report, bonus items, conflict form).

## Accuracy against ground truth

Still needs the student's real course list and upcoming deadlines as shown in Brightspace and on the course outlines.

| School | Real assessments checked | Correct course | Correct type | Correct due time | Duplicates | Missing from feed |
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

- Feed: `https://learn.uwaterloo.ca/d2l/le/calendar/feed/user/feed.ics?token=…`. `X-WR-CALNAME: All Courses - University of Waterloo`. Root org id 6606.
- Course label format: `<SUBJ> <NUM> - <Term> <Year>`. It has no course name but does have a term, which enables stale-date detection.
- Suffixes seen: only ` - Due`.
- Many timed events use a local 16:00/16:05 due time. 70/124 events are all-day (institutional dates copied into course shells).
- **Coverage problem:** most first-year math and CS courses run deliverables outside LEARN (course websites, external graders, Piazza). The feed alone would badly under-report Waterloo deadlines.
- Rule overrides needed: none.

### Laurier (MyLearningSpace)

- Feed: `https://mylearningspace.wlu.ca/d2l/le/calendar/feed/user/feed.ics?token=…`. `X-WR-CALNAME: All Courses - Wilfrid Laurier University`. Root org id 6605.
- Course label format: `<SUBJ>-<NUM>-<sections> - <Course Name>`. It has a name but no term, so stale detection relies on the course-median fallback.
- Suffixes seen: ` - Due`, ` - Available`, ` - Availability Ends`.
- 77 root-org events (maintenance windows from 2010–2014, some RRULE). These are noise.
- Publisher (Macmillan Achieve) homework syncs into the calendar, which gives good coverage for EC 120.
- Rule overrides needed: none.

## Next steps

1. Get ground truth from the student and fill in the accuracy table.
2. Schedule daily `run-all` for about 2 weeks and run the controlled experiments (Q10, Q11, Q14, Q15).
3. Get a feed from at least one more school (McMaster or Guelph), ideally a non-math/CS program.
4. Product implications to carry into the MVP design: syllabus ingestion is core, so is stale/noise filtering, and so is an onboarding step where the student confirms the course list ("I found MATH 135 deadlines but nothing for CS 115. Want to send me the CS 115 outline?").
