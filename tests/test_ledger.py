import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from stickman.ledger import Ledger, LedgerEntry, utc_day_start, week_start

PK = timezone(timedelta(hours=5))


def entry(ts, usd=0.01, billing="billed", neurons=None, unit="001"):
    return LedgerEntry(
        ts=ts, project="2026-09-25_demo", unit=unit, kind="image",
        model="@cf/black-forest-labs/flux-2-klein-4b", est_usd=usd, billing=billing, neurons=neurons,
    )


def test_entries_round_trip_one_json_object_per_line(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    first = entry(datetime(2026, 9, 22, 14, 3, 11, tzinfo=PK), neurons=207.59)
    ledger.append(first)
    ledger.append(entry(datetime(2026, 9, 22, 14, 4, 0, tzinfo=PK), unit=None))
    assert list(ledger.entries())[0] == first
    lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["ts"] == "2026-09-22T14:03:11+05:00"


def test_a_missing_ledger_is_empty(tmp_path):
    assert list(Ledger(tmp_path / "none.jsonl").entries()) == []


def test_a_torn_last_line_is_skipped_and_the_next_entry_stays_readable(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry(datetime(2026, 9, 22, 14, 0, tzinfo=PK)))
    with (tmp_path / "ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"ts": "2026-09-2')  # a write that was killed halfway
    ledger.append(entry(datetime(2026, 9, 22, 15, 0, tzinfo=PK), unit="002a"))
    assert [e.unit for e in ledger.entries()] == ["001", "002a"]


def test_the_week_starts_on_monday_at_midnight():
    thursday = datetime(2026, 9, 24, 10, 0, tzinfo=PK)
    assert week_start(thursday) == datetime(2026, 9, 21, 0, 0, tzinfo=PK)
    assert week_start(datetime(2026, 9, 27, 23, 59, 59, tzinfo=PK)) == datetime(2026, 9, 21, tzinfo=PK)
    assert week_start(datetime(2026, 9, 28, 0, 0, tzinfo=PK)) == datetime(2026, 9, 28, tzinfo=PK)


def test_the_weeks_spend_counts_billed_and_possibly_billed(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry(datetime(2026, 9, 20, 23, 59, 59, tzinfo=PK), usd=5.0))  # Sunday: last week
    ledger.append(entry(datetime(2026, 9, 21, 0, 0, 0, tzinfo=PK), usd=1.0))  # Monday 00:00
    ledger.append(entry(datetime(2026, 9, 23, 12, 0, tzinfo=PK), usd=0.5, billing="possibly_billed"))
    ledger.append(entry(datetime(2026, 9, 23, 12, 0, tzinfo=PK), usd=0.25, billing="not_billed"))
    sunday_night = datetime(2026, 9, 27, 23, 59, 59, tzinfo=PK)
    assert ledger.spent_since(week_start(sunday_night)) == pytest.approx(1.5)


def test_todays_neurons_count_from_midnight_utc(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry(datetime(2026, 9, 24, 4, 59, tzinfo=PK), neurons=100.0))  # 23:59 UTC the day before
    ledger.append(entry(datetime(2026, 9, 24, 5, 0, tzinfo=PK), neurons=207.59))  # 00:00 UTC
    now = datetime(2026, 9, 24, 20, 0, tzinfo=PK)
    assert utc_day_start(now) == datetime(2026, 9, 24, 0, 0, tzinfo=UTC)
    assert ledger.neurons_since(utc_day_start(now)) == pytest.approx(207.59)


def test_a_torn_line_that_cuts_a_multibyte_character_is_skipped(tmp_path):
    """A non-ASCII project folder name (given with -p) puts multi-byte characters in the ledger."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry(datetime(2026, 9, 22, 14, 0, tzinfo=PK)))
    torn = entry(datetime(2026, 9, 22, 14, 30, tzinfo=PK)).model_copy(update={"project": "2026-09-25_café"})
    data = torn.model_dump_json().encode("utf-8")
    with (tmp_path / "ledger.jsonl").open("ab") as handle:
        handle.write(data[: data.index("é".encode("utf-8")) + 1])  # killed inside the two bytes of é
    ledger.append(entry(datetime(2026, 9, 22, 15, 0, tzinfo=PK), unit="002a"))
    assert [e.unit for e in ledger.entries()] == ["001", "002a"]
