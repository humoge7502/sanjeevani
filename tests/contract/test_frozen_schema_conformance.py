"""Conformance of the Pydantic contracts against the frozen JSON schemas.

The six shared contracts are an *integration* boundary: another member, or a real
SAP connector, reads them. `tests/contract/test_shared_contracts.py` proves the
models behave; this file proves they still describe the artifact the team agreed
on, by reading the frozen schema documents directly and comparing them.

The schemas live in `docs/evidence/contracts/` and were supplied with the
SANJEEVANI real-data package (v2.1.0). They are treated as read-only source: if a
field here disappears from a model, the contract has silently drifted and this
test fails, which is the entire point of freezing a contract.

A note on the examples: the frozen schemas use the literal string `"ISO-8601"` as
a placeholder in every `timestamp` field. That is not a parseable timestamp, so a
model that validates its timestamp — as `Event` deliberately does — cannot accept
the example verbatim. The placeholder is substituted below rather than weakening
the validator, because a contract that accepts the string "ISO-8601" as a time is
not describing a time at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.contracts import (
    Approval,
    Event,
    ExecutionReceipt,
    Impact,
    Outcome,
    RecoveryPlan,
)

CONTRACT_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "contracts"

#: schema file stem -> (Pydantic model, contract name)
CONTRACTS: dict[str, tuple[type, str]] = {
    "event": (Event, "event"),
    "impact": (Impact, "impact"),
    "recovery_plan": (RecoveryPlan, "recovery_plan"),
    "approval": (Approval, "approval"),
    "execution_receipt": (ExecutionReceipt, "execution_receipt"),
    "outcome": (Outcome, "outcome"),
}

#: Used to replace the frozen schemas' `"ISO-8601"` placeholder.
VALID_TIMESTAMP = "2026-09-30T06:00:00Z"


def load_schema(stem: str) -> dict:
    path = CONTRACT_DIR / f"{stem}.schema.json"
    if not path.exists():  # pragma: no cover - guards a deleted evidence pack
        pytest.skip(f"frozen schema not present: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def declared_field_names(schema: dict) -> list[str]:
    """Extract field names from entries like `'revenue_at_risk (number)'`."""
    names: list[str] = []
    for entry in schema["fields"]:
        name = entry.split("(", 1)[0].strip()
        if name:
            names.append(name)
    return names


@pytest.mark.contract
@pytest.mark.parametrize("stem", sorted(CONTRACTS))
def test_every_frozen_field_exists_on_the_model(stem: str) -> None:
    """No contract field may be renamed, removed or typo'd."""
    model, _ = CONTRACTS[stem]
    schema = load_schema(stem)

    model_fields = set(model.model_fields)
    missing = [name for name in declared_field_names(schema) if name not in model_fields]

    assert missing == [], (
        f"{stem}: the frozen contract declares fields the model does not expose: "
        f"{missing}. Renaming or dropping a contract field is a breaking change."
    )


@pytest.mark.contract
@pytest.mark.parametrize("stem", sorted(CONTRACTS))
def test_model_accepts_the_frozen_example(stem: str) -> None:
    """The documented example must be a valid instance, not just a shape."""
    model, _ = CONTRACTS[stem]
    schema = load_schema(stem)

    example = dict(schema["example"])
    if example.get("timestamp") == "ISO-8601":
        example["timestamp"] = VALID_TIMESTAMP

    instance = model.model_validate(example)
    assert instance is not None


@pytest.mark.contract
def test_contract_versions_are_declared_and_frozen() -> None:
    """Every schema must carry a version, and every model must agree with it."""
    from backend.contracts import CONTRACT_VERSION

    for stem in CONTRACTS:
        schema = load_schema(stem)
        assert schema["contract"] == stem
        # e.g. "1.0.0 (frozen per workflow PDF Ch.3)"
        assert schema["version"].startswith(CONTRACT_VERSION), (
            f"{stem}: schema version {schema['version']!r} does not start with "
            f"CONTRACT_VERSION {CONTRACT_VERSION!r}"
        )


@pytest.mark.contract
def test_every_schema_states_the_no_private_schema_rule() -> None:
    """The rule that keeps the contract single-sourced must survive edits."""
    for stem in CONTRACTS:
        schema = load_schema(stem)
        assert "no member invents a private schema" in schema["rule"].lower()


@pytest.mark.contract
def test_additive_fields_are_optional_so_v1_consumers_keep_working() -> None:
    """Anything beyond the frozen set must have a default.

    A required extra field would break a consumer that only knows the frozen
    contract, which is exactly what "additive" is supposed to rule out.
    """
    for stem, (model, _) in CONTRACTS.items():
        schema = load_schema(stem)
        frozen = set(declared_field_names(schema))

        for name, field in model.model_fields.items():
            if name in frozen:
                continue
            assert not field.is_required(), (
                f"{stem}.{name} is not in the frozen contract, so it must be "
                f"optional with a default. It is currently required."
            )


@pytest.mark.contract
def test_compliance_status_values_cover_the_frozen_examples() -> None:
    """`compliance_status` must accept every value the schema enumerates.

    The frozen schema lists them as examples, and the only one the source PDF
    fixes outright is PENDING. This asserts the implementation covers the
    schema's list so the shipped schema and the runtime cannot disagree.
    """
    schema = load_schema("recovery_plan")
    entry = next(e for e in schema["fields"] if e.startswith("compliance_status"))

    listed = entry.split("(")[1].rstrip(")")
    values = [v.strip() for v in listed.replace("e.g.", "").split("/") if v.strip()]

    annotation = RecoveryPlan.model_fields["compliance_status"].annotation
    allowed = set(getattr(annotation, "__args__", ()))

    assert allowed, "compliance_status should be a closed Literal"
    for value in values:
        assert value in allowed, (
            f"frozen schema lists compliance_status={value!r} but the "
            f"implementation allows {sorted(allowed)}"
        )
