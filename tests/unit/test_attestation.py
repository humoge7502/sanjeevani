"""Ledger-head attestation: the anchor is only worth what its tests can prove.

An attestation that verifies when it should not is worse than no attestation —
it launders a fabricated history with a signature-shaped stamp. So every tamper
class gets its own case, and the CLI is exercised end-to-end against the real
ledger (the conftest redirects the runtime directory, so nothing here touches a
real ledger file).

    python -m pytest tests/unit/test_attestation.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.audit.attestation import (
    AttestationError,
    attest_head,
    verify_attestation,
)
from backend.audit.ledger import ledger

MODULE = "tests.unit.test_attestation"

KEY = "k" * 48
HEAD = "a" * 64


@pytest.fixture
def seeded_ledger():
    """A small real chain: three records, hash-chained, in the temp runtime."""
    ledger.reset()
    for i in range(3):
        ledger.append(f"STAGE-{i}", "test", "act", result="OK", ids={"n": str(i)})
    return ledger


def _attest(ledger=ledger, key: str = KEY, **overrides) -> dict:
    verdict = ledger.verify_chain()
    return attest_head(
        head_hash=overrides.get("head_hash", str(verdict["head"])),
        length=overrides.get("length", int(verdict["records"])),
        key=key,
        key_id=overrides.get("key_id", "primary"),
        created_at=overrides.get("created_at", "2026-10-28T00:00:00+00:00"),
    )


# ---------------------------------------------------------------------------
# The pure functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_an_attestation_round_trips_against_the_ledger_it_describes(seeded_ledger):
    att = _attest(seeded_ledger)
    verdict = seeded_ledger.verify_chain()
    verify_attestation(
        att, key=KEY, head_hash=str(verdict["head"]), length=int(verdict["records"])
    )


@pytest.mark.unit
def test_the_same_head_signs_to_identical_bytes(seeded_ledger):
    assert _attest(seeded_ledger) == _attest(seeded_ledger)


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda att: {**att, "head_hash": "b" * 64}, id="head-swapped"),
        pytest.param(lambda att: {**att, "mac": "0" * 64}, id="mac-edited"),
        pytest.param(lambda att: {**att, "length": att["length"] + 1}, id="length-inflated"),
        pytest.param(lambda att: {**att, "length": att["length"] - 1}, id="length-truncated"),
        pytest.param(lambda att: {**att, "created_at": "2025-01-01T00:00:00+00:00"}, id="time-moved"),
        pytest.param(lambda att: {**att, "kind": "hmac-sha256-head-v0"}, id="kind-swapped"),
    ],
)
def test_every_edit_to_the_attestation_file_is_refused(seeded_ledger, mutate):
    att = mutate(_attest(seeded_ledger))
    verdict = seeded_ledger.verify_chain()
    with pytest.raises(AttestationError):
        verify_attestation(
            att, key=KEY, head_hash=str(verdict["head"]), length=int(verdict["records"])
        )


@pytest.mark.unit
def test_a_different_key_cannot_verify_someone_elses_attestation(seeded_ledger):
    att = _attest(seeded_ledger)
    verdict = seeded_ledger.verify_chain()
    with pytest.raises(AttestationError, match="different key"):
        verify_attestation(
            att, key="z" * 48, head_hash=str(verdict["head"]), length=int(verdict["records"])
        )


@pytest.mark.unit
def test_an_empty_key_or_an_empty_head_is_a_loud_error_not_an_attestation():
    with pytest.raises(AttestationError, match="attestation key is required"):
        attest_head(head_hash=HEAD, length=3, key="", created_at="t")
    with pytest.raises(AttestationError, match="no head"):
        attest_head(head_hash="", length=3, key=KEY, created_at="t")


# ---------------------------------------------------------------------------
# The prefix rule: append is compatible, truncation and rewrite are not
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_chain_may_grow_after_an_attestation(seeded_ledger):
    att = _attest(seeded_ledger)
    seeded_ledger.append("STAGE-3", "test", "act")
    records = seeded_ledger.records()
    # The attested prefix is still the chain's own prefix.
    assert records[att["length"] - 1]["hash"] == att["head_hash"]
    verify_attestation(att, key=KEY, head_hash=att["head_hash"], length=att["length"])


# ---------------------------------------------------------------------------
# The CLI, end to end against the real ledger
# ---------------------------------------------------------------------------


def _run_cli(monkeypatch, tmp_path: Path, *argv: str) -> int:
    from scripts.attest_ledger import main

    monkeypatch.setenv("SANJEEVANI_ATTESTATION_KEY", KEY)
    monkeypatch.setattr("sys.argv", ["attest_ledger.py", *argv])
    return main()


@pytest.mark.unit
def test_the_cli_signs_and_verifies_a_full_round_trip(seeded_ledger, monkeypatch, tmp_path):
    out_dir = tmp_path / "attestations"
    assert _run_cli(monkeypatch, tmp_path, "attest", "--out-dir", str(out_dir)) == 0
    files = list(out_dir.glob("attest-*.json"))
    assert len(files) == 1
    assert _run_cli(monkeypatch, tmp_path, "verify", "--file", str(files[0])) == 0


@pytest.mark.unit
def test_the_cli_refuses_a_ledger_shorter_than_the_attested_prefix(
    seeded_ledger, monkeypatch, tmp_path
):
    """Truncation is the attack the prefix rule exists for."""
    out_dir = tmp_path / "attestations"
    _run_cli(monkeypatch, tmp_path, "attest", "--out-dir", str(out_dir))
    att_file = next(out_dir.glob("attest-*.json"))

    # Rewind the ledger below the attested length.
    seeded_ledger.reset()
    seeded_ledger.append("STAGE-0", "test", "act")

    assert _run_cli(monkeypatch, tmp_path, "verify", "--file", str(att_file)) == 1


@pytest.mark.unit
def test_the_cli_offline_mode_needs_no_ledger(seeded_ledger, monkeypatch, tmp_path):
    out_dir = tmp_path / "attestations"
    _run_cli(monkeypatch, tmp_path, "attest", "--out-dir", str(out_dir))
    att_file = next(out_dir.glob("attest-*.json"))
    att = json.loads(att_file.read_text(encoding="utf-8"))
    # Hand-verify offline, then have the CLI do the same.
    verify_attestation(att, key=KEY, head_hash=att["head_hash"], length=att["length"])
    assert _run_cli(monkeypatch, tmp_path, "verify", "--file", str(att_file), "--offline") == 0


@pytest.mark.unit
def test_a_missing_key_is_a_usage_error_not_a_signature(seeded_ledger, monkeypatch, tmp_path):
    # _run_cli always sets the key, so this case sets up argv itself.
    monkeypatch.delenv("SANJEEVANI_ATTESTATION_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["attest_ledger.py", "attest", "--out-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as excinfo:
        from scripts.attest_ledger import main

        main()
    assert excinfo.value.code == 2
