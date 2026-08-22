"""Pure anytime portfolio state for a bounded Research Council Decision Cell."""

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Dict, List, Mapping, Sequence, Tuple

from lca_research_kernel.research_council_contracts import (
    _artifact,
    _reject_secret_keys,
    _text,
    compile_decision_cell,
    compile_incumbent,
    compile_progress_update,
)


_STALL_THRESHOLD = 2
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_CANDIDATE_INPUT_FIELDS = {
    "candidate_id",
    "conclusion",
    "validation_status",
    "scientific_importance",
    "flip_potential",
    "parallelizability",
    "expected_critical_path_seconds",
    "token_metrics",
}
_CANDIDATE_BASE_FIELDS = {
    "candidate_id",
    "conclusion",
    "validation_status",
    "routing_evidence",
}
_CANDIDATE_CORE_FIELDS = _CANDIDATE_BASE_FIELDS | {"candidate_content_sha256"}
_ROUTING_EVIDENCE_FIELDS = {
    "scientific_importance",
    "flip_potential",
    "parallelizability",
    "expected_critical_path_seconds",
    "token_metrics",
}
_PORTFOLIO_BODY_FIELDS = {
    "contract",
    "cell",
    "cell_ref",
    "incumbent",
    "dissent",
    "active_candidates",
    "parked_candidates",
    "configured_stall_threshold",
    "consecutive_stalls",
    "latest_progress_updates",
    "consumed_progress_ids",
    "progress_classification",
    "observed_at_utc",
    "cell_status",
    "next_route",
}
_PORTFOLIO_FIELDS = _PORTFOLIO_BODY_FIELDS | {
    "portfolio_content_sha256",
    "portfolio_id",
}
_REACTIVATION_CONDITION = (
    "A source-bound forward artifact makes this candidate newly admissible."
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _finish(body: Dict[str, object]) -> Dict[str, object]:
    digest = hashlib.sha256(_canonical_json(body)).hexdigest()
    result = dict(body)
    result["portfolio_content_sha256"] = digest
    result["portfolio_id"] = digest[:16]
    return result


def _validate_cell(value: object) -> Dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("a valid Decision Cell is required")
    try:
        expected = compile_decision_cell(
            agenda_ref=value["agenda_ref"],
            authority_receipt_ref=value["authority_receipt_ref"],
            decision_id=value["decision_id"],
            objective=value["objective"],
            input_artifacts=value["input_artifacts"],
            claims=value["claims"],
            roles=value["roles"],
            stop_conditions=value["stop_conditions"],
            flip_conditions=value["flip_conditions"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("a valid Decision Cell is required") from error
    if dict(value) != expected:
        raise ValueError("a valid Decision Cell is required")
    return expected


def _validate_incumbent(value: object) -> Dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("a valid incumbent is required")
    try:
        expected = compile_incumbent(
            cell_ref=value["cell_ref"],
            candidate_id=value["candidate_id"],
            conclusion=value["conclusion"],
            claim_refs=value["claim_refs"],
            evidence_artifacts=value["evidence_artifacts"],
            dissent=value["dissent"],
            validation_status=value["validation_status"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("a valid incumbent is required") from error
    if dict(value) != expected:
        raise ValueError("a valid incumbent is required")
    return expected


def _number(value: object, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field} must be a finite number")
    if normalized < 0 or (positive and normalized == 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{field} must be {qualifier}")
    return normalized


def _token_metrics(value: object, field: str) -> Dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} keys must be strings")
    metrics: Dict[str, object] = {}
    for key in sorted(value):
        name = _text(key, f"{field} key", casefold=True)
        if name not in {"input_tokens", "output_tokens", "total_tokens"}:
            raise ValueError(f"{field}.{name} is not an allowed token metric")
        metric = value[key]
        if isinstance(metric, bool) or not isinstance(metric, int) or metric < 0:
            raise ValueError(f"{field}.{name} must be a non-negative integer")
        metrics[name] = metric
    return metrics


def _candidate(value: object, index: int) -> Dict[str, object]:
    field = f"candidates[{index}]"
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} keys must be strings")
    _reject_secret_keys(
        {key: child for key, child in value.items() if key != "token_metrics"}
    )
    if set(value) != _CANDIDATE_INPUT_FIELDS:
        raise ValueError(f"{field} must contain exactly the approved candidate fields")

    candidate_id = _text(value.get("candidate_id"), f"{field}.candidate_id", casefold=True)
    conclusion = _text(value.get("conclusion"), f"{field}.conclusion")
    validation_status = _text(
        value.get("validation_status"), f"{field}.validation_status", casefold=True
    )
    if validation_status not in {"passed", "pending", "failed"}:
        raise ValueError(f"{field}.validation_status is invalid")
    routing_evidence = {
        "scientific_importance": _number(
            value.get("scientific_importance"), f"{field}.scientific_importance"
        ),
        "flip_potential": _number(
            value.get("flip_potential"), f"{field}.flip_potential"
        ),
        "parallelizability": _number(
            value.get("parallelizability"), f"{field}.parallelizability"
        ),
        "expected_critical_path_seconds": _number(
            value.get("expected_critical_path_seconds"),
            f"{field}.expected_critical_path_seconds",
            positive=True,
        ),
        "token_metrics": _token_metrics(
            value.get("token_metrics", {}), f"{field}.token_metrics"
        ),
    }
    candidate = {
        "candidate_id": candidate_id,
        "conclusion": conclusion,
        "validation_status": validation_status,
        "routing_evidence": routing_evidence,
    }
    candidate["candidate_content_sha256"] = hashlib.sha256(
        _canonical_json(candidate)
    ).hexdigest()
    return candidate


def _rank_key(candidate: Mapping[str, object]) -> Tuple[object, ...]:
    evidence = candidate["routing_evidence"]
    assert isinstance(evidence, Mapping)
    return (
        -float(evidence["scientific_importance"]),
        -float(evidence["flip_potential"]),
        -float(evidence["parallelizability"]),
        float(evidence["expected_critical_path_seconds"]),
        str(candidate["candidate_id"]),
    )


def _cell_ref(cell: Mapping[str, object]) -> Dict[str, object]:
    cell_id = str(cell["cell_id"])
    payload = _canonical_json(cell)
    return {
        "role": "decision_cell",
        "path": f"outputs/research-council-benchmark/cells/{cell_id}/contract.json",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def _active_candidate(candidate: Mapping[str, object], reason: str) -> Dict[str, object]:
    result = dict(candidate)
    result["promotion_reason"] = reason
    return result


def _parked_candidate(candidate: Mapping[str, object]) -> Dict[str, object]:
    result = {key: value for key, value in candidate.items() if key in _CANDIDATE_CORE_FIELDS}
    result["reactivation_condition"] = _REACTIVATION_CONDITION
    return result


def compile_portfolio(
    cell: Mapping[str, object],
    candidates: Sequence[Mapping[str, object]],
    incumbent: Mapping[str, object],
) -> Dict[str, object]:
    """Compile the deterministic initial anytime portfolio for one valid Cell."""
    valid_cell = _validate_cell(cell)
    valid_incumbent = _validate_incumbent(incumbent)
    expected_cell_ref = _cell_ref(valid_cell)
    if valid_incumbent["cell_ref"] != expected_cell_ref:
        raise ValueError("incumbent must bind the actual Decision Cell")
    if not isinstance(candidates, (list, tuple)) or not candidates:
        raise ValueError("candidates must be a non-empty list")
    normalized = [_candidate(value, index) for index, value in enumerate(candidates)]
    candidate_ids = [value["candidate_id"] for value in normalized]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("duplicate candidate_id is not allowed")

    ranked = sorted(normalized, key=_rank_key)
    active_ids = {value["candidate_id"] for value in ranked[:2]}
    minority = min(
        (value for value in ranked if value["candidate_id"] not in active_ids),
        key=lambda value: (
            -float(value["routing_evidence"]["flip_potential"]),
            str(value["candidate_id"]),
        ),
        default=None,
    )
    if minority is not None:
        active_ids.add(minority["candidate_id"])

    active: List[Dict[str, object]] = []
    parked: List[Dict[str, object]] = []
    for candidate in ranked:
        candidate_id = candidate["candidate_id"]
        if candidate_id in active_ids:
            reason = (
                "highest_flip_potential_minority"
                if minority is not None and candidate_id == minority["candidate_id"]
                else "leading_route"
            )
            active.append(_active_candidate(candidate, reason))
        else:
            parked.append(_parked_candidate(candidate))

    body: Dict[str, object] = {
        "contract": "lca_research_kernel.research_council_portfolio/v1",
        "cell": valid_cell,
        "cell_ref": expected_cell_ref,
        "incumbent": valid_incumbent,
        "dissent": valid_incumbent["dissent"],
        "active_candidates": active,
        "parked_candidates": parked,
        "configured_stall_threshold": _STALL_THRESHOLD,
        "consecutive_stalls": 0,
        "latest_progress_updates": [],
        "consumed_progress_ids": [],
        "progress_classification": "no_forward_change",
        "observed_at_utc": None,
        "cell_status": "active",
        "next_route": "continue-current-cell",
    }
    return _finish(body)


def _validated_progress_updates(
    values: object, expected_cell_ref: Mapping[str, object]
) -> List[Dict[str, object]]:
    if not isinstance(values, (list, tuple)):
        raise ValueError("progress_updates must be a list")
    updates: List[Dict[str, object]] = []
    seen = set()
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("every progress update must be a valid contract")
        try:
            expected = compile_progress_update(
                cell_ref=value["cell_ref"],
                changes=value["changes"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("every progress update must be a valid contract") from error
        if dict(value) != expected or expected["cell_ref"] != expected_cell_ref:
            raise ValueError("every progress update must be a valid contract")
        progress_id = expected["progress_id"]
        if progress_id in seen:
            raise ValueError("duplicate progress update is not allowed")
        seen.add(progress_id)
        updates.append(expected)
    return sorted(updates, key=lambda update: str(update["progress_id"]))


def _candidate_input_from_core(value: object, field: str) -> Dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} keys must be strings")
    if set(value) != _CANDIDATE_CORE_FIELDS:
        raise ValueError(f"{field} candidate core is invalid")
    evidence = value.get("routing_evidence")
    if not isinstance(evidence, Mapping) or set(evidence) != _ROUTING_EVIDENCE_FIELDS:
        raise ValueError(f"{field}.routing_evidence is invalid")
    return {
        "candidate_id": value["candidate_id"],
        "conclusion": value["conclusion"],
        "validation_status": value["validation_status"],
        "scientific_importance": evidence["scientific_importance"],
        "flip_potential": evidence["flip_potential"],
        "parallelizability": evidence["parallelizability"],
        "expected_critical_path_seconds": evidence[
            "expected_critical_path_seconds"
        ],
        "token_metrics": evidence["token_metrics"],
    }


def _validate_candidate_row(
    value: object, field: str, *, active: bool
) -> Dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} keys must be strings")
    reason = value.get("promotion_reason") if active else None
    expected_fields = _CANDIDATE_CORE_FIELDS | (
        {"promotion_reason"} if active else {"reactivation_condition"}
    )
    if reason == "reactivated_by_forward_change":
        expected_fields.add("reactivation_evidence")
    if set(value) != expected_fields:
        raise ValueError(f"{field} has a non-canonical candidate shape")
    core = {key: value[key] for key in _CANDIDATE_CORE_FIELDS}
    normalized_core = _candidate(_candidate_input_from_core(core, field), 0)
    if core != normalized_core:
        raise ValueError(f"{field} candidate core is not canonical")
    if active:
        if reason not in {
            "leading_route",
            "highest_flip_potential_minority",
            "reactivated_by_forward_change",
        }:
            raise ValueError(f"{field}.promotion_reason is invalid")
        if reason == "reactivated_by_forward_change":
            evidence = value["reactivation_evidence"]
            if not isinstance(evidence, Mapping) or set(evidence) != {
                "progress_id",
                "artifact_ref",
                "admissibility_status",
                "candidate_content_sha256",
            }:
                raise ValueError(f"{field}.reactivation_evidence is invalid")
            progress_id = evidence["progress_id"]
            if not isinstance(progress_id, str) or not _ID_RE.fullmatch(progress_id):
                raise ValueError(f"{field}.reactivation_evidence.progress_id is invalid")
            artifact_ref = _artifact(
                evidence["artifact_ref"],
                f"{field}.reactivation_evidence.artifact_ref",
                expected_role="candidate_admissibility",
            )
            if (
                evidence["artifact_ref"] != artifact_ref
                or evidence["admissibility_status"] != "passed"
                or evidence["candidate_content_sha256"]
                != core["candidate_content_sha256"]
            ):
                raise ValueError(f"{field}.reactivation_evidence is invalid")
    elif value["reactivation_condition"] != _REACTIVATION_CONDITION:
        raise ValueError(f"{field}.reactivation_condition is invalid")
    return dict(value)


def _validate_candidate_partition(
    active_values: object, parked_values: object
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    if not isinstance(active_values, (list, tuple)) or not isinstance(
        parked_values, (list, tuple)
    ):
        raise ValueError("portfolio candidates must be lists")
    active = [
        _validate_candidate_row(value, f"active_candidates[{index}]", active=True)
        for index, value in enumerate(active_values)
    ]
    parked = [
        _validate_candidate_row(value, f"parked_candidates[{index}]", active=False)
        for index, value in enumerate(parked_values)
    ]
    if not active:
        raise ValueError("at least one active candidate is required")
    if active != sorted(active, key=_rank_key) or parked != sorted(parked, key=_rank_key):
        raise ValueError("candidate rows must use canonical rank order")
    all_rows = active + parked
    candidate_ids = [row["candidate_id"] for row in all_rows]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate IDs must form one unique partition")

    ranked = sorted(all_rows, key=_rank_key)
    leader_ids = {row["candidate_id"] for row in ranked[:2]}
    minority = min(
        (row for row in ranked if row["candidate_id"] not in leader_ids),
        key=lambda row: (
            -float(row["routing_evidence"]["flip_potential"]),
            str(row["candidate_id"]),
        ),
        default=None,
    )
    protected_reasons = {candidate_id: "leading_route" for candidate_id in leader_ids}
    if minority is not None:
        protected_reasons[minority["candidate_id"]] = (
            "highest_flip_potential_minority"
        )
    active_by_id = {row["candidate_id"]: row for row in active}
    for candidate_id, reason in protected_reasons.items():
        if candidate_id not in active_by_id:
            raise ValueError("protected candidate routes must remain active")
        if active_by_id[candidate_id]["promotion_reason"] != reason:
            raise ValueError("protected candidate promotion reason is invalid")
    for row in active:
        if (
            row["candidate_id"] not in protected_reasons
            and row["promotion_reason"] != "reactivated_by_forward_change"
        ):
            raise ValueError("additional active routes must be authenticated recoveries")
    return active, parked


def _observed_at(value: object) -> str:
    timestamp = _text(value, "observed_at_utc")
    if not timestamp.endswith("Z"):
        raise ValueError("observed_at_utc must be UTC with a Z suffix")
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError("observed_at_utc must use YYYY-MM-DDTHH:MM:SSZ") from error
    return timestamp


def _validate_consumed_progress_ids(value: object) -> List[str]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("consumed_progress_ids must be a list")
    if any(not isinstance(progress_id, str) or not _ID_RE.fullmatch(progress_id) for progress_id in value):
        raise ValueError("consumed_progress_ids must contain progress IDs")
    normalized = sorted(value)
    if list(value) != normalized or len(normalized) != len(set(normalized)):
        raise ValueError("consumed_progress_ids must be sorted and unique")
    return normalized


def _validate_portfolio(value: object) -> Dict[str, object]:
    """Authenticate and semantically validate one complete portfolio snapshot."""
    if not isinstance(value, Mapping):
        raise ValueError("portfolio must be an object")
    if not all(isinstance(key, str) for key in value) or set(value) != _PORTFOLIO_FIELDS:
        raise ValueError("portfolio must contain exactly the v1 fields")
    body = {key: value[key] for key in _PORTFOLIO_BODY_FIELDS}
    if dict(value) != _finish(body):
        raise ValueError("portfolio content identity is invalid")
    if value["contract"] != "lca_research_kernel.research_council_portfolio/v1":
        raise ValueError("portfolio contract is invalid")

    cell = _validate_cell(value["cell"])
    expected_cell_ref = _cell_ref(cell)
    if value["cell_ref"] != expected_cell_ref:
        raise ValueError("portfolio cell_ref is invalid")
    incumbent = _validate_incumbent(value["incumbent"])
    if incumbent["cell_ref"] != expected_cell_ref:
        raise ValueError("incumbent must bind the actual Decision Cell")
    if value["dissent"] != incumbent["dissent"]:
        raise ValueError("portfolio dissent must equal incumbent dissent")

    if value["configured_stall_threshold"] != _STALL_THRESHOLD:
        raise ValueError("configured_stall_threshold is invalid")
    stalls = value["consecutive_stalls"]
    if isinstance(stalls, bool) or not isinstance(stalls, int) or stalls < 0:
        raise ValueError("consecutive_stalls must be a non-negative integer")
    active, parked = _validate_candidate_partition(
        value["active_candidates"], value["parked_candidates"]
    )

    updates = _validated_progress_updates(
        value["latest_progress_updates"], expected_cell_ref
    )
    if list(value["latest_progress_updates"]) != updates:
        raise ValueError("latest_progress_updates must use canonical order")
    consumed_ids = _validate_consumed_progress_ids(value["consumed_progress_ids"])
    latest_ids = {str(update["progress_id"]) for update in updates}
    if not latest_ids.issubset(consumed_ids):
        raise ValueError("latest progress updates must be recorded as consumed")
    for row in active:
        if row["promotion_reason"] == "reactivated_by_forward_change":
            reactivation_id = row["reactivation_evidence"]["progress_id"]
            if reactivation_id not in consumed_ids:
                raise ValueError("reactivation progress must be recorded as consumed")
    expected_classification = (
        "forward_change"
        if any(update["progress_classification"] == "forward_change" for update in updates)
        else "no_forward_change"
    )
    if value["progress_classification"] != expected_classification:
        raise ValueError("portfolio progress_classification is invalid")
    if expected_classification == "forward_change" and stalls != 0:
        raise ValueError("forward progress must reset consecutive stalls")

    observed_at = value["observed_at_utc"]
    if observed_at is None:
        if consumed_ids or updates or stalls != 0:
            raise ValueError("only an initial portfolio may omit observed_at_utc")
    else:
        _observed_at(observed_at)

    status = value["cell_status"]
    route = value["next_route"]
    if status == "active":
        expected_route = (
            "replan-current-cell"
            if stalls == _STALL_THRESHOLD
            else "continue-current-cell"
        )
        if stalls > _STALL_THRESHOLD or route != expected_route:
            raise ValueError("active portfolio routing state is invalid")
        if observed_at is not None and expected_classification == "no_forward_change" and stalls == 0:
            raise ValueError("an observed no-change round must increment stalls")
    elif status == "closed":
        if stalls <= _STALL_THRESHOLD or route != "close-current-cell":
            raise ValueError("closed portfolio routing state is invalid")
    else:
        raise ValueError("cell_status is invalid")

    normalized = dict(value)
    normalized["cell"] = cell
    normalized["cell_ref"] = expected_cell_ref
    normalized["incumbent"] = incumbent
    normalized["active_candidates"] = active
    normalized["parked_candidates"] = parked
    normalized["latest_progress_updates"] = updates
    normalized["consumed_progress_ids"] = consumed_ids
    return normalized


def _reactivate(
    active: Sequence[Mapping[str, object]],
    parked: Sequence[Mapping[str, object]],
    updates: Sequence[Mapping[str, object]],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    active_rows = [dict(value) for value in active]
    parked_rows = [dict(value) for value in parked]
    parked_by_id = {row["candidate_id"]: row for row in parked_rows}
    requested = {}
    for update in updates:
        if update["progress_classification"] != "forward_change":
            continue
        for change in update["changes"]:
            candidate_id = change.get("candidate_id")
            candidate = parked_by_id.get(candidate_id)
            artifact_ref = change.get("artifact_ref")
            if candidate is None or not isinstance(artifact_ref, Mapping):
                continue
            expected_digest = candidate["candidate_content_sha256"]
            if (
                change.get("kind") == "artifact"
                and change.get("admissibility_status") == "passed"
                and artifact_ref.get("role") == "candidate_admissibility"
                and isinstance(change.get("candidate_content_sha256"), str)
                and _SHA256_RE.fullmatch(change["candidate_content_sha256"])
                and change["candidate_content_sha256"] == expected_digest
            ):
                requested.setdefault(
                    candidate_id,
                    {
                        "progress_id": update["progress_id"],
                        "artifact_ref": artifact_ref,
                        "admissibility_status": "passed",
                        "candidate_content_sha256": expected_digest,
                    },
                )
    for candidate_id in sorted(requested):
        match = parked_by_id[candidate_id]
        parked_rows.remove(match)
        restored = {
            key: value for key, value in match.items() if key != "reactivation_condition"
        }
        restored["promotion_reason"] = "reactivated_by_forward_change"
        restored["reactivation_evidence"] = requested[candidate_id]
        active_rows.append(restored)
    active_rows.sort(key=_rank_key)
    parked_rows.sort(key=_rank_key)
    return active_rows, parked_rows


def advance_portfolio(
    portfolio: Mapping[str, object],
    progress_updates: Sequence[Mapping[str, object]],
    observed_at_utc: str,
) -> Dict[str, object]:
    """Advance stalls, replan, recovery, and terminal state from valid updates."""
    authenticated = _validate_portfolio(portfolio)
    valid_cell = authenticated["cell"]
    valid_incumbent = authenticated["incumbent"]
    expected_cell_ref = authenticated["cell_ref"]
    updates = _validated_progress_updates(progress_updates, expected_cell_ref)
    if authenticated["cell_status"] == "closed" and updates:
        raise ValueError("a closed Decision Cell cannot consume progress updates")
    consumed_ids = set(authenticated["consumed_progress_ids"])
    update_ids = {str(update["progress_id"]) for update in updates}
    replayed_ids = consumed_ids.intersection(update_ids)
    if replayed_ids:
        raise ValueError("progress update was already consumed")
    observed_at = _observed_at(observed_at_utc)
    threshold = authenticated["configured_stall_threshold"]
    prior_stalls = authenticated["consecutive_stalls"]

    forward_change = any(
        update["progress_classification"] == "forward_change" for update in updates
    )
    active = authenticated["active_candidates"]
    parked = authenticated["parked_candidates"]

    if authenticated["cell_status"] == "closed":
        cell_status = "closed"
        next_route = "close-current-cell"
        consecutive_stalls = prior_stalls
    elif forward_change:
        active, parked = _reactivate(active, parked, updates)
        cell_status = "active"
        next_route = "continue-current-cell"
        consecutive_stalls = 0
    elif prior_stalls >= threshold:
        cell_status = "closed"
        next_route = "close-current-cell"
        consecutive_stalls = prior_stalls + 1
    elif prior_stalls + 1 >= threshold:
        cell_status = "active"
        next_route = "replan-current-cell"
        consecutive_stalls = prior_stalls + 1
    else:
        cell_status = "active"
        next_route = "continue-current-cell"
        consecutive_stalls = prior_stalls + 1

    body: Dict[str, object] = {
        "contract": "lca_research_kernel.research_council_portfolio/v1",
        "cell": valid_cell,
        "cell_ref": expected_cell_ref,
        "incumbent": valid_incumbent,
        "dissent": valid_incumbent["dissent"],
        "active_candidates": active,
        "parked_candidates": parked,
        "configured_stall_threshold": threshold,
        "consecutive_stalls": consecutive_stalls,
        "latest_progress_updates": updates,
        "consumed_progress_ids": sorted(consumed_ids | update_ids),
        "progress_classification": (
            "forward_change" if forward_change else "no_forward_change"
        ),
        "observed_at_utc": observed_at,
        "cell_status": cell_status,
        "next_route": next_route,
    }
    return _finish(body)
