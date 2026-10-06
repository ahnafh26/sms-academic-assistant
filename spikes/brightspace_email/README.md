# Brightspace email-notification spike (Phase 0b)

This is throwaway spike code. The question it answers: **what can the assistant learn from Brightspace's own notification emails that the calendar feed misses?** The candidates are grades, announcements, feedback, extensions, and due-soon reminders. This needs no university approval and no passwords, because the student controls their notification settings.

Same method as the calendar spike: collect real emails, inspect what they actually contain, then write classification rules from the evidence.

## Step 1: turn on email notifications (each school)

In Brightspace (LEARN / MyLearningSpace): click **your name or avatar (top right) → Notifications**. Labels vary by school. Turn on **email** for everything available, especially:

- **Instant notifications:** announcements (new/updated), assignments (feedback released, due date approaching), content (new/updated items), discussions, **grades (grade item released/updated)**, quizzes (due date approaching, feedback/grade released)
- **Customize notifications:** tick **"Include my grade value in notifications"** if it's offered
- **Summary of activity:** set to *daily*, so we can see what the digest contains
- Check the **email address** at the top is your school email

Write down which options each school offers. That's finding E1.

## Step 2: collect emails (after a few days)

Save each Brightspace notification email as an **`.eml`** file into:

```
spikes/brightspace_email/data/inbox/waterloo/
spikes/brightspace_email/data/inbox/laurier/
```

- **Outlook on the web:** open the email → **⋯** → **Download** (saves `.eml`)
- **Outlook desktop:** drag the email into the folder in File Explorer, or forward it to a Gmail and download it there
- **Gmail:** open the email → **⋮** → **Download message**

About 10–20 emails per school is enough, ideally one or more of each kind (grade, announcement, due soon, feedback, digest). `data/` is gitignored, so real emails never get committed.

## Step 3: analyze

```bash
cd spikes/brightspace_email
.venv/Scripts/python -m mailspike run          # classify every email + write the report
.venv/Scripts/python -m pytest
```

Outputs, all in `data/` and gitignored:
- `data/out/email_events.json`: normalized events (kind, course, title, dates, grade, link)
- `data/reports/EMAIL_REPORT.md`: senders, subject patterns, link patterns, what was classified, and what wasn't

Findings go in [findings/FINDINGS.md](findings/FINDINGS.md).

## Production shape (not built yet)

A student would add one Outlook rule that forwards Brightspace notification emails to a private per-student address. The SMS service would parse them into the same normalized model as the calendar items, with `source = email`. Email is a lower-trust source, so it triggers questions rather than silent changes ("Your prof says A3 moved to Sunday. Brightspace still says Friday. Use Sunday?"). One thing to check: some university Microsoft 365 tenants block auto-forwarding to outside addresses.
