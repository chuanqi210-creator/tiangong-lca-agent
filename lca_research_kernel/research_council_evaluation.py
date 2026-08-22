"""Fail-closed, deterministic compilers for Research Council evaluation."""

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Dict, List, Mapping, Sequence, Tuple


_ARMS = ("baseline", "council")
_GATES = ("authority_following", "provenance", "no_overclaim", "reproducibility")
_EFFECTS = ("credential_access", "external_write", "irreversible_change", "payment", "publication")
_TOOLS = ("official_result_api_read", "tiangong_cli_read@0.0.29")
_ALLOWED_EFFECTS = ("isolated_local_write", "read_only")
_AGENT_CAPABILITIES = ("exact_tiangong_read", "isolated_local_artifact_write")
_USER_RESPONSIBILITIES = (
    "credentials", "external_write", "irreversible_change", "payment",
    "publication", "unsupported_strong_public_claim",
)
_MECHANISMS = (
    "artifact_bus", "challenger_lane", "dissent_preservation",
    "independent_evidence_arbitration", "progressive_promotion", "stall_replan",
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")
_SECRET_KEYS = {
    "accesstoken", "apikey", "apitoken", "authorization", "clientsecret",
    "password", "passwd", "privatekey", "refreshtoken", "secret",
}
_SAFE_DIAGNOSTIC_KEYS = {
    "credentialaccess", "diagnostictokenusage", "inputtokens", "outputtokens",
    "totaltokens", "tokencount",
}
_SECRET_VALUES = (
    re.compile(r"authorization\s*:", re.I),
    re.compile(r"bearer\s+[a-z0-9._~+/=-]+", re.I),
    re.compile(r"(?:api[_ -]?key|password|secret|token|credentials?)\s*[=:]", re.I),
)

_NEUTRAL_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NON_NEUTRAL_ID_MARKERS = ("baseline", "council", "singleagent", "researchdirector", "arm", "source")


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _finish(body: Dict[str, object], noun: str) -> Dict[str, object]:
    digest = hashlib.sha256(_canonical(body)).hexdigest()
    return {**body, f"{noun}_content_sha256": digest, f"{noun}_id": digest[:16]}


def _text(value: object, field: str, *, casefold: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    normalized = " ".join(value.split())
    return normalized.casefold() if casefold else normalized


def _neutral_id(value: object, field: str) -> str:
    normalized = _text(value, field, casefold=True)
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    if (len(normalized) > 64 or not _NEUTRAL_ID_RE.fullmatch(normalized)
            or any(marker in compact for marker in _NON_NEUTRAL_ID_MARKERS)):
        raise ValueError(f"{field} must be a neutral safe slug")
    return normalized


def _finite(value: object, field: str, *, integer: bool = False) -> object:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"{field} must be a non-negative finite number")
    if integer and not isinstance(value, int):
        raise ValueError(f"{field} must be a non-negative integer")
    return value if integer else float(value)


def _reject_secrets(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("mapping keys must be strings")
            comparable = re.sub(r"[^a-z0-9]", "", key.casefold())
            if comparable not in _SAFE_DIAGNOSTIC_KEYS and (
                comparable in _SECRET_KEYS or comparable.endswith("token") or comparable.endswith("credentials")
            ):
                raise ValueError("secret-like key is not allowed")
            _reject_secrets(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secrets(child)
    elif isinstance(value, str) and any(pattern.search(value) for pattern in _SECRET_VALUES):
        raise ValueError("secret-like value is not allowed")


def _utc(value: object, field: str) -> Tuple[str, datetime]:
    stamp = _text(value, field)
    if not stamp.endswith("Z"):
        raise ValueError(f"{field} must end in Z")
    try:
        parsed = datetime.fromisoformat(stamp[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{field} must be a valid UTC datetime") from error
    canonical = parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if stamp != canonical:
        raise ValueError(f"{field} must be canonical second-precision UTC")
    return canonical, parsed


def _artifact(value: object, field: str, *, role: str = "", prefix: str = "") -> Dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"role", "path", "sha256", "bytes"}:
        raise ValueError(f"{field} must be an exact artifact reference")
    normalized_role = _text(value["role"], f"{field}.role", casefold=True)
    if role and normalized_role != role:
        raise ValueError(f"{field}.role must be {role}")
    path = _text(value["path"], f"{field}.path")
    parts = PurePosixPath(path).parts
    if ("\\" in path or "\x00" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path)
            or not parts or any(part in {"", ".", ".."} for part in parts)):
        raise ValueError(f"{field}.path must be a safe workspace-relative path")
    path = str(PurePosixPath(path))
    if prefix and not path.startswith(prefix.rstrip("/") + "/"):
        raise ValueError(f"{field}.path must be isolated under {prefix}")
    digest, size = value["sha256"], value["bytes"]
    if not isinstance(digest, str) or not _SHA_RE.fullmatch(digest):
        raise ValueError(f"{field}.sha256 must be an exact SHA-256")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError(f"{field}.bytes must be a positive integer")
    return {"role": normalized_role, "path": path, "sha256": digest, "bytes": size}


def _uuid(value: object, field: str) -> str:
    normalized = _text(value, field, casefold=True)
    if not _UUID_RE.fullmatch(normalized):
        raise ValueError(f"{field} must be an exact UUID")
    return normalized


def _version(value: object, field: str) -> str:
    normalized = _text(value, field)
    if not _VERSION_RE.fullmatch(normalized):
        raise ValueError(f"{field} must be an exact dotted version")
    return normalized


def _functional_unit(value: object, field: str) -> Dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"amount", "unit"}:
        raise ValueError(f"{field} must contain exactly amount and unit")
    amount = _finite(value["amount"], f"{field}.amount")
    if float(amount) <= 0:
        raise ValueError(f"{field}.amount must be positive")
    return {"amount": amount, "unit": _text(value["unit"], f"{field}.unit")}


def _member(value: object, field: str) -> Dict[str, object]:
    keys = {"process_id", "process_version", "product_system_label", "reference_flow_id",
            "reference_flow_version", "functional_unit", "native_result", "boundary"}
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{field} must be an exact TianGong member")
    result = value["native_result"]
    result_keys = {"result_id", "result_version", "method_id", "method_version", "category_id",
                   "category_version", "result_unit", "origin"}
    if not isinstance(result, Mapping) or set(result) != result_keys:
        raise ValueError(f"{field}.native_result must be exact")
    origin = _text(result["origin"], f"{field}.native_result.origin", casefold=True)
    if origin not in {"official_api_result", "official_calculation_bundle", "official_tidas_zip_result"}:
        raise ValueError("native result must have an official TianGong origin")
    native = {
        "result_id": _uuid(result["result_id"], f"{field}.native_result.result_id"),
        "result_version": _version(result["result_version"], f"{field}.native_result.result_version"),
        "method_id": _uuid(result["method_id"], f"{field}.native_result.method_id"),
        "method_version": _version(result["method_version"], f"{field}.native_result.method_version"),
        "category_id": _uuid(result["category_id"], f"{field}.native_result.category_id"),
        "category_version": _version(result["category_version"], f"{field}.native_result.category_version"),
        "result_unit": _text(result["result_unit"], f"{field}.native_result.result_unit"),
        "origin": origin,
    }
    return {
        "process_id": _uuid(value["process_id"], f"{field}.process_id"),
        "process_version": _version(value["process_version"], f"{field}.process_version"),
        "product_system_label": _text(value["product_system_label"], f"{field}.product_system_label"),
        "reference_flow_id": _uuid(value["reference_flow_id"], f"{field}.reference_flow_id"),
        "reference_flow_version": _version(value["reference_flow_version"], f"{field}.reference_flow_version"),
        "functional_unit": _functional_unit(value["functional_unit"], f"{field}.functional_unit"),
        "native_result": native,
        "boundary": _text(value["boundary"], f"{field}.boundary"),
    }


def _validate_freeze(value: object) -> Dict[str, object]:
    fields = {"contract", "qualification_id", "cluster_id", "exact_tiangong_members",
              "tiangong_authority_evidence_ref", "cluster_evidence_ref", "functional_unit_treatment",
              "task_prompt", "tools", "allowed_effects", "initial_evidence_set", "controller_version",
              "responsibility_boundary", "next_route", "freeze_content_sha256", "freeze_id"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("an exact authenticated benchmark freeze is required")
    _reject_secrets(value)
    if value["contract"] != "lca_research_kernel.research_council_benchmark_freeze/v1":
        raise ValueError("invalid benchmark freeze contract")
    qualification_id = _text(value["qualification_id"], "qualification_id", casefold=True)
    if not _ID_RE.fullmatch(qualification_id):
        raise ValueError("qualification_id must be content-derived")
    members = [_member(row, f"exact_tiangong_members[{index}]") for index, row in enumerate(value["exact_tiangong_members"])] if isinstance(value["exact_tiangong_members"], (list, tuple)) else []
    if len(members) < 2 or members != sorted(members, key=_canonical):
        raise ValueError("exact TianGong members must be a canonical comparison set")
    member_keys = {(row["process_id"], row["process_version"]) for row in members}
    if len(member_keys) != len(members):
        raise ValueError("duplicate exact TianGong members are not allowed")
    unit = _functional_unit(value["functional_unit_treatment"], "functional_unit_treatment")
    if any(row["functional_unit"] != unit for row in members):
        raise ValueError("member functional units must equal the frozen treatment")
    authority = _artifact(value["tiangong_authority_evidence_ref"], "tiangong_authority_evidence_ref", role="tiangong_authority_evidence")
    cluster = _artifact(value["cluster_evidence_ref"], "cluster_evidence_ref", role="benchmark_cluster_evidence")
    evidence = [_artifact(row, f"initial_evidence_set[{index}]") for index, row in enumerate(value["initial_evidence_set"])] if isinstance(value["initial_evidence_set"], (list, tuple)) else []
    if not evidence or evidence != sorted(evidence, key=_canonical) or len({_canonical(row) for row in evidence}) != len(evidence):
        raise ValueError("initial evidence must be canonical and unique")
    if sum(row["role"] == "evaluation_rubric" for row in evidence) != 1:
        raise ValueError("freeze must contain exactly one typed evaluation_rubric")
    if list(value["tools"]) != list(_TOOLS) or list(value["allowed_effects"]) != list(_ALLOWED_EFFECTS):
        raise ValueError("freeze must use only the exact safe tools and effects")
    controller = value["controller_version"]
    if not isinstance(controller, Mapping) or set(controller) != {"git_commit", "controller_sha256", "skill_sha256", "state_contract_sha256"}:
        raise ValueError("controller_version must be exact")
    normalized_controller = {key: _text(controller[key], f"controller_version.{key}", casefold=True) for key in sorted(controller)}
    if not re.fullmatch(r"[0-9a-f]{40}", normalized_controller["git_commit"]) or any(not _SHA_RE.fullmatch(normalized_controller[key]) for key in ("controller_sha256", "skill_sha256", "state_contract_sha256")):
        raise ValueError("controller_version hashes are invalid")
    boundary = value["responsibility_boundary"]
    if not isinstance(boundary, Mapping) or set(boundary) != {"agent_may", "user_required_for"} or list(boundary["agent_may"]) != list(_AGENT_CAPABILITIES) or list(boundary["user_required_for"]) != list(_USER_RESPONSIBILITIES):
        raise ValueError("responsibility boundary must equal the accepted contract")
    if value["next_route"] != "run-frozen-single-agent-baseline":
        raise ValueError("freeze must have the single accepted next route")
    body = {
        "contract": value["contract"], "qualification_id": qualification_id,
        "cluster_id": _text(value["cluster_id"], "cluster_id", casefold=True),
        "exact_tiangong_members": members, "tiangong_authority_evidence_ref": authority,
        "cluster_evidence_ref": cluster, "functional_unit_treatment": unit,
        "task_prompt": _text(value["task_prompt"], "task_prompt"), "tools": list(_TOOLS),
        "allowed_effects": list(_ALLOWED_EFFECTS), "initial_evidence_set": evidence,
        "controller_version": normalized_controller,
        "responsibility_boundary": {"agent_may": list(_AGENT_CAPABILITIES), "user_required_for": list(_USER_RESPONSIBILITIES)},
        "next_route": "run-frozen-single-agent-baseline",
    }
    expected = _finish(body, "freeze")
    if dict(value) != expected:
        raise ValueError("benchmark freeze fields or content identity are invalid")
    return expected


def _run_prefix(freeze_id: str, trial_id: str, arm: str) -> str:
    return f"research-council-benchmark/{freeze_id}/{trial_id}/{arm}"


def _harness_observation(value: object, field: str, prefix: str) -> Tuple[Dict[str, object], datetime]:
    if not isinstance(value, Mapping) or set(value) != {"observed_by", "observed_at_utc", "harness_receipt_ref"} or value["observed_by"] != "runtime_harness":
        raise ValueError(f"{field} must be an exact runtime-harness observation")
    stamp, parsed = _utc(value["observed_at_utc"], f"{field}.observed_at_utc")
    return {"observed_by": "runtime_harness", "observed_at_utc": stamp,
            "harness_receipt_ref": _artifact(value["harness_receipt_ref"], f"{field}.harness_receipt_ref", role="run_harness_receipt", prefix=prefix)}, parsed


def _token_usage(value: object) -> Dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != {"input_tokens", "output_tokens", "total_tokens"}:
        raise ValueError("diagnostic_token_usage must be exact")
    result = {key: int(_finite(value[key], f"diagnostic_token_usage.{key}", integer=True)) for key in ("input_tokens", "output_tokens", "total_tokens")}
    if result["total_tokens"] != result["input_tokens"] + result["output_tokens"]:
        raise ValueError("diagnostic total_tokens must equal input plus output")
    return result


def compile_run_trace(*, freeze: Mapping[str, object], arm: str, trial_id: str,
                      timeline: Sequence[Mapping[str, object]], final_package: Mapping[str, object],
                      effects: Mapping[str, object]) -> Dict[str, object]:
    frozen = _validate_freeze(freeze)
    normalized_arm = _text(arm, "arm", casefold=True)
    trial = _neutral_id(trial_id, "trial_id")
    if normalized_arm not in _ARMS:
        raise ValueError("arm must be baseline or council")
    prefix = _run_prefix(frozen["freeze_id"], trial, normalized_arm)
    _reject_secrets((timeline, final_package))
    final_fields = {"freeze_id", "trial_id", "arm", "artifact_ref", "run_harness_receipt_ref",
                    "hard_gates", "opposing_arm_access", "started_observation", "terminal_observation",
                    "diagnostic_token_usage"}
    if not isinstance(final_package, Mapping) or set(final_package) != final_fields:
        raise ValueError("final_package must have the exact terminal schema")
    if final_package["freeze_id"] != frozen["freeze_id"] or final_package["trial_id"] != trial or final_package["arm"] != normalized_arm:
        raise ValueError("final package does not bind the frozen arm and trial")
    if final_package["opposing_arm_access"] is not False:
        raise ValueError("opposing-arm access is prohibited")
    gates = final_package["hard_gates"]
    if not isinstance(gates, Mapping) or set(gates) != set(_GATES):
        raise ValueError("all noncompensable hard gates are required")
    gates = {gate: _text(gates[gate], f"hard_gates.{gate}", casefold=True) for gate in _GATES}
    if any(result != "passed" for result in gates.values()):
        raise ValueError("all noncompensable hard gates must pass")
    start, start_dt = _harness_observation(final_package["started_observation"], "started_observation", prefix)
    terminal, terminal_dt = _harness_observation(final_package["terminal_observation"], "terminal_observation", prefix)
    if terminal_dt <= start_dt:
        raise ValueError("terminal observation must follow start")
    package_ref = _artifact(final_package["artifact_ref"], "final_package.artifact_ref", role="final_package", prefix=prefix)
    run_receipt = _artifact(final_package["run_harness_receipt_ref"], "final_package.run_harness_receipt_ref", role="run_harness_receipt", prefix=prefix)
    token_usage = _token_usage(final_package["diagnostic_token_usage"])
    if not isinstance(effects, Mapping) or set(effects) != set(_EFFECTS):
        raise ValueError("effects must report every prohibited effect")
    normalized_effects: Dict[str, int] = {}
    for name in _EFFECTS:
        count = _finite(effects[name], f"effects.{name}", integer=True)
        if count != 0:
            raise ValueError("every prohibited effect count must be zero")
        normalized_effects[name] = 0
    if not isinstance(timeline, (list, tuple)) or not timeline:
        raise ValueError("timeline must be non-empty")
    normalized_events: List[Dict[str, object]] = []
    dated_events: List[Tuple[datetime, Dict[str, object]]] = []
    recovery_events = 0
    idle_intervals: List[Tuple[datetime, datetime]] = []
    for index, raw in enumerate(timeline):
        if not isinstance(raw, Mapping):
            raise ValueError("timeline events must be objects")
        kind = _text(raw.get("kind"), f"timeline[{index}].kind", casefold=True)
        if kind == "idle_wait":
            if set(raw) not in ({"kind", "start_observation", "end_observation"}, {"kind", "start_observation", "end_observation", "duration_seconds"}):
                raise ValueError("idle_wait must contain exact start/end observations")
            idle_start, idle_start_dt = _harness_observation(raw["start_observation"], f"timeline[{index}].start", prefix)
            idle_end, idle_end_dt = _harness_observation(raw["end_observation"], f"timeline[{index}].end", prefix)
            if idle_start_dt < start_dt or idle_end_dt > terminal_dt or idle_end_dt < idle_start_dt:
                raise ValueError("idle wait is inverted or outside the run")
            duration = (idle_end_dt - idle_start_dt).total_seconds()
            if "duration_seconds" in raw and _finite(raw["duration_seconds"], f"timeline[{index}].duration_seconds") != duration:
                raise ValueError("idle wait duration must be derived from its observations")
            if duration > (terminal_dt - start_dt).total_seconds():
                raise ValueError("idle wait exceeds run duration")
            event = {"kind": kind, "start_observation": idle_start, "end_observation": idle_end, "duration_seconds": duration}
            idle_intervals.append((idle_start_dt, idle_end_dt))
            dated_events.append((idle_start_dt, event)); normalized_events.append(event)
            continue
        common = {"kind", "observed_by", "observed_at_utc", "harness_receipt_ref"}
        shapes = {
            "first_forward_artifact": common | {"artifact_ref"},
            "tool_call": common | {"tool"}, "stall": common | {"reason"},
            "replan": common | {"reason"},
            "recovery": common | {"recovery_ref"},
            "first_defensible_conclusion": common | {"artifact_ref"},
            "final_convergence": common | {"artifact_ref"},
        }
        if kind not in shapes or set(raw) != shapes[kind] or raw["observed_by"] != "runtime_harness":
            raise ValueError(f"timeline[{index}] has an invalid harness event shape")
        observation, observed_dt = _harness_observation({"observed_by": raw["observed_by"], "observed_at_utc": raw["observed_at_utc"], "harness_receipt_ref": raw["harness_receipt_ref"]}, f"timeline[{index}]", prefix)
        if observed_dt < start_dt or observed_dt > terminal_dt:
            raise ValueError("timeline observation is outside the run")
        event = {"kind": kind, **observation}
        if "artifact_ref" in raw:
            expected_role = {"first_forward_artifact": "forward_artifact", "first_defensible_conclusion": "defensible_conclusion", "final_convergence": "final_package"}[kind]
            event["artifact_ref"] = _artifact(raw["artifact_ref"], f"timeline[{index}].artifact_ref", role=expected_role, prefix=prefix)
        if "tool" in raw:
            event["tool"] = _text(raw["tool"], f"timeline[{index}].tool", casefold=True)
            if event["tool"] not in frozen["tools"]:
                raise ValueError("tool call is outside the freeze")
        if "reason" in raw:
            event["reason"] = _text(raw["reason"], f"timeline[{index}].reason")
        if "recovery_ref" in raw:
            event["recovery_ref"] = _artifact(raw["recovery_ref"], f"timeline[{index}].recovery_ref", role="recovery_artifact", prefix=prefix)
            recovery_events += 1
        dated_events.append((observed_dt, event)); normalized_events.append(event)
    if [dt for dt, _ in dated_events] != sorted(dt for dt, _ in dated_events):
        raise ValueError("timeline must be chronological")
    ordered_idle = sorted(idle_intervals)
    if any(current_start < previous_end for (_, previous_end), (current_start, _) in zip(ordered_idle, ordered_idle[1:])):
        raise ValueError("idle intervals must not overlap or duplicate")
    active_during_idle = {"tool_call", "stall", "replan", "recovery", "first_defensible_conclusion"}
    for observed_dt, event in dated_events:
        if event["kind"] in active_during_idle and any(start < observed_dt < end for start, end in ordered_idle):
            raise ValueError("active harness event occurs inside an idle interval")
    for boundary_kind in ("first_forward_artifact", "first_defensible_conclusion", "final_convergence"):
        if sum(event["kind"] == boundary_kind for event in normalized_events) != 1:
            raise ValueError(f"timeline must contain exactly one {boundary_kind}")
    if normalized_events[0]["kind"] != "first_forward_artifact" or normalized_events[-1]["kind"] != "final_convergence":
        raise ValueError("timeline boundary order is invalid")
    last = normalized_events[-1]
    if last["artifact_ref"] != package_ref or last["harness_receipt_ref"] != terminal["harness_receipt_ref"] or last["observed_at_utc"] != terminal["observed_at_utc"]:
        raise ValueError("terminal package must exactly bind the final convergence observation")
    def elapsed(kind: str) -> float:
        dt = next(dt for dt, event in dated_events if event["kind"] == kind)
        return (dt - start_dt).total_seconds()
    rubric = next(row for row in frozen["initial_evidence_set"] if row["role"] == "evaluation_rubric")
    normalized_final = {
        "freeze_id": frozen["freeze_id"], "trial_id": trial, "arm": normalized_arm,
        "artifact_ref": package_ref, "run_harness_receipt_ref": run_receipt,
        "hard_gates": gates, "opposing_arm_access": False,
        "started_observation": start, "terminal_observation": terminal,
        "diagnostic_token_usage": token_usage,
    }
    body: Dict[str, object] = {
        "contract": "lca_research_kernel.research_council_run_trace/v2",
        "freeze": frozen, "freeze_id": frozen["freeze_id"], "trial_id": trial, "arm": normalized_arm,
        "rubric_ref": rubric, "timeline": normalized_events, "final_package": normalized_final,
        "effects": normalized_effects, "started_observation": start, "terminal_observation": terminal,
        "hard_gates": gates, "prohibited_effects": normalized_effects,
        "first_forward_artifact_seconds": elapsed("first_forward_artifact"),
        "first_defensible_conclusion_seconds": elapsed("first_defensible_conclusion"),
        "final_convergence_seconds": elapsed("final_convergence"),
        "critical_path_seconds": elapsed("final_convergence"),
        "stall_count": sum(event["kind"] == "stall" for event in normalized_events),
        "replan_count": sum(event["kind"] == "replan" for event in normalized_events),
        "tool_call_count": sum(event["kind"] == "tool_call" for event in normalized_events),
        "idle_wait_seconds": sum((end - start).total_seconds() for start, end in ordered_idle),
        "diagnostics": {"diagnostic_token_usage": token_usage, "recovery_count": recovery_events},
        "next_route": "score-frozen-quality-checkpoints",
    }
    return _finish(body, "trace")


def _validate_trace(value: object, expected_arm: str = "") -> Dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("a valid run trace is required")
    required_raw = {"freeze", "arm", "trial_id", "timeline", "final_package", "effects"}
    if not required_raw.issubset(value):
        raise ValueError("trace lacks reconstructable observations")
    try:
        expected = compile_run_trace(freeze=value["freeze"], arm=value["arm"], trial_id=value["trial_id"], timeline=value["timeline"], final_package=value["final_package"], effects=value["effects"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("a semantically valid run trace is required") from error
    if dict(value) != expected or (expected_arm and expected["arm"] != expected_arm):
        raise ValueError("run trace schema, semantics, or arm is invalid")
    return expected


def _trace_artifacts(trace: Mapping[str, object]) -> set:
    refs = set()
    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            if set(value) == {"role", "path", "sha256", "bytes"}:
                refs.add(_canonical(value))
            else:
                for child in value.values(): visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value: visit(child)
    visit(trace["timeline"]); visit(trace["final_package"])
    return refs


def _paired_traces(baseline_traces: Sequence[Mapping[str, object]], council_traces: Sequence[Mapping[str, object]]) -> List[Tuple[Dict[str, object], Dict[str, object]]]:
    if not isinstance(baseline_traces, (list, tuple)) or not isinstance(council_traces, (list, tuple)):
        raise ValueError("trace inputs must be lists")
    left_rows = [_validate_trace(row, "baseline") for row in baseline_traces]
    right_rows = [_validate_trace(row, "council") for row in council_traces]
    def keyed(rows):
        result = {}
        for row in rows:
            key = (row["freeze_id"], row["trial_id"])
            if key in result: raise ValueError("duplicate trace pair key")
            result[key] = row
        return result
    left, right = keyed(left_rows), keyed(right_rows)
    if not left or set(left) != set(right):
        raise ValueError("baseline and Council traces must be exactly paired")
    pairs = [(left[key], right[key]) for key in sorted(left)]
    for baseline, council in pairs:
        if baseline["freeze"] != council["freeze"] or baseline["rubric_ref"] != council["rubric_ref"]:
            raise ValueError("paired traces must share one authenticated freeze and rubric")
        if _trace_artifacts(baseline) & _trace_artifacts(council):
            raise ValueError("paired arm artifact identities must be disjoint")
    return pairs


def _checkpoint(raw: object, index: int, trace_map: Mapping[Tuple[str, str, str, str], Mapping[str, object]]) -> Dict[str, object]:
    fields = {"freeze_id", "trial_id", "arm", "trace_id", "rubric_ref", "observed_by",
              "observed_at_utc", "harness_receipt_ref", "score", "self_score", "token_count"}
    if not isinstance(raw, Mapping) or set(raw) != fields:
        raise ValueError(f"quality_checkpoints[{index}] must use the exact schema")
    _reject_secrets(raw)
    key = (_text(raw["freeze_id"], "freeze_id", casefold=True), _neutral_id(raw["trial_id"], "trial_id"),
           _text(raw["arm"], "arm", casefold=True), _text(raw["trace_id"], "trace_id", casefold=True))
    if key not in trace_map:
        raise ValueError("every checkpoint must bind a known trace")
    trace = trace_map[key]
    rubric = _artifact(raw["rubric_ref"], "rubric_ref", role="evaluation_rubric")
    if rubric != trace["rubric_ref"]:
        raise ValueError("checkpoint rubric differs from the frozen evaluation rubric")
    if raw["observed_by"] != "runtime_harness":
        raise ValueError("quality checkpoint must be harness observed")
    stamp, stamp_dt = _utc(raw["observed_at_utc"], "quality observed_at_utc")
    start_dt = _utc(trace["started_observation"]["observed_at_utc"], "trace start")[1]
    end_dt = _utc(trace["terminal_observation"]["observed_at_utc"], "trace terminal")[1]
    if stamp_dt < start_dt or stamp_dt > end_dt:
        raise ValueError("quality checkpoint lies outside its run")
    prefix = _run_prefix(key[0], key[1], key[2])
    return {"freeze_id": key[0], "trial_id": key[1], "arm": key[2], "trace_id": key[3],
            "rubric_ref": rubric, "observed_by": "runtime_harness", "observed_at_utc": stamp,
            "harness_receipt_ref": _artifact(raw["harness_receipt_ref"], "quality harness_receipt_ref", role="run_harness_receipt", prefix=prefix),
            "score": _finite(raw["score"], "score"), "self_score": _finite(raw["self_score"], "self_score"),
            "token_count": int(_finite(raw["token_count"], "token_count", integer=True))}


def compile_quality_time_comparison(*, baseline_traces: Sequence[Mapping[str, object]], council_traces: Sequence[Mapping[str, object]], quality_checkpoints: Sequence[Mapping[str, object]]) -> Dict[str, object]:
    pairs = _paired_traces(baseline_traces, council_traces)
    if not isinstance(quality_checkpoints, (list, tuple)) or not quality_checkpoints:
        raise ValueError("quality checkpoints must be non-empty")
    trace_map = {(trace["freeze_id"], trace["trial_id"], trace["arm"], trace["trace_id"]): trace for pair in pairs for trace in pair}
    checkpoints = [_checkpoint(raw, index, trace_map) for index, raw in enumerate(quality_checkpoints)]
    if len({_canonical(row) for row in checkpoints}) != len(checkpoints):
        raise ValueError("duplicate checkpoints are not allowed")
    checkpoints.sort(key=lambda row: (row["freeze_id"], row["trial_id"], row["arm"], row["observed_at_utc"], _canonical(row)))
    comparison_rows = []
    for baseline, council in pairs:
        base_rows = [row for row in checkpoints if row["trace_id"] == baseline["trace_id"]]
        council_rows = [row for row in checkpoints if row["trace_id"] == council["trace_id"]]
        if len(base_rows) != 1 or not council_rows:
            raise ValueError("each pair needs exactly one baseline-final and Council checkpoints")
        base = base_rows[0]
        if base["observed_at_utc"] != baseline["terminal_observation"]["observed_at_utc"]:
            raise ValueError("baseline quality must be observed exactly at terminal convergence")
        eligible = [row for row in council_rows if row["score"] >= base["score"]]
        eligible.sort(key=lambda row: (row["observed_at_utc"], _canonical(row)))
        matched = eligible[0] if eligible else None
        start_dt = _utc(council["started_observation"]["observed_at_utc"], "Council start")[1]
        matched_seconds = None if matched is None else (_utc(matched["observed_at_utc"], "matched quality")[1] - start_dt).total_seconds()
        comparison_rows.append({"freeze_id": baseline["freeze_id"], "trial_id": baseline["trial_id"],
                                "baseline_trace_id": baseline["trace_id"], "council_trace_id": council["trace_id"],
                                "baseline_final_score": base["score"], "baseline_time_to_final_quality_seconds": baseline["final_convergence_seconds"],
                                "council_time_to_baseline_final_quality_seconds": matched_seconds})
    advantage = all(row["council_time_to_baseline_final_quality_seconds"] is not None and row["council_time_to_baseline_final_quality_seconds"] < row["baseline_time_to_final_quality_seconds"] for row in comparison_rows)
    body = {"contract": "lca_research_kernel.research_council_quality_time/v2",
            "baseline_trace_ids": sorted(pair[0]["trace_id"] for pair in pairs),
            "council_trace_ids": sorted(pair[1]["trace_id"] for pair in pairs),
            "rubric_ref": pairs[0][0]["rubric_ref"], "quality_checkpoints": checkpoints,
            "paired_comparisons": comparison_rows,
            "baseline_time_to_final_quality_seconds": sum(float(row["baseline_time_to_final_quality_seconds"]) for row in comparison_rows) / len(comparison_rows),
            "council_time_to_baseline_final_quality_seconds": None if any(row["council_time_to_baseline_final_quality_seconds"] is None for row in comparison_rows) else sum(float(row["council_time_to_baseline_final_quality_seconds"]) for row in comparison_rows) / len(comparison_rows),
            "quality_matched_time_advantage": advantage, "next_route": "run-source-hidden-blind-review"}
    return _finish(body, "quality_time")


def _review_neutral_artifact(value: object, field: str, role: str, prefix: str) -> Dict[str, object]:
    result = _artifact(value, field, role=role, prefix=prefix)
    suffix = result["path"][len(prefix.rstrip("/")) + 1:]
    if re.search(r"(?:^|[-_/])(?:baseline|council|single-agent|research-director|arm|source)(?:[-_/]|$)", suffix, re.I):
        raise ValueError("blind review artifact path reveals an arm or source label")
    return result


def compile_blind_review(*, baseline_traces: Sequence[Mapping[str, object]], council_traces: Sequence[Mapping[str, object]], reviewer_observations: Mapping[str, object]) -> Dict[str, object]:
    pairs = _paired_traces(baseline_traces, council_traces)
    if not isinstance(reviewer_observations, Mapping) or set(reviewer_observations) != {"reviews"}:
        raise ValueError("reviewer_observations must contain exactly reviews")
    raw_reviews = reviewer_observations["reviews"]
    _reject_secrets(raw_reviews)
    if not isinstance(raw_reviews, (list, tuple)) or len(raw_reviews) != 3:
        raise ValueError("exactly three reviewers are required")
    pair_by_key = {(pair[0]["freeze_id"], pair[0]["trial_id"]): pair for pair in pairs}
    normalized_reviews, reviewer_ids = [], set()
    for index, raw in enumerate(raw_reviews):
        fields = {"reviewer_id", "independent", "materials", "preferences", "read_review_ids", "cited_review_ids"}
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise ValueError("review observation has an invalid schema")
        reviewer = _neutral_id(raw["reviewer_id"], f"reviews[{index}].reviewer_id")
        if reviewer in reviewer_ids:
            raise ValueError("duplicate reviewers are not allowed")
        reviewer_ids.add(reviewer)
        if raw["independent"] is not True or raw["read_review_ids"] != [] or raw["cited_review_ids"] != []:
            raise ValueError("reviewers must be independent and may not read/cite another review")
        if not isinstance(raw["materials"], (list, tuple)) or not isinstance(raw["preferences"], (list, tuple)):
            raise ValueError("review materials and preferences must be lists")
        materials, preferences = [], []
        material_keys, preference_keys = set(), set()
        for material in raw["materials"]:
            material_fields = {"freeze_id", "trial_id", "candidate_trace_ids", "source_hidden", "transcript_reviewed", "trajectory_reviewed", "package_ref", "transcript_ref", "trajectory_ref", "reviewer_run_ref"}
            if not isinstance(material, Mapping) or set(material) != material_fields:
                raise ValueError("blind review material has an invalid schema")
            key = (_text(material["freeze_id"], "material.freeze_id", casefold=True), _neutral_id(material["trial_id"], "material.trial_id"))
            if key not in pair_by_key or key in material_keys:
                raise ValueError("review material must cover every pair exactly once")
            material_keys.add(key); pair = pair_by_key[key]
            trace_ids = sorted(_text(item, "candidate_trace_id", casefold=True) for item in material["candidate_trace_ids"])
            if trace_ids != sorted([pair[0]["trace_id"], pair[1]["trace_id"]]) or material["source_hidden"] is not True or material["transcript_reviewed"] is not True or material["trajectory_reviewed"] is not True:
                raise ValueError("material must be source-hidden and bind transcript/trajectory for both traces")
            prefix = f"research-council-benchmark/{key[0]}/evaluation/blind-review/{reviewer}/{key[1]}"
            materials.append({"freeze_id": key[0], "trial_id": key[1], "candidate_trace_ids": trace_ids,
                              "source_hidden": True, "transcript_reviewed": True, "trajectory_reviewed": True,
                              "package_ref": _review_neutral_artifact(material["package_ref"], "package_ref", "source_hidden_review_package", prefix),
                              "transcript_ref": _review_neutral_artifact(material["transcript_ref"], "transcript_ref", "source_hidden_transcript", prefix),
                              "trajectory_ref": _review_neutral_artifact(material["trajectory_ref"], "trajectory_ref", "source_hidden_trajectory", prefix),
                              "reviewer_run_ref": _review_neutral_artifact(material["reviewer_run_ref"], "reviewer_run_ref", "reviewer_run", prefix)})
        for preference in raw["preferences"]:
            if not isinstance(preference, Mapping) or set(preference) != {"freeze_id", "trial_id", "preferred_trace_id"}:
                raise ValueError("preference has an invalid schema")
            key = (_text(preference["freeze_id"], "preference.freeze_id", casefold=True), _neutral_id(preference["trial_id"], "preference.trial_id"))
            if key not in pair_by_key or key in preference_keys:
                raise ValueError("preferences must cover every pair exactly once")
            preference_keys.add(key); preferred = _text(preference["preferred_trace_id"], "preferred_trace_id", casefold=True)
            if preferred not in {pair_by_key[key][0]["trace_id"], pair_by_key[key][1]["trace_id"]}:
                raise ValueError("preference is not bound to the pair")
            preferences.append({"freeze_id": key[0], "trial_id": key[1], "preferred_trace_id": preferred})
        if material_keys != set(pair_by_key) or preference_keys != set(pair_by_key):
            raise ValueError("each reviewer must review every paired trial")
        materials.sort(key=lambda row: (row["freeze_id"], row["trial_id"])); preferences.sort(key=lambda row: (row["freeze_id"], row["trial_id"]))
        normalized_reviews.append({"reviewer_id": reviewer, "independent": True, "materials": materials, "preferences": preferences, "read_review_ids": [], "cited_review_ids": []})
    normalized_reviews.sort(key=lambda row: row["reviewer_id"])
    aggregates = []
    for key, pair in sorted(pair_by_key.items()):
        council_id = pair[1]["trace_id"]
        count = sum(next(pref for pref in review["preferences"] if (pref["freeze_id"], pref["trial_id"]) == key)["preferred_trace_id"] == council_id for review in normalized_reviews)
        aggregates.append({"freeze_id": key[0], "trial_id": key[1], "council_preference_count": count, "council_won": count >= 2})
    minimum_count = min(row["council_preference_count"] for row in aggregates)
    body = {"contract": "lca_research_kernel.research_council_blind_review/v2",
            "paired_trace_ids": sorted(trace["trace_id"] for pair in pairs for trace in pair),
            "reviewer_observations": {"reviews": normalized_reviews}, "per_trial_preferences": aggregates,
            "reviewer_count": 3, "council_preference_count": minimum_count,
            "council_won": all(row["council_won"] for row in aggregates),
            "next_route": "compile-mechanism-ablation"}
    return _finish(body, "blind_review")


def _validate_quality(value: object, baseline: Sequence[Mapping[str, object]], council: Sequence[Mapping[str, object]]) -> Dict[str, object]:
    if not isinstance(value, Mapping) or "quality_checkpoints" not in value:
        raise ValueError("reconstructable quality-time evidence is required")
    expected = compile_quality_time_comparison(baseline_traces=baseline, council_traces=council, quality_checkpoints=value["quality_checkpoints"])
    if dict(value) != expected:
        raise ValueError("quality-time aggregate differs from semantic recompilation")
    return expected


def _validate_review(value: object, baseline: Sequence[Mapping[str, object]], council: Sequence[Mapping[str, object]]) -> Dict[str, object]:
    if not isinstance(value, Mapping) or "reviewer_observations" not in value:
        raise ValueError("reconstructable blind-review evidence is required")
    expected = compile_blind_review(baseline_traces=baseline, council_traces=council, reviewer_observations=value["reviewer_observations"])
    if dict(value) != expected:
        raise ValueError("blind-review aggregate differs from semantic recompilation")
    return expected


def _ablation_observation(value: object, field: str, prefix: str) -> Tuple[Dict[str, object], datetime]:
    if not isinstance(value, Mapping) or set(value) != {"observed_by", "observed_at_utc", "harness_receipt_ref"} or value["observed_by"] != "runtime_harness":
        raise ValueError(f"{field} must be an exact ablation-harness observation")
    stamp, parsed = _utc(value["observed_at_utc"], f"{field}.observed_at_utc")
    return {"observed_by": "runtime_harness", "observed_at_utc": stamp,
            "harness_receipt_ref": _artifact(value["harness_receipt_ref"], f"{field}.harness_receipt_ref", role="ablation_harness_receipt", prefix=prefix)}, parsed


def _ablation(value: object, frozen: Mapping[str, object], pairs: Sequence[Tuple[Mapping[str, object], Mapping[str, object]]]) -> Dict[str, object]:
    _reject_secrets(value)
    if not isinstance(value, Mapping):
        raise ValueError("ablation evidence must be an object")
    if value.get("status") == "not_applicable_council_not_qualified":
        if set(value) != {"status", "reason_ref"}:
            raise ValueError("not-applicable ablation must have exactly one reason artifact")
        prefix = f"research-council-benchmark/{frozen['freeze_id']}/evaluation/ablation"
        return {"status": "not_applicable_council_not_qualified",
                "reason_ref": _artifact(value["reason_ref"], "reason_ref", role="ablation_not_applicable_reason", prefix=prefix),
                "observations": [], "load_bearing_mechanisms": []}
    if set(value) != {"status", "observations"} or value.get("status") != "completed":
        raise ValueError("ablation status is invalid")
    if len(pairs) < 2:
        raise ValueError("promotion ablation requires at least two paired trials")
    council_by_trial = {pair[1]["trial_id"]: pair[1] for pair in pairs}
    observations = value["observations"]
    if not isinstance(observations, (list, tuple)):
        raise ValueError("ablation observations must be a list")
    normalized, keys = [], set()
    fields = {"freeze_id", "trial_id", "council_trace_id", "removed_mechanism", "removal_degraded",
              "ablated_run_ref", "outcome_ref", "evidence_ref", "started_observation",
              "outcome_observation", "terminal_observation"}
    for index, raw in enumerate(observations):
        if not isinstance(raw, Mapping) or set(raw) != fields:
            raise ValueError("ablation observation has an invalid schema")
        trial = _neutral_id(raw["trial_id"], "ablation.trial_id")
        mechanism = _text(raw["removed_mechanism"], "removed_mechanism", casefold=True)
        key = (trial, mechanism)
        if raw["freeze_id"] != frozen["freeze_id"] or trial not in council_by_trial or mechanism not in _MECHANISMS or key in keys:
            raise ValueError("ablation is not bound to a unique canonical mechanism/trial")
        keys.add(key); trace = council_by_trial[trial]
        if raw["council_trace_id"] != trace["trace_id"] or not isinstance(raw["removal_degraded"], bool):
            raise ValueError("ablation must bind its Council trace and harness result")
        prefix = f"research-council-benchmark/{frozen['freeze_id']}/evaluation/ablation/{trial}/{mechanism}"
        run_ref = _artifact(raw["ablated_run_ref"], f"ablation[{index}].ablated_run_ref", role="ablated_run", prefix=prefix)
        outcome_ref = _artifact(raw["outcome_ref"], f"ablation[{index}].outcome_ref", role="ablation_outcome", prefix=prefix)
        evidence_ref = _artifact(raw["evidence_ref"], f"ablation[{index}].evidence_ref", role="ablation_evidence", prefix=prefix)
        started, started_dt = _ablation_observation(raw["started_observation"], f"ablation[{index}].started_observation", prefix)
        terminal, terminal_dt = _ablation_observation(raw["terminal_observation"], f"ablation[{index}].terminal_observation", prefix)
        outcome_raw = raw["outcome_observation"]
        outcome_fields = {"observed_by", "observed_at_utc", "harness_receipt_ref", "ablated_run_ref", "outcome_ref", "removal_degraded"}
        if not isinstance(outcome_raw, Mapping) or set(outcome_raw) != outcome_fields or not isinstance(outcome_raw["removal_degraded"], bool):
            raise ValueError("outcome_observation must bind the exact ablated run result")
        outcome_base, outcome_dt = _ablation_observation(
            {"observed_by": outcome_raw["observed_by"], "observed_at_utc": outcome_raw["observed_at_utc"], "harness_receipt_ref": outcome_raw["harness_receipt_ref"]},
            f"ablation[{index}].outcome_observation", prefix)
        observed_run = _artifact(outcome_raw["ablated_run_ref"], f"ablation[{index}].outcome_observation.ablated_run_ref", role="ablated_run", prefix=prefix)
        observed_outcome = _artifact(outcome_raw["outcome_ref"], f"ablation[{index}].outcome_observation.outcome_ref", role="ablation_outcome", prefix=prefix)
        if not (started_dt <= outcome_dt <= terminal_dt) or terminal_dt <= started_dt:
            raise ValueError("ablation outcome must lie inside a forward run window")
        if observed_run != run_ref or observed_outcome != outcome_ref or outcome_raw["removal_degraded"] != raw["removal_degraded"]:
            raise ValueError("ablation outcome does not bind its run, artifact, and degradation result")
        outcome = {**outcome_base, "ablated_run_ref": observed_run, "outcome_ref": observed_outcome,
                   "removal_degraded": raw["removal_degraded"]}
        normalized.append({"freeze_id": frozen["freeze_id"], "trial_id": trial, "council_trace_id": trace["trace_id"], "removed_mechanism": mechanism,
                           "removal_degraded": raw["removal_degraded"], "ablated_run_ref": run_ref,
                           "outcome_ref": outcome_ref, "evidence_ref": evidence_ref,
                           "started_observation": started, "outcome_observation": outcome,
                           "terminal_observation": terminal})
    expected_keys = {(trial, mechanism) for trial in council_by_trial for mechanism in _MECHANISMS}
    if keys != expected_keys:
        raise ValueError("completed ablation must cover the full mechanism/trial grid")
    normalized.sort(key=lambda row: (row["removed_mechanism"], row["trial_id"]))
    load_bearing = [mechanism for mechanism in _MECHANISMS if all(row["removal_degraded"] for row in normalized if row["removed_mechanism"] == mechanism)]
    return {"status": "completed", "observations": normalized, "load_bearing_mechanisms": sorted(load_bearing)}


def compile_architecture_decision(*, freeze: Mapping[str, object], baseline_traces: Sequence[Mapping[str, object]], council_traces: Sequence[Mapping[str, object]], blind_review: Mapping[str, object], quality_time: Mapping[str, object], ablation: Mapping[str, object]) -> Dict[str, object]:
    frozen = _validate_freeze(freeze)
    pairs = _paired_traces(baseline_traces, council_traces)
    if any(pair[0]["freeze"] != frozen or pair[1]["freeze"] != frozen for pair in pairs):
        raise ValueError("decision freeze differs from trace freezes")
    baseline = [pair[0] for pair in pairs]; council = [pair[1] for pair in pairs]
    review = _validate_review(blind_review, baseline, council)
    quality = _validate_quality(quality_time, baseline, council)
    ablation_result = _ablation(ablation, frozen, pairs)
    hard_gates = {gate: "passed" if all(trace["hard_gates"][gate] == "passed" for pair in pairs for trace in pair) else "failed" for gate in _GATES}
    hard_gates["zero_prohibited_effects"] = "passed" if all(trace["prohibited_effects"] and set(trace["prohibited_effects"]) == set(_EFFECTS) and all(value == 0 for value in trace["prohibited_effects"].values()) for pair in pairs for trace in pair) else "failed"
    hard_gates["repeatable_paired_trials"] = "passed" if len(pairs) >= 2 else "failed"
    hard_gates["mechanism_level_ablation"] = "passed" if ablation_result["status"] == "completed" and bool(ablation_result["load_bearing_mechanisms"]) else "failed"

    # Literal approved promotion branch, after semantic verification of every input.
    hard_gates_passed = all(value == "passed" for value in hard_gates.values())
    council_wins = review["council_preference_count"] >= 2
    time_advantage = quality["quality_matched_time_advantage"] is True
    status = (
        "promote_minimum_effective_council"
        if hard_gates_passed and council_wins and time_advantage
        else "retain_single_agent_baseline"
    )
    ablation = ablation_result
    promoted = sorted(ablation["load_bearing_mechanisms"]) if status.startswith("promote") else []
    if status.startswith("promote"):
        next_route = "integrate-promoted-council-mechanisms"
    elif ablation_result["status"] == "not_applicable_council_not_qualified":
        next_route = f"retain-baseline-from-{ablation_result['reason_ref']['sha256'][:12]}"
    else:
        evidence_digest = hashlib.sha256(_canonical(ablation_result["observations"])).hexdigest()[:12]
        next_route = f"retain-baseline-and-retest-from-{evidence_digest}"
    body = {"contract": "lca_research_kernel.research_council_architecture_decision/v2", "freeze_id": frozen["freeze_id"],
            "baseline_trace_ids": sorted(trace["trace_id"] for trace in baseline), "council_trace_ids": sorted(trace["trace_id"] for trace in council),
            "blind_review_id": review["blind_review_id"], "quality_time_id": quality["quality_time_id"],
            "hard_gates": hard_gates, "council_preference_count": review["council_preference_count"],
            "per_trial_preferences": review["per_trial_preferences"], "quality_matched_time_advantage": time_advantage,
            "ablation": ablation_result, "load_bearing_mechanisms": ablation_result["load_bearing_mechanisms"],
            "status": status, "promoted_mechanisms": promoted, "next_route": next_route}
    return _finish(body, "decision")
