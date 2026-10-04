from pathlib import Path

from spike.config import InstitutionConfig
from spike.inspect_feed import build_report
from spike.parse import parse_ics

FIXTURES = Path(__file__).parent / "fixtures"
INST = InstitutionConfig(
    slug="waterloo", name="UW", lms_name="LEARN", hosts=("learn.example.ca",), timezone="America/Toronto"
)


def test_report_has_every_section():
    report = build_report(parse_ics((FIXTURES / "synthetic_before.ics").read_bytes()), INST, label="t")
    for heading in ("Calendar-level", "Property coverage", "Time representation", "Feed window",
                    "Title patterns", "UIDs", "Courses", "Normalized types", "Sample raw events"):
        assert f"## {heading}" in report
    assert "| CS135 | Designing Functional Programs | 2 |" in report


def test_report_never_contains_feed_tokens():
    raw = (FIXTURES / "synthetic_before.ics").read_bytes().replace(
        b"/detailsview", b"/detailsview?token=LEAKYTOKEN999"
    )
    report = build_report(parse_ics(raw), INST, label="t")
    assert "LEAKYTOKEN999" not in report
