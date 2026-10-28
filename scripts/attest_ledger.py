"""Ledger-head attestation CLI — sign the audit chain's head, or verify a signature.

    # After a demo (or from cron), sign the current head:
    SANJEEVANI_ATTESTATION_KEY=<secret> python scripts/attest_ledger.py attest

    # Confirm the signature against the live ledger:
    SANJEEVANI_ATTESTATION_KEY=<secret> python scripts/attest_ledger.py verify \
        --file runtime/attestations/attest-00043-1a2b3c4d.json

    # Confirm offline, without the ledger (the file plus the key are enough):
    SANJEEVANI_ATTESTATION_KEY=<secret> python scripts/attest_ledger.py verify \
        --file runtime/attestations/attest-00043-1a2b3c4d.json --offline

The key NEVER goes into the attestation file and must not live on the ledger
volume — the whole value of the anchor is that the key and the ledger have
different attack surfaces.

WHY REAL WALL-CLOCK TIME. The demo freezes the scenario clock so runs are
byte-reproducible; an attestation's job is the opposite — to claim a position
in real time. So the timestamp here is UTC now, deliberately outside the
reproducibility guarantee, and the docstring in backend/audit/attestation.py
says what the timestamp is worth (exactly the key's storage).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.audit.attestation import (
    DEFAULT_KEY_ENV,
    AttestationError,
    attest_head,
    verify_attestation,
)
from backend.audit.ledger import ledger

DEFAULT_DIR = REPO_ROOT / "runtime" / "attestations"


def _resolve_key(args: argparse.Namespace) -> str:
    env_name = args.key_env or DEFAULT_KEY_ENV
    key = os.environ.get(env_name, "")
    if not key:
        print(
            f"ERROR: no attestation key. Set {env_name} (>= 32 random bytes). "
            "The key must NOT be stored on the ledger volume.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return key


def _ledger_state() -> tuple[str, int]:
    verdict = ledger.verify_chain()
    if not verdict["intact"]:
        print(
            f"ERROR: the ledger chain is BROKEN ({len(verdict['issues'])} issues); "
            "attesting a broken chain would launder it.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return str(verdict["head"] or ""), int(verdict["records"])


def cmd_attest(args: argparse.Namespace) -> int:
    key = _resolve_key(args)
    head, length = _ledger_state()
    attestation = attest_head(
        head_hash=head,
        length=length,
        key=key,
        key_id=args.key_id,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"attest-{length:05d}-{head[:8]}.json"
    out_path = out_dir / name
    out_path.write_text(json.dumps(attestation, indent=2) + "\n", encoding="utf-8")
    print(f"attested  head={head[:16]}…  records={length}")
    print(f"wrote     {out_path}")
    print(
        "remember  keep the key OFF the ledger volume; verify with:\n"
        f"          python scripts/attest_ledger.py verify --file {out_path}"
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    key = _resolve_key(args)
    attestation = json.loads(Path(args.file).read_text(encoding="utf-8"))
    try:
        if args.offline:
            verify_attestation(
                attestation,
                key=key,
                head_hash=str(attestation["head_hash"]),
                length=int(attestation["length"]),
            )
            print("verified  (offline: signature integrity only)")
            return 0
        _head, length = _ledger_state()
        attested_length = int(attestation["length"])
        if length < attested_length:
            raise AttestationError(
                f"the ledger ({length} records) is SHORTER than the attested "
                f"prefix ({attested_length}) — records were removed"
            )
        records = ledger.records()
        attested_head = records[attested_length - 1]["hash"]
        if length > attested_length:
            print(
                f"note      ledger has grown: {length} records now, attested prefix {attested_length}"
            )
        verify_attestation(
            attestation, key=key, head_hash=str(attested_head), length=attested_length
        )
        print(
            f"verified  attested head {str(attestation['head_hash'])[:16]}… is intact at "
            f"position {attested_length} of the live chain"
        )
        return 0
    except AttestationError as exc:
        print(f"FAILED    {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--key-env",
        default=DEFAULT_KEY_ENV,
        help=f"env var holding the key (default {DEFAULT_KEY_ENV})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_attest = sub.add_parser("attest", help="sign the current ledger head")
    p_attest.add_argument("--out-dir", default="", help="where to write the attestation JSON")
    p_attest.add_argument("--key-id", default="primary", help="label recorded inside the attestation")
    p_attest.set_defaults(func=cmd_attest)

    p_verify = sub.add_parser("verify", help="verify an attestation file")
    p_verify.add_argument("--file", required=True, help="path to the attestation JSON")
    p_verify.add_argument(
        "--offline", action="store_true", help="check signature integrity only, without the ledger"
    )
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
