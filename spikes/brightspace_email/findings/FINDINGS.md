# Brightspace email-notification findings

**Question:** Can Brightspace's own notification emails reliably give the assistant what the calendar feed can't (grades, announcements, feedback, extensions, due-soon reminders), with no university approval and no passwords?

**Go criteria:**
- Both schools let students email themselves grade and announcement notifications
- At least 90% of notification emails are classified with the correct kind and course
- Grade emails contain the grade value, or at least which item was graded
- Deadline changes announced by profs can be detected and matched to a calendar item
- Students can forward these emails automatically (no M365 block on external forwarding)

Status legend: ✅ confirmed · ⚠️ partially / with caveats · ❌ no · ❓ not tested yet

| # | Question | Waterloo (LEARN) | Laurier (MyLearningSpace) |
|---|---|---|---|
| E1 | Which notification types can be emailed? | ❓ | ❓ |
| E2 | Sender address(es)? | ❓ | ❓ |
| E3 | Are subject formats consistent per kind? | ❓ | ❓ |
| E4 | Do emails link to D2L tool URLs with org unit ids? | ❓ | ❓ |
| E5 | Is the course identifiable (code or org unit)? | ❓ | ❓ |
| E6 | Grade emails: is the value included ("include grade value" setting)? | ❓ | ❓ |
| E7 | Due-soon emails: exact due date/time included? | ❓ | ❓ |
| E8 | Announcements: is the full text included, or just a link? | ❓ | ❓ |
| E9 | Can a deadline change in an announcement be matched to a calendar item? | ❓ | ❓ |
| E10 | What does the daily summary digest contain? | ❓ | ❓ |
| E11 | Is auto-forwarding to an external address allowed? | ❓ | ❓ |
| E12 | Does the same classifier work at both schools? | ❓ | ❓ |

## Notes

_Pending real emails. Settings available at each school (E1) go here first._
