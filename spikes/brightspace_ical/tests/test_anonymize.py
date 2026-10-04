from pathlib import Path

from spike.anonymize import anonymize_ics
from spike.config import InstitutionConfig
from spike.normalize import normalize_events
from spike.parse import parse_ics

FIXTURES = Path(__file__).parent / "fixtures"
INST = InstitutionConfig(
    slug="waterloo", name="UW", lms_name="LEARN", hosts=("learn.example.ca",), timezone="America/Toronto"
)
ORIGINAL = (FIXTURES / "synthetic_before.ics").read_bytes()


def test_identifying_text_removed():
    out = anonymize_ics(ORIGINAL, salt="s1").decode()
    for secret in ("Designing Functional Programs", "CS 135", "BU111", "Calculus I", "411111",
                   "422222", "Submit to the A4 dropbox", "Synthetic Student Calendar"):
        assert secret not in out, secret


def test_structure_preserved_for_normalizer():
    before = normalize_events(parse_ics(ORIGINAL).events, INST)
    after = normalize_events(parse_ics(anonymize_ics(ORIGINAL, salt="s1")).events, INST)
    assert len(after) == len(before)
    assert sorted(i.type for i in after) == sorted(i.type for i in before)
    assert sorted(str(i.due_at) for i in after) == sorted(str(i.due_at) for i in before)
    assert sorted(i.title for i in after) == sorted(i.title for i in before)
    # course codes still detectable, just different
    codes_after = {i.course_code for i in after if i.course_code}
    assert len(codes_after) == 4 and "CS135" not in codes_after
    # org-unit fallback still works on anonymized URLs
    lab = next(i for i in after if i.title == "Lab Report 2")
    assert lab.org_unit_id and lab.org_unit_id != "422222"


def test_mapping_is_deterministic_per_salt():
    assert anonymize_ics(ORIGINAL, salt="s1") == anonymize_ics(ORIGINAL, salt="s1")
    assert anonymize_ics(ORIGINAL, salt="s1") != anonymize_ics(ORIGINAL, salt="s2")


def test_uids_keep_shape_and_stay_consistent_across_snapshots():
    later = (FIXTURES / "synthetic_after.ics").read_bytes()
    a = {e.uid for e in parse_ics(anonymize_ics(ORIGINAL, salt="s1")).events}
    b = {e.uid for e in parse_ics(anonymize_ics(later, salt="s1")).events}
    assert all(u.endswith("@synthetic.d2l") for u in a)
    assert len(a & b) == 7  # same UIDs that survived in the real diff
