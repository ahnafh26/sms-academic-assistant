# Brightspace iCal viability spike (Phase 0)

This is throwaway spike code. Its job is to answer one question: **do Brightspace calendar (iCal) feeds from Ontario universities expose enough reliable information (courses, deadlines, types, stable IDs) to power the SMS assistant without university partnerships?**

Findings go in [findings/FINDINGS.md](findings/FINDINGS.md).

## Setup

```bash
cd spikes/brightspace_ical
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux
cp feeds.local.example.yaml feeds.local.yaml               # then paste your feed URL(s)
.venv/Scripts/python -m spike list
```

## Safety

- Feed URLs contain a personal token. Keep them in `feeds.local.yaml` only. That file is gitignored. Never paste a feed URL into chat, issues, or commits.
- Raw snapshots and outputs are written to `data/`, which is also gitignored. Only anonymized fixtures go into `tests/fixtures/`.
