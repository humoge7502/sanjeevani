"""Guards on the test harness itself.

These are unusual tests: they check the *suite*, not the product. They exist
because a harness defect already caused real data loss once.

`orchestrator.reset()` truncates the ledger file, and several tests call it as
part of exercising normal behaviour. With the runtime directory pointed at the
repository, running `pytest` deleted the on-disk audit ledger -- a 43-record
chain came back as 4 lines. Nothing failed, because a test suite deleting
application state looks exactly like a test suite passing.

conftest.py redirects the runtime directory to a temp directory to fix that. These
tests make the redirect non-optional: if someone removes it, the suite fails here
rather than silently eating the ledger again.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from backend.config import REPO_ROOT, RUNTIME_DIR


@pytest.mark.unit
def test_runtime_dir_is_not_the_repository_runtime_directory():
    """The suite must not operate on the real runtime directory."""
    real = (REPO_ROOT / "runtime").resolve()
    assert RUNTIME_DIR.resolve() != real, (
        "tests are using the repository's runtime/ directory; a reset() will "
        "truncate the developer's real ledger. See tests/conftest.py."
    )


@pytest.mark.unit
def test_runtime_dir_is_a_temp_sandbox():
    """And it must be an isolated temp directory, not merely a different path."""
    resolved = RUNTIME_DIR.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    assert resolved.is_relative_to(temp_root), f"{resolved} is not under {temp_root}"
    assert resolved.name.startswith("sanjeevani-test-runtime-"), resolved.name


@pytest.mark.unit
def test_ledger_writes_go_to_the_sandbox_not_the_repository():
    """The ledger's resolved path follows the sandboxed runtime directory."""
    from backend.audit.ledger import ledger

    ledger_path = Path(ledger.path).resolve()
    assert ledger_path.is_relative_to(RUNTIME_DIR.resolve()), ledger_path
    assert not ledger_path.is_relative_to((REPO_ROOT / "runtime").resolve())


@pytest.mark.unit
def test_reset_does_not_reach_the_repository_ledger():
    """The concrete failure mode: reset() truncating the real file."""
    from backend.audit.ledger import ledger
    from backend.orchestrator import orchestrator

    real_file = REPO_ROOT / "runtime" / "audit_ledger.jsonl"
    before = real_file.stat().st_mtime_ns if real_file.is_file() else None

    orchestrator.reset()  # the call that used to destroy the real ledger

    after = real_file.stat().st_mtime_ns if real_file.is_file() else None
    assert after == before, "reset() touched the repository's real ledger file"
    assert Path(ledger.path).resolve() != real_file.resolve()
