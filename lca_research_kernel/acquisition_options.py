"""Compile human-responsibility evidence-acquisition options without executing them."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


JsonObject = Dict[str, object]

ACQUISITION_OPTIONS_CONTRACT = (
    "lca_research_kernel.responsibility_aware_acquisition_options/v1"
)
EVIDENCE_ACQUISITION_CONTRACT = (
    "lca_research_kernel.evidence_acquisition_manifest/v1"
)
AUTHOR_HOSTED_CONTRACT = (
    "lca_research_kernel.author_hosted_public_file_routes/v1"
)
RESUME_INPUT_CONTRACT = (
    "lca_research_kernel.acquisition_option_resume_input/v1"
)

FORBIDDEN_SECRET_KEYS = {
    "api_key",
    "apikey",
    "token",
    "access_token",
    "password",
    "cookie",
    "session_cookie",
    "secret",
    "secret_value",
    "credential_value",
}

FORMULA_CANDIDATES = {
    "Uncoated LiFePO4 route core, screening proxy": {
        "formula": "LiFePO4",
        "basis_status": "screening_product_formula_explicit_in_flow_identity",
    },
    "Lithium carbonate": {
        "formula": "Li2CO3",
        "basis_status": "formula_candidate_requires_flow_purity_confirmation",
    },
    "Magnetite": {
        "formula": "Fe3O4",
        "basis_status": "formula_candidate_requires_flow_purity_confirmation",
    },
    "Diammonium hydrogen phosphate": {
        "formula": "(NH4)2HPO4",
        "basis_status": "formula_candidate_requires_flow_purity_confirmation",
    },
    "Lithium hydroxide": {
        "formula": "LiOH",
        "basis_status": "anhydrous_formula_candidate_hydration_unknown",
    },
    "Phosphoric acid": {
        "formula": "H3PO4",
        "basis_status": "pure_formula_candidate_solution_concentration_unknown",
    },
    "Iron sulfate": {
        "formula": "FeSO4",
        "basis_status": "anhydrous_ferrous_candidate_hydration_and_oxidation_state_need_confirmation",
    },
    "Lithium sulfate": {
        "formula": "Li2SO4",
        "basis_status": "anhydrous_formula_candidate_hydration_unknown",
    },
    "Process water proxy": {
        "formula": "H2O",
        "basis_status": "water_proxy_formula_with_density_and_composition_assumptions",
    },
}

REQUIRED_SCENARIO_FLOWS = {
    "solid-state": {
        "Uncoated LiFePO4 route core, screening proxy",
        "Lithium carbonate",
        "Magnetite",
        "Diammonium hydrogen phosphate",
        "Electricity, process",
        "Uncharacterized process mass residual, solid-state route proxy",
    },
    "hydrothermal": {
        "Uncoated LiFePO4 route core, screening proxy",
        "Lithium hydroxide",
        "Phosphoric acid",
        "Iron sulfate",
        "Lithium sulfate",
        "Natural gas heat, process proxy",
        "Process water proxy",
        "Uncharacterized process mass residual, hydrothermal route proxy",
    },
}


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _artifact(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an artifact binding")
    path = _required_text(value.get("path"), f"{label}.path")
    sha256 = _required_text(value.get("sha256"), f"{label}.sha256")
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise ValueError(f"{label}.sha256 must be a lowercase sha256")
    return {"path": path, "sha256": sha256}


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _targets_and_fields(evidence: JsonObject) -> Tuple[List[JsonObject], List[str]]:
    targets = evidence.get("source_targets")
    if not isinstance(targets, list) or len(targets) != 2:
        raise ValueError("exactly two evidence source targets are required")
    compiled_targets = []
    all_fields = []
    seen_targets = set()
    seen_fields = set()
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("evidence source target is malformed")
        target_id = _required_text(target.get("target_id"), "target_id")
        if target_id in seen_targets:
            raise ValueError(f"duplicate target: {target_id}")
        seen_targets.add(target_id)
        identifiers = target.get("identifiers")
        fields = target.get("required_fields")
        if not isinstance(identifiers, dict) or not isinstance(fields, list):
            raise ValueError("target identifiers or field contracts are malformed")
        field_ids = []
        for field in fields:
            if (
                not isinstance(field, dict)
                or field.get("status") != "unresolved"
                or field.get("value_invented") is not False
            ):
                raise ValueError("all acquisition fields must remain unresolved and uninvented")
            field_id = _required_text(field.get("field_id"), "field_id")
            if field_id in seen_fields:
                raise ValueError(f"duplicate field contract: {field_id}")
            accepted = field.get("accepted_evidence")
            if not isinstance(accepted, list) or not accepted:
                raise ValueError("field acceptance evidence is missing")
            seen_fields.add(field_id)
            field_ids.append(field_id)
            all_fields.append(field_id)
        compiled_targets.append(
            {
                "target_id": target_id,
                "doi": _required_text(identifiers.get("doi"), f"{target_id}.doi"),
                "title": _required_text(target.get("title"), f"{target_id}.title"),
                "unresolved_field_ids": sorted(field_ids),
            }
        )
    if len(all_fields) != 15:
        raise ValueError("current vertical requires exactly 15 unresolved fields")
    return sorted(compiled_targets, key=lambda row: row["target_id"]), sorted(all_fields)


def _description(exchange: JsonObject) -> str:
    ref = exchange.get("referenceToFlowDataSet")
    if not isinstance(ref, dict):
        raise ValueError("scenario exchange is missing a Flow reference")
    value = ref.get("common:shortDescription")
    if isinstance(value, dict):
        return _required_text(value.get("#text"), "Flow short description")
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return _required_text(value[0].get("#text"), "Flow short description")
    raise ValueError("scenario exchange is missing a Flow short description")


def _scenario_readiness(rows: List[JsonObject]) -> JsonObject:
    if not isinstance(rows, list) or len(rows) != 2:
        raise ValueError("exactly two current residual scenario rows are required")
    observations = []
    routes = set()
    observed_formula_names = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("residual scenario row is malformed")
        process = row.get("processDataSet")
        if not isinstance(process, dict):
            raise ValueError("residual scenario process is malformed")
        try:
            name = process["processInformation"]["dataSetInformation"]["name"]["baseName"][0]["#text"]
            exchanges = process["exchanges"]["exchange"]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("residual scenario structure is malformed") from error
        route_id = next(
            (candidate for candidate in REQUIRED_SCENARIO_FLOWS if candidate in name),
            None,
        )
        if route_id is None or route_id in routes:
            raise ValueError("residual scenario route identity is missing or duplicated")
        routes.add(route_id)
        if not isinstance(exchanges, list):
            raise ValueError("scenario exchanges are malformed")
        flow_rows = []
        flow_names = set()
        for exchange in exchanges:
            if not isinstance(exchange, dict):
                raise ValueError("scenario exchange is malformed")
            description = _description(exchange)
            if description in flow_names:
                raise ValueError(f"duplicate scenario Flow: {description}")
            flow_names.add(description)
            try:
                amount = Decimal(str(exchange.get("meanAmount")))
                resulting = Decimal(str(exchange.get("resultingAmount")))
            except (InvalidOperation, TypeError, ValueError) as error:
                raise ValueError("scenario amount is not a finite decimal") from error
            if (
                not amount.is_finite()
                or not resulting.is_finite()
                or amount <= 0
                or amount != resulting
            ):
                raise ValueError("scenario amount is not a positive stable quantity")
            direction = _required_text(exchange.get("exchangeDirection"), "direction")
            ref = exchange["referenceToFlowDataSet"]
            flow_rows.append(
                {
                    "flow_id": _required_text(ref.get("@refObjectId"), "flow_id"),
                    "flow_name": description,
                    "direction": direction,
                    "amount": str(amount),
                }
            )
            if description in FORMULA_CANDIDATES:
                observed_formula_names.add(description)
        if flow_names != REQUIRED_SCENARIO_FLOWS[route_id]:
            missing = sorted(REQUIRED_SCENARIO_FLOWS[route_id] - flow_names)
            extra = sorted(flow_names - REQUIRED_SCENARIO_FLOWS[route_id])
            raise ValueError(
                f"{route_id} scenario Flow set changed; missing={missing}, extra={extra}"
            )
        observations.append(
            {
                "route_id": route_id,
                "process_name": name,
                "exchange_count": len(flow_rows),
                "flow_observations": sorted(flow_rows, key=lambda value: value["flow_name"]),
            }
        )
    if observed_formula_names != set(FORMULA_CANDIDATES):
        raise ValueError("scenario formula candidate coverage changed")
    basis_risks = {
        name: row["basis_status"]
        for name, row in FORMULA_CANDIDATES.items()
        if "explicit" not in row["basis_status"]
    }
    return {
        "selected_route": "compile-elemental-stoichiometric-consistency-bracket",
        "status": "ready",
        "selection_reason": (
            "Current content-addressed scenarios contain exact positive reagent, product, "
            "coproduct, water, energy, and residual amounts; formula-basis risks can be "
            "bracketed without target-paper full text."
        ),
        "scenario_route_count": len(observations),
        "scenario_exchange_count": sum(row["exchange_count"] for row in observations),
        "scenario_routes": sorted(observations, key=lambda row: row["route_id"]),
        "formula_candidates": [
            {"flow_name": name, **FORMULA_CANDIDATES[name]}
            for name in sorted(FORMULA_CANDIDATES)
        ],
        "basis_risks": basis_risks,
        "residual_species_reclassification_allowed": False,
        "model_mutation_allowed": False,
        "comparative_lcia_allowed": False,
    }


def _resume_schema(kind: str) -> JsonObject:
    common = {
        "package_report_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "option_id": {"type": "string"},
        "decision": {
            "type": "string",
            "enum": ["provided", "deferred", "declined"],
        },
    }
    required = ["package_report_sha256", "option_id", "decision"]
    if kind == "credential_reference":
        common["secret_reference_id"] = {
            "type": "string",
            "description": "Opaque vault/keychain reference only; never the secret value.",
        }
        required.append("secret_reference_id")
    elif kind == "attachment_binding":
        common["attachment_bindings"] = {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "target_id",
                    "path",
                    "sha256",
                    "bytes",
                    "access_basis",
                    "version_status",
                ],
            },
        }
        required.append("attachment_bindings")
    elif kind == "contact_result":
        common["authorization_request_id"] = {"type": "string"}
        common["response_artifact_bindings"] = {"type": "array"}
        required.extend(["authorization_request_id", "response_artifact_bindings"])
    else:
        raise ValueError(f"unsupported resume input kind: {kind}")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": common,
        "required_when_decision_is_provided": required,
        "forbidden_secret_properties": sorted(FORBIDDEN_SECRET_KEYS),
    }


def _option(
    *,
    option_id: str,
    label: str,
    effect_type: str,
    priority: int,
    expected_yield: str,
    target_ids: List[str],
    field_ids: List[str],
    full_text_guaranteed: bool,
    prerequisites: List[str],
    risks: List[str],
    resume_kind: str,
    provided_route: str,
    fallback_route: str,
) -> JsonObject:
    schema = _resume_schema(resume_kind)
    template = {
        "contract": RESUME_INPUT_CONTRACT,
        "package_report_sha256": None,
        "option_id": option_id,
        "decision": None,
    }
    for key in schema["required_when_decision_is_provided"]:
        template.setdefault(key, None)
    return {
        "option_id": option_id,
        "label": label,
        "effect_type": effect_type,
        "priority": priority,
        "current_state": "available_human_action_not_started",
        "human_required": True,
        "automatic_execution_allowed": False,
        "external_action_executed": False,
        "target_ids": target_ids,
        "expected_evidence_yield": expected_yield,
        "directly_addressable_field_ids": field_ids,
        "full_text_guaranteed": full_text_guaranteed,
        "prerequisites": prerequisites,
        "risks": risks,
        "secret_handling": "opaque_reference_only_never_secret_value_in_project_state",
        "resume_input_kind": resume_kind,
        "resume_input_schema": schema,
        "resume_input_template": template,
        "provided_input_route": provided_route,
        "deferred_or_declined_fallback_route": fallback_route,
        "field_acceptance_automatic": False,
    }


def compile_acquisition_options(
    evidence_manifest: JsonObject,
    evidence_binding: JsonObject,
    author_hosted_report: JsonObject,
    author_hosted_binding: JsonObject,
    residual_rows: List[JsonObject],
    residual_binding: JsonObject,
) -> JsonObject:
    """Compile human-responsibility options and one automatic internal route."""

    if (
        evidence_manifest.get("contract") != EVIDENCE_ACQUISITION_CONTRACT
        or evidence_manifest.get("status")
        != "lawful_evidence_acquisition_manifest_compiled"
        or evidence_manifest.get("routing", {}).get("pause_internal_work") is not False
        or evidence_manifest.get("scientific_comparison_ready") is not False
        or evidence_manifest.get("comparative_lcia_allowed") is not False
        or evidence_manifest.get("values_invented") is not False
    ):
        raise ValueError("accepted evidence-acquisition manifest is required")
    if (
        author_hosted_report.get("contract") != AUTHOR_HOSTED_CONTRACT
        or author_hosted_report.get("routing", {}).get("next_internal_route")
        != "compile-responsibility-aware-acquisition-options"
        or author_hosted_report.get("lawful_attachment_candidate_count") != 0
        or author_hosted_report.get("scientific_absence_proven") is not False
        or author_hosted_report.get("scientific_field_evidence_admitted") is not False
        or author_hosted_report.get("scientific_comparison_ready") is not False
        or author_hosted_report.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted author-hosted route is required")
    if (
        evidence_manifest.get("scope_id") != author_hosted_report.get("scope_id")
        or evidence_manifest.get("revision") != author_hosted_report.get("revision")
    ):
        raise ValueError("acquisition option upstream scope identity conflicts")
    targets, field_ids = _targets_and_fields(evidence_manifest)
    target_ids = [row["target_id"] for row in targets]
    selected = _scenario_readiness(residual_rows)
    fallback = selected["selected_route"]
    options = [
        _option(
            option_id="scienceon-api-credential",
            label="ScienceON paper API credential",
            effect_type="credential_issuance_and_secret_binding",
            priority=3,
            expected_yield="discovery_only",
            target_ids=target_ids,
            field_ids=[],
            full_text_guaranteed=False,
            prerequisites=[
                "human applies for and receives an issued ScienceON key",
                "secret is stored outside project artifacts",
            ],
            risks=[
                "API metadata/provider links may still expose no lawful target attachment",
                "credential value must never enter event or artifact state",
            ],
            resume_kind="credential_reference",
            provided_route="implement-and-run-scienceon-credentialed-source-adapter",
            fallback_route=fallback,
        ),
        _option(
            option_id="riss-api-credential",
            label="RISS overseas article API credential",
            effect_type="credential_issuance_and_secret_binding",
            priority=4,
            expected_yield="discovery_only",
            target_ids=target_ids,
            field_ids=[],
            full_text_guaranteed=False,
            prerequisites=[
                "human application is approved and a RISS key is issued",
                "secret is stored outside project artifacts",
            ],
            risks=[
                "index or licensed-location metadata is not a target attachment",
                "credential value must never enter event or artifact state",
            ],
            resume_kind="credential_reference",
            provided_route="implement-and-run-riss-credentialed-source-adapter",
            fallback_route=fallback,
        ),
        _option(
            option_id="institutional-library-access",
            label="Lawful institutional library access",
            effect_type="credentialed_or_session_bound_access",
            priority=1,
            expected_yield="full_text_candidate",
            target_ids=target_ids,
            field_ids=field_ids,
            full_text_guaranteed=False,
            prerequisites=[
                "human has lawful institutional access",
                "download terms permit local research use",
                "file version and access basis are recorded",
            ],
            risks=[
                "session automation is prohibited without explicit human action",
                "a downloaded file still requires byte/hash/media/version/identity validation",
            ],
            resume_kind="attachment_binding",
            provided_route="register-and-validate-human-provided-attachment",
            fallback_route=fallback,
        ),
        _option(
            option_id="publisher-purchase-or-license",
            label="Publisher purchase or license acceptance",
            effect_type="payment_or_license_acceptance",
            priority=5,
            expected_yield="full_text_candidate",
            target_ids=target_ids,
            field_ids=field_ids,
            full_text_guaranteed=False,
            prerequisites=[
                "human approves cost and license terms",
                "lawful downloaded bytes and access basis are supplied",
            ],
            risks=[
                "financial and contractual responsibility belongs to the human",
                "license may restrict storage, reuse, or redistribution",
            ],
            resume_kind="attachment_binding",
            provided_route="register-and-validate-human-provided-attachment",
            fallback_route=fallback,
        ),
        _option(
            option_id="corresponding-author-or-data-provider-contact",
            label="Human-approved author or data-provider contact",
            effect_type="external_write",
            priority=2,
            expected_yield="primary_protocol_or_inventory_candidate",
            target_ids=target_ids,
            field_ids=field_ids,
            full_text_guaranteed=False,
            prerequisites=[
                "human approves recipient, message, identity, and outbound send",
                "response attribution and permission status are recorded",
            ],
            risks=[
                "outbound communication and privacy responsibility belongs to the human",
                "response is optional and may not contain decision-grade evidence",
            ],
            resume_kind="contact_result",
            provided_route="register-and-validate-human-provided-contact-result",
            fallback_route=fallback,
        ),
    ]
    options.sort(key=lambda row: row["option_id"])
    return {
        "schema_version": 1,
        "contract": ACQUISITION_OPTIONS_CONTRACT,
        "scope_id": _required_text(evidence_manifest.get("scope_id"), "scope_id"),
        "revision": _required_text(evidence_manifest.get("revision"), "revision"),
        "status": "responsibility_aware_acquisition_options_compiled",
        "evidence_acquisition_manifest": _artifact(
            evidence_binding, "evidence_binding"
        ),
        "author_hosted_report": _artifact(
            author_hosted_binding, "author_hosted_binding"
        ),
        "residual_scenarios": _artifact(residual_binding, "residual_binding"),
        "target_count": len(targets),
        "unresolved_field_count": len(field_ids),
        "targets": targets,
        "option_count": len(options),
        "credential_option_count": sum(
            row["resume_input_kind"] == "credential_reference" for row in options
        ),
        "full_text_option_count": sum(
            row["resume_input_kind"] == "attachment_binding" for row in options
        ),
        "contact_option_count": sum(
            row["resume_input_kind"] == "contact_result" for row in options
        ),
        "external_action_executed_count": 0,
        "options": options,
        "internal_route_selection": selected,
        "selected_internal_route": selected["selected_route"],
        "routing": {
            "human_option_ids": [row["option_id"] for row in options],
            "human_options_wait_independently": True,
            "next_internal_route": selected["selected_route"],
            "pause_internal_work": False,
        },
        "scientific_field_evidence_admitted": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "next_action": (
            "Compile a hash-bound elemental and stoichiometric consistency bracket "
            "for both current scenarios without reclassifying residual species or opening LCIA."
        ),
    }


def _contains_forbidden_secret(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).casefold() in FORBIDDEN_SECRET_KEYS
            or _contains_forbidden_secret(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_secret(child) for child in value)
    return False


def validate_acquisition_option_input(
    option: JsonObject, payload: JsonObject
) -> JsonObject:
    """Validate a future non-secret resume payload without executing an option."""

    if not isinstance(payload, dict):
        raise ValueError("acquisition option input must be an object")
    if _contains_forbidden_secret(payload):
        raise ValueError("secret value fields must never enter project state")
    decision = payload.get("decision")
    if decision not in {"provided", "deferred", "declined"}:
        raise ValueError("acquisition option decision is invalid")
    if decision == "provided":
        kind = option.get("resume_input_kind")
        if kind == "credential_reference":
            _required_text(payload.get("secret_reference_id"), "secret_reference_id")
        elif kind == "attachment_binding":
            bindings = payload.get("attachment_bindings")
            if not isinstance(bindings, list) or not bindings:
                raise ValueError("provided attachment input requires bindings")
        elif kind == "contact_result":
            _required_text(
                payload.get("authorization_request_id"), "authorization_request_id"
            )
            bindings = payload.get("response_artifact_bindings")
            if not isinstance(bindings, list):
                raise ValueError("contact result bindings are required")
        else:
            raise ValueError("acquisition option resume input kind is invalid")
    return dict(payload)


def expected_acquisition_option_output_paths(package: JsonObject) -> List[str]:
    options = package.get("options")
    if not isinstance(options, list):
        raise ValueError("acquisition options are malformed")
    return [
        f"resume-input-templates/{_required_text(row.get('option_id'), 'option_id')}.json"
        for row in options
        if isinstance(row, dict)
    ]


def write_acquisition_option_package(
    package: JsonObject, template_root: Path, output: Path
) -> None:
    """Write deterministic resume templates and the compiled package."""

    if package.get("contract") != ACQUISITION_OPTIONS_CONTRACT:
        raise ValueError("responsibility-aware acquisition package is required")
    template_root.mkdir(parents=True, exist_ok=True)
    for option in package.get("options", []):
        option_id = _required_text(option.get("option_id"), "option_id")
        template = option.get("resume_input_template")
        if not isinstance(template, dict):
            raise ValueError("resume input template is missing")
        (template_root / f"{option_id}.json").write_bytes(_json_bytes(template))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_json_bytes(package))


def _read_json(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> List[JsonObject]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        rows.append(value)
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-manifest", type=Path, required=True)
    parser.add_argument("--evidence-binding", type=Path, required=True)
    parser.add_argument("--author-hosted-report", type=Path, required=True)
    parser.add_argument("--author-hosted-binding", type=Path, required=True)
    parser.add_argument("--residual-rows", type=Path, required=True)
    parser.add_argument("--residual-binding", type=Path, required=True)
    parser.add_argument("--template-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    package = compile_acquisition_options(
        _read_json(args.evidence_manifest),
        _read_json(args.evidence_binding),
        _read_json(args.author_hosted_report),
        _read_json(args.author_hosted_binding),
        _read_jsonl(args.residual_rows),
        _read_json(args.residual_binding),
    )
    write_acquisition_option_package(package, args.template_root, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
