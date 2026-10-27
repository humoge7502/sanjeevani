"""Unit tests: durable persistence and ledger durability.

Two properties are being protected.

  1. **The mirror can never become a second source of truth.** The JSONL ledger is
     authoritative and the only thing the hash chain covers. These tests assert
     that a disagreement is *reported*, that the mirror is never consulted to
     decide what happened, and that a mirror failure cannot break a request.
  2. **Durability is observable.** The previous implementation swallowed every
     write error, so a read-only filesystem lost audit records silently. Now the
     failures are counted and surfaced on ``/api/health``.
"""

from __future__ import annotations

import sqlite3

import pytest

from backend.audit.ledger import DecisionLedger
from backend.config import reload_config
from backend.persistence import SqliteAuditMirror

pytestmark = pytest.mark.unit


@pytest.fixture
def mirror(tmp_path):
    instance = SqliteAuditMirror(tmp_path / "mirror.sqlite3", enabled=True)
    yield instance
    instance.close()


def _record(seq: int, **overrides) -> dict:
    base = {
        "seq": seq,
        "stage": "APPROVAL_DECIDED",
        "actor": "meera.iyer",
        "action": "approve",
        "result": "OK",
        "ids": {"plan_id": "PLAN-001", "tenant": "acme-pharma"},
        "detail": {"note": "approved"},
        "timestamp": "2026-09-30T06:00:00Z",
        "prev_hash": "GENESIS" if seq == 1 else "abc",
        "hash": f"hash{seq}",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# The mirror
# ---------------------------------------------------------------------------


def test_disabled_mirror_is_a_no_op(tmp_path):
    instance = SqliteAuditMirror(tmp_path / "off.sqlite3", enabled=False)
    instance.append(_record(1))
    assert instance.count() == 0
    assert instance.query()["records"] == []
    assert instance.status().backend == "none"
    instance.close()


def test_mirror_writes_and_counts(mirror):
    for seq in range(1, 6):
        mirror.append(_record(seq))
    assert mirror.count() == 5


def test_mirror_uses_wal_so_readers_do_not_block_the_writer(mirror):
    mirror.append(_record(1))
    assert mirror.status().journal_mode == "wal"


def test_mirror_is_idempotent_on_a_replayed_seq(mirror):
    """A re-mirrored record must not become a second row."""
    mirror.append(_record(1))
    mirror.append(_record(1, result="REPLAYED"))
    assert mirror.count() == 1
    rows = mirror.query()["records"]
    assert rows[0]["result"] == "REPLAYED"


def test_mirror_query_filters_and_pages(mirror):
    for seq in range(1, 11):
        stage = "APPROVAL_DECIDED" if seq % 2 == 0 else "POLICY_EVALUATED"
        mirror.append(_record(seq, stage=stage))
    approvals = mirror.query(stage="APPROVAL_DECIDED")
    assert approvals["total"] == 5
    assert all(r["stage"] == "APPROVAL_DECIDED" for r in approvals["records"])
    page = mirror.query(stage="APPROVAL_DECIDED", limit=2, offset=0)
    assert page["returned"] == 2
    assert page["total"] == 5
    # Newest first, so the audit reader sees the latest decision immediately.
    assert page["records"][0]["seq"] > page["records"][1]["seq"]


def test_mirror_query_filters_by_result(mirror):
    mirror.append(_record(1, result="OK"))
    mirror.append(_record(2, result="DENIED"))
    assert mirror.query(result="DENIED")["total"] == 1


def test_mirror_reset_empties_it(mirror):
    mirror.append(_record(1))
    mirror.reset()
    assert mirror.count() == 0


def test_a_mirror_write_failure_is_counted_not_raised(mirror):
    """A mirror is never on the critical path: a broken database must not stop an approval."""
    mirror.append(_record(1))
    mirror.close()
    mirror._connection = sqlite3.connect(":memory:")
    mirror._connection.execute("DROP TABLE IF EXISTS ledger_records")
    mirror.append(_record(2))  # must not raise
    assert mirror.write_failures >= 1
    assert mirror.last_error
    mirror._connection.close()


def test_reset_never_touches_the_jsonl_ledger(tmp_path):
    """The mirror and the ledger are separate files, and the mirror's reset is scoped to it."""
    ledger_path = tmp_path / "audit_ledger.jsonl"
    ledger = DecisionLedger(path=ledger_path)
    ledger.append("SIGNAL_RECEIVED", "NEWS", "ingest")
    assert ledger_path.read_text(encoding="utf-8").strip()

    instance = SqliteAuditMirror(tmp_path / "mirror.sqlite3", enabled=True)
    instance.append(ledger.records()[-1])
    instance.reset()
    assert instance.count() == 0
    assert ledger_path.read_text(encoding="utf-8").strip(), "the JSONL ledger was touched"
    instance.close()


# ---------------------------------------------------------------------------
# Consistency is reported, never silently reconciled
# ---------------------------------------------------------------------------


def test_consistency_reports_a_disagreement_rather_than_hiding_it(mirror):
    mirror.append(_record(1))
    agreement = mirror.consistency(1)
    assert agreement["consistent"] is True
    disagreement = mirror.consistency(5)
    assert disagreement["consistent"] is False
    assert disagreement["mirror_records"] == 1
    assert disagreement["ledger_records"] == 5
    assert "authoritative" in disagreement["resolution"]


# ---------------------------------------------------------------------------
# Ledger durability
# ---------------------------------------------------------------------------


def test_ledger_records_write_failures_instead_of_swallowing_them(tmp_path):
    """A non-writable ledger path must be reported, not silently lose history.

    The failure is produced honestly rather than by monkeypatching: the ledger's
    parent is a FILE, so opening the append target raises ``NotADirectoryError``
    -- the same OSError class a read-only mount raises. The record must still be
    accepted in memory (an approval is not blocked by a disk problem), and the
    failure must be counted and surfaced on ``/api/health``.
    """
    barrier = tmp_path / "not-a-directory"
    barrier.write_text("this is a file, not a directory", encoding="utf-8")
    ledger = DecisionLedger(path=barrier / "readonly.jsonl")
    assert ledger.write_failures == 0

    record = ledger.append("SIGNAL_RECEIVED", "NEWS", "ingest")
    assert record.seq == 1
    assert len(ledger) == 1, "the record must still be in memory"
    assert ledger.write_failures == 1
    assert ledger.last_write_error
    health = ledger.health()
    assert health["write_failures"] == 1
    assert health["last_write_error"]


def test_ledger_health_reports_durability_mode_and_mirror(tmp_path):
    ledger = DecisionLedger(path=tmp_path / "audit.jsonl")
    ledger.append("SIGNAL_RECEIVED", "NEWS", "ingest")
    health = ledger.health()
    assert health["durability"] in {"none", "flush", "fsync"}
    assert "mirror" in health
    assert health["mirror"]["enabled"] is False  # off by default


def test_fsync_durability_is_configurable(tmp_path, monkeypatch):
    """The fsync path must actually run, and must be reachable from config."""
    import sys

    from backend.config import settings

    ledger_module = sys.modules["backend.audit.ledger"]
    monkeypatch.setattr(
        ledger_module,
        "settings",
        lambda: {**settings(), "persistence": {"ledger_durability": "fsync"}},
    )
    ledger = DecisionLedger(path=tmp_path / "durable.jsonl")
    ledger.append("SIGNAL_RECEIVED", "NEWS", "ingest")
    assert ledger.write_failures == 0
    assert (tmp_path / "durable.jsonl").read_text(encoding="utf-8").strip()


def test_a_typo_in_durability_is_a_startup_failure(monkeypatch):
    """A silently weaker audit trail is worse than refusing to start."""
    from backend.config import _apply_env_overrides

    monkeypatch.setenv("SANJEEVANI_LEDGER_DURABILITY", "flsuh")
    with pytest.raises(ValueError, match=r"none\|flush\|fsync"):
        _apply_env_overrides({})
    monkeypatch.delenv("SANJEEVANI_LEDGER_DURABILITY", raising=False)
    reload_config()
