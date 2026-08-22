from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REGISTRY_PATH = Path("docs/lca-agent-d18-evaluation-fixtures.json")
P1_ACCEPTANCE_PATH = Path("docs/lca-agent-d18-p1-acceptance.json")
REQUIRED_CASE_FAMILIES = {"council", "nev", "lifepo4", "uslci"}
ROLES = {"historical_replay", "negative_safety", "positive_regression"}
ALLOWED_ROOTS = {"cases", "docs", "outputs", "tests"}


class FixtureRegistryError(ValueError):
    """Raised when a case fixture is absent or fails its content contract."""


@dataclass(frozen=True)
class FixtureSnapshot:
    fixture_id: str
    case_family: str
    role: str
    source_path: str
    sha256: str
    media_type: str
    payload: bytes
    architecture_authority: bool = False
    project_truth_writer: bool = False


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_registry(root: Path) -> dict[str, Any]:
    return json.loads((root / REGISTRY_PATH).read_text(encoding="utf-8"))


def _safe_fixture_path(root: Path, relative: object) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    if Path(relative).parts[0] not in ALLOWED_ROOTS:
        return None
    return candidate


def validate_fixture_registry(root: str | Path) -> list[str]:
    root = Path(root)
    errors: list[str] = []
    try:
        registry = _load_registry(root)
    except (OSError, json.JSONDecodeError):
        return ["D-18 evaluation fixture registry is unreadable"]

    if registry.get("contract") != "lca_research_kernel.d18_evaluation_fixture_registry/v1":
        errors.append("D-18 evaluation fixture registry identity drifted")

    basis = registry.get("p1_acceptance_ref", {})
    basis_path = root / str(basis.get("path", ""))
    if (
        basis.get("path") != P1_ACCEPTANCE_PATH.as_posix()
        or not basis_path.is_file()
        or basis.get("sha256") != _sha256(basis_path)
    ):
        errors.append("D-18 evaluation fixture P1 basis drifted")

    declared_families = registry.get("required_case_families")
    if set(declared_families or []) != REQUIRED_CASE_FAMILIES:
        errors.append("D-18 evaluation fixture family contract drifted")

    fixtures = registry.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        errors.append("D-18 evaluation fixture set is empty")
        fixtures = []
    ids: list[object] = []
    families: list[object] = []
    for row in fixtures:
        if not isinstance(row, dict):
            errors.append("D-18 evaluation fixture entry is malformed")
            continue
        ids.append(row.get("fixture_id"))
        families.append(row.get("case_family"))
        if row.get("role") not in ROLES:
            errors.append("D-18 evaluation fixture role is unsupported")
        if row.get("architecture_authority") is not False or row.get("project_truth_writer") is not False:
            errors.append(
                "evaluation fixture cannot define architecture or write project truth"
            )
        if not row.get("allowed_assertions") or not row.get("forbidden_claims"):
            errors.append("D-18 evaluation fixture claim boundary is incomplete")
        path = _safe_fixture_path(root, row.get("path"))
        if path is None or not path.is_file() or row.get("sha256") != _sha256(path):
            errors.append("evaluation fixture drifted")

    if len(ids) != len(set(ids)) or any(not isinstance(value, str) or not value for value in ids):
        errors.append("D-18 evaluation fixture identity is not unique")
    if set(families) != REQUIRED_CASE_FAMILIES:
        errors.append("D-18 evaluation fixture families are incomplete")
    invariants = registry.get("invariants", {})
    if invariants != {
        "case_imports_into_d18_core": 0,
        "fixture_project_record_writers": 0,
        "fixture_architecture_authorities": 0,
        "fixture_mutations": 0,
    }:
        errors.append("D-18 evaluation fixture invariants drifted")
    if registry.get("next_route") != "run-d18-evaluation-acceptance-tranche":
        errors.append("D-18 evaluation fixture next route drifted")
    return errors


class EvaluationFixtureHarness:
    """Reads immutable case evidence without importing it into the D-18 core."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        errors = validate_fixture_registry(self.root)
        if errors:
            raise FixtureRegistryError("; ".join(errors))
        self.registry = _load_registry(self.root)

    def read(self, fixture_id: str) -> FixtureSnapshot:
        row = next(
            (
                candidate
                for candidate in self.registry["fixtures"]
                if candidate["fixture_id"] == fixture_id
            ),
            None,
        )
        if row is None:
            raise FixtureRegistryError(f"unknown evaluation fixture: {fixture_id}")
        path = _safe_fixture_path(self.root, row["path"])
        if path is None or not path.is_file() or _sha256(path) != row["sha256"]:
            raise FixtureRegistryError("evaluation fixture drifted")
        return FixtureSnapshot(
            fixture_id=row["fixture_id"],
            case_family=row["case_family"],
            role=row["role"],
            source_path=row["path"],
            sha256=row["sha256"],
            media_type=row["media_type"],
            payload=path.read_bytes(),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate D-18 evaluation fixtures")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    errors = validate_fixture_registry(args.root)
    result = {
        "contract": "lca_research_kernel.d18_evaluation_fixture_check/v1",
        "error_count": len(errors),
        "errors": errors,
        "status": "passed" if not errors else "failed",
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
