#!/usr/bin/env python3
"""Generate a deterministic SBOM for SANJEEVANI at dist/sbom.json.

The inventory is built from the two installed dependency trees:

* Python: ``pip freeze`` of the interpreter that runs this script (runtime,
  dev and any extras present in the environment).
* Node: ``npm ls --prefix frontend --json --depth=0`` (the frontend's direct
  production and dev dependencies, exactly as the lockfile resolved them).

Honest scope note: this is a freeze-based inventory, not a Syft/CycloneDX
deep scan. Python components come from the environment (so they include the
transitive closure pip actually installed); Node components are the frontend's
direct dependencies at depth 0. It answers "what is installed right now",
which is what the verify gate needs; it does not claim binary-level provenance.

Deterministic by construction: no timestamps, package lists sorted by
normalized name, JSON with sort_keys. The same environment produces the same
bytes, so the file can be diffed or hashed across runs.

Usage:

    python scripts/make_sbom.py          # writes dist/sbom.json
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from shutil import which

REPO_ROOT = Path(__file__).resolve().parent.parent
SBOM_FORMAT = "sanjeevani-sbom-v1"


def _normalize_name(name: str) -> str:
    """PEP 503 normalization: case-insensitive, ``-``/``_``/``.`` collapse."""
    return re.sub(r"[-_.]+", "-", name).lower().strip()


def collect_python() -> list[dict[str, str]]:
    """Return the Python environment's installed components, sorted."""
    # The command is fixed, never user-supplied: full interpreter path + freeze.
    result = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--disable-pip-version-check"],
        capture_output=True,
        text=True,
        check=True,
    )
    components: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            name, _, version = line.partition("==")
            key = _normalize_name(name)
            components[key] = {"name": key, "version": version.strip()}
        else:
            # Direct-URL or editable installs (`pkg @ url`, `-e path`) carry no
            # clean version. They are recorded verbatim rather than dropped:
            # an inventory that silently omits packages is not an inventory.
            components[_normalize_name(line)] = {"name": line, "version": "unparsed"}
    return [components[k] for k in sorted(components)]


def collect_node(repo_root: Path) -> list[dict[str, str | bool]]:
    """Return the frontend's direct dependencies (prod + dev), sorted."""
    npm = which("npm")
    if npm is None:
        raise RuntimeError("npm not found on PATH; install Node to generate the SBOM")
    # The command is fixed, never user-supplied: the resolved npm path plus a
    # literal argument list, so the S603 suppression below is the honest reading.
    result = subprocess.run(  # noqa: S603 - resolved tool path, fixed literal args
        [npm, "ls", "--prefix", str(repo_root / "frontend"), "--json", "--depth=0"],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    tree = json.loads(result.stdout)
    components: list[dict[str, str | bool]] = []
    for name, info in sorted((tree.get("dependencies") or {}).items()):
        entry: dict[str, str | bool] = {
            "name": name,
            "version": str(info.get("version", "unknown")),
        }
        # Dev dependencies are part of the build-machine supply chain; record
        # the flag rather than merging the two lists.
        if info.get("dev"):
            entry["dev"] = True
        components.append(entry)
    return components


def main() -> int:
    dist = REPO_ROOT / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    python_components = collect_python()
    node_components = collect_node(REPO_ROOT)
    sbom = {
        "format": SBOM_FORMAT,
        "generator": "scripts/make_sbom.py",
        "scope": {
            "python": "pip freeze of the running interpreter (full installed environment)",
            "node": "npm ls --depth=0 of frontend/ (direct prod + dev dependencies)",
        },
        "components": {"node": node_components, "python": python_components},
    }
    out = dist / "sbom.json"
    out.write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"SBOM written to {out} "
        f"({len(python_components)} python, {len(node_components)} node components)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
