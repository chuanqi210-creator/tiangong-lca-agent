"""Compile exact saved TianGong Flow plans for one authoring bulk batch."""

from __future__ import annotations

import hashlib
import json
import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_flow_authoring_bulk_batch_saved_plans/v1"
LOGIC_VERSION = "2026-07-18.6"
POLICY_VERSION = "2026-07-18.1"
MASS_PROPERTY_ID = "93a60a56-a3c8-11da-a746-0800200b9a66"
MASS_UNIT_GROUP_ID = "93a60a57-a4c8-11da-a746-0800200c9a66"
AREA_TIME_PROPERTY_ID = "93a60a56-a3c8-21da-a746-0800200c9a66"
ENERGY_PROPERTY_ID = "f6811440-ee37-11de-8a39-0800200c9a66"
SUPPORT_PROPERTY_SPECS = {
    MASS_PROPERTY_ID: {
        "flow_property_name": "Mass",
        "flow_property_version": "03.00.003",
        "reference_unit": "kg",
        "unit_group_id": MASS_UNIT_GROUP_ID,
        "unit_group_version": "03.00.003",
        "assignment_status": "resolved_by_accepted_mass_contract_reuse",
    },
    AREA_TIME_PROPERTY_ID: {
        "flow_property_name": "Area*Time",
        "flow_property_version": "03.00.003",
        "reference_unit": "m2*a",
        "unit_group_id": "93a60a57-a3c8-20da-a746-0800200c9a66",
        "unit_group_version": "03.00.003",
        "assignment_status": "resolved_by_accepted_explicit_contract_reuse",
    },
    ENERGY_PROPERTY_ID: {
        "flow_property_name": "Energy",
        "flow_property_version": "00.00.000",
        "reference_unit": "MJ",
        "unit_group_id": "93a60a57-a3c8-11da-a746-0800200c9a66",
        "unit_group_version": "03.00.003",
        "assignment_status": "resolved_by_accepted_explicit_contract_reuse",
    },
}
PLAN_MEDIA_TYPE = "application/vnd.tiangong.flow-build-plan.v1+json"
SUPPORT_CONTRACT = "lca_research_kernel.uslci_flow_authoring_canary_supports/v1"
AUTHORING_CONTRACT = "lca_research_kernel.uslci_flow_authoring_plan/v1"
BULK_SUPPORT_CONTRACT = (
    "lca_research_kernel.uslci_flow_authoring_bulk_batch_supports/v1"
)
EXPECTED_INPUT_NAMES = (
    "bulk_support_report",
    "selected_batch",
    "source_observations",
    "classification_decisions",
    "support_assignments",
    "readiness",
    "review_markers",
    "authoring_plan_report",
    "authoring_intents",
    "support_report",
    "support_contracts",
    "runtime_report",
    "runtime_manifest",
    "planning_anchor",
)
ZERO_SIDE_EFFECTS = (
    "native_invocation_executed_count",
    "network_read_executed_count",
    "remote_candidate_search_executed_count",
    "flow_creation_executed_count",
    "flow_materialization_executed_count",
    "remote_flow_materialization_executed_count",
    "save_draft_executed_count",
    "process_creation_executed_count",
    "model_connection_executed_count",
    "model_mutation_executed_count",
    "external_write_executed_count",
    "publication_executed_count",
    "comparison_executed_count",
    "lcia_executed_count",
)
SUPPORT_AUTHORITY_ZERO_EFFECTS = (
    "network_read_executed_count",
    "remote_candidate_search_executed_count",
    "saved_flow_plan_compiled_count",
    "flow_creation_executed_count",
    "flow_materialization_executed_count",
    "remote_flow_materialization_executed_count",
    "save_draft_executed_count",
    "process_creation_executed_count",
    "model_connection_executed_count",
    "model_mutation_executed_count",
    "external_write_executed_count",
    "publication_executed_count",
    "comparison_executed_count",
    "lcia_executed_count",
)
BATCH_9_SUPPORT_INPUT_VERSION = "fff3456486c11c38"
BATCH_9_SUPPORT_RESOLUTION_VERSION = "5160dd3a971e0af0"
BATCH_8_NATIVE_EXECUTION_VERSION = "b208fec5b25c212a"
BATCH_8_NATIVE_REPORT_SHA256 = (
    "cffc9886e9b1b0911b03f5cf5633757a8e300301680b0cf6a6f4f2371463b6b7"
)
BATCH_10_SUPPORT_INPUT_VERSION = "b28c5eeb9a718ae6"
BATCH_10_SUPPORT_RESOLUTION_VERSION = "1284b1d20a584a48"
BATCH_10_SUPPORT_REPORT_SHA256 = (
    "dbc989e195106db7c9f1584075a7bbf45a8451b7a9ade3bbc85090b2a11cb551"
)
BATCH_9_NATIVE_EXECUTION_VERSION = "56870a987f96fb17"
BATCH_9_NATIVE_REPORT_SHA256 = (
    "655a58b06c565f423b16919f8db06673db13e4c8fc8a2647e46b996524dddb84"
)
BATCH_10_SOURCE_ENVELOPE_SHA256 = (
    "43984a451d9a2fdd3f605ac4705ae40e46626c3882a23f712355d090765e46c4"
)
BATCH_10_SOURCE_ENVELOPE_BYTES = 2171478
BATCH_10_SOURCE_ENVELOPE_SUFFIX = (
    "raw-public-responses/016-11c82858-46f7-38cb-b7a6-1536006a87ad.json"
)
BATCH_11_SUPPORT_INPUT_VERSION = "e443be3b0ee233e2"
BATCH_11_SUPPORT_RESOLUTION_VERSION = "4d16ff032fda285b"
BATCH_11_SUPPORT_REPORT_SHA256 = (
    "5595dab36555382a6e9bed2b3a8cf9e077a4955834c4c545c0290fa88a69eb5f"
)
BATCH_10_NATIVE_EXECUTION_VERSION = "a8deb2b37461e3f7"
BATCH_10_NATIVE_REPORT_SHA256 = (
    "1dcbb3c5e2fa9fb865c6e10b07461f2796ea9aed7aef4e220a907a667a161913"
)
BATCH_12_SUPPORT_INPUT_VERSION = "e285b204cac5c5fd"
BATCH_12_SUPPORT_RESOLUTION_VERSION = "388eb4b3fd78ea7d"
BATCH_12_SUPPORT_REPORT_SHA256 = (
    "20bb485d3ad0bbef9ef3673bc8737957ffdc9e5ba6c38d92f64b3e0e02525b99"
)
BATCH_11_NATIVE_EXECUTION_VERSION = "f93d909c6218b6e1"
BATCH_11_NATIVE_REPORT_SHA256 = (
    "4be9110ffaa74fbbdab8207598a9744d4ce6c3dace3ef1ef11b2704f34ae41cd"
)
BATCH_12_SOURCE_ENVELOPE_SHA256 = (
    "268c0977394b659f7abc609807b1ae357c4c32fbf6627326f2ecaa9e00cc13ed"
)
BATCH_12_SOURCE_ENVELOPE_BYTES = 1428215
BATCH_12_SOURCE_ENVELOPE_SUFFIX = (
    "raw-public-responses/026-45ef7d06-b57a-36cd-a6ce-37d5ded64f60.json"
)
BATCH_13_SUPPORT_INPUT_VERSION = "8e49c91222bfec6a"
BATCH_13_SUPPORT_RESOLUTION_VERSION = "66cfe44ebf186a31"
BATCH_13_SUPPORT_REPORT_SHA256 = (
    "5cf58e1e0c68ebb4348262e31c9ef8832b0d872dbeacd05c8c629c0e930ae690"
)
BATCH_12_NATIVE_EXECUTION_VERSION = "1170c3f1be7ed8ce"
BATCH_12_NATIVE_REPORT_SHA256 = (
    "0d7c3419d2f7310c88020be9257e84d80abcaa429a779926fe066db00d1bd1db"
)
BATCH_14_SUPPORT_INPUT_VERSION = "159ad89ba2f3c1ba"
BATCH_14_SUPPORT_RESOLUTION_VERSION = "4487c6e7113b3346"
BATCH_14_SUPPORT_REPORT_SHA256 = (
    "1aa9bfa058362359d0c3d259d31de74a6f626c198ee1a7bb15a1b4be82396cf7"
)
BATCH_13_NATIVE_EXECUTION_VERSION = "988668c8c56e7400"
BATCH_13_NATIVE_REPORT_SHA256 = (
    "2a3240bab1b88d302e43e1543fd8d4c3ab8f711ef09dab4bb1c946241ae376c3"
)
BATCH_15_SUPPORT_INPUT_VERSION = "bdf7c6c2ba9005fa"
BATCH_15_SUPPORT_RESOLUTION_VERSION = "5c2d82a6fe8f1d25"
BATCH_15_SUPPORT_REPORT_SHA256 = (
    "f960ff126506bccd82cea2e5f4cdda7db867f2fc22cd7e8e8d03e1c6b9859140"
)
BATCH_14_NATIVE_EXECUTION_VERSION = "0b80309731f4b0b4"
BATCH_14_NATIVE_REPORT_SHA256 = (
    "2232bf0cb539314e62004d537a3ff8245ebe46a34fdbe1e83222c9bf92acb2cf"
)
BATCH_16_SUPPORT_INPUT_VERSION = "10a240a578f79172"
BATCH_16_SUPPORT_RESOLUTION_VERSION = "2bbadf526faae1d3"
BATCH_16_SUPPORT_REPORT_SHA256 = (
    "9486eda41ea78283bb8d93d5cedaf12055e308bf94e6eeb501dd1e3d0f4a246e"
)
BATCH_15_NATIVE_EXECUTION_VERSION = "4c35d988ad94df48"
BATCH_15_NATIVE_REPORT_SHA256 = (
    "38ac5ef1311a48a5a886d430e67630be262a189c23ac3a3c6e1eeacb58a479ec"
)
BATCH_16_SOURCE_ENVELOPE_SHA256 = (
    "04f7ae10990ec49bb7f0f3fa5f92d0b4294bbbd43495abc9b3a2e7743fbb48e3"
)
BATCH_16_SOURCE_ENVELOPE_BYTES = 1323034
BATCH_16_SOURCE_ENVELOPE_SUFFIX = (
    "raw-public-responses/008-485a2faf-7d1c-3f3b-819a-804141bd6ee6.json"
)


def _json_bytes(value: object, *, pretty: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    return b"".join(_json_bytes(dict(row)) for row in rows)


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _array(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return list(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def validate_bulk_support_authority(
    value: Mapping[str, object],
    *,
    batch_index: int,
    action_count: int,
    review_count: int,
    report_sha256: Optional[str] = None,
) -> None:
    """Require the hardened support boundary used by batch 9 and later."""

    if batch_index < 9:
        return
    report = _object(value, f"batch-{batch_index} support authority")
    categories = _object(
        report.get("source_category_counts"), "source category counts"
    )
    codes = _object(
        report.get("classification_code_counts"), "classification code counts"
    )
    input_bindings = _object(report.get("input_bindings"), "support input bindings")
    route_binding = _object(
        input_bindings.get("route_authority"),
        "route authority binding",
    )
    expected_logic_version = (
        "2026-07-18.7"
        if batch_index == 16
        else "2026-07-18.6"
        if batch_index == 15
        else "2026-07-18.5"
        if batch_index >= 10
        else "2026-07-18.3"
    )
    exact_authority = True
    if batch_index == 9:
        exact_authority = (
            report.get("batch_key") == "aafe6ff62efd4f89"
            and report.get("input_version") == BATCH_9_SUPPORT_INPUT_VERSION
            and report.get("resolution_version")
            == BATCH_9_SUPPORT_RESOLUTION_VERSION
            and report.get("source_route_authority_version")
            == BATCH_8_NATIVE_EXECUTION_VERSION
            and route_binding.get("sha256") == BATCH_8_NATIVE_REPORT_SHA256
            and categories
            == {
                "Elementary flows/emission/air": 38,
                "Elementary flows/emission/ground": 8,
                "Elementary flows/emission/water": 54,
            }
            and codes == {"1.1.3": 54, "1.2.3": 8, "1.3.4": 38}
        )
    elif batch_index == 10:
        source_binding = _object(
            input_bindings.get("elementary_source_envelope"),
            "elementary source envelope binding",
        )
        exact_authority = (
            report.get("batch_key") == "183bcb1cf880f679"
            and report.get("input_version") == BATCH_10_SUPPORT_INPUT_VERSION
            and report.get("resolution_version")
            == BATCH_10_SUPPORT_RESOLUTION_VERSION
            and report.get("source_route_authority_version")
            == BATCH_9_NATIVE_EXECUTION_VERSION
            and route_binding.get("sha256") == BATCH_9_NATIVE_REPORT_SHA256
            and isinstance(source_binding.get("path"), str)
            and source_binding["path"].endswith(BATCH_10_SOURCE_ENVELOPE_SUFFIX)
            and source_binding.get("sha256") == BATCH_10_SOURCE_ENVELOPE_SHA256
            and source_binding.get("bytes") == BATCH_10_SOURCE_ENVELOPE_BYTES
            and categories
            == {
                "Elementary flows/emission/air": 57,
                "Elementary flows/emission/ground": 14,
                "Elementary flows/emission/water": 29,
            }
            and codes == {"1.1.3": 29, "1.2.3": 14, "1.3.4": 57}
            and (
                report_sha256 is None
                or report_sha256 == BATCH_10_SUPPORT_REPORT_SHA256
            )
        )
    elif batch_index == 11:
        source_binding = _object(
            input_bindings.get("elementary_source_envelope"),
            "elementary source envelope binding",
        )
        exact_authority = (
            report.get("batch_key") == "48d7fdcb315d1dda"
            and report.get("input_version") == BATCH_11_SUPPORT_INPUT_VERSION
            and report.get("resolution_version")
            == BATCH_11_SUPPORT_RESOLUTION_VERSION
            and report.get("source_route_authority_version")
            == BATCH_10_NATIVE_EXECUTION_VERSION
            and route_binding.get("sha256") == BATCH_10_NATIVE_REPORT_SHA256
            and isinstance(source_binding.get("path"), str)
            and source_binding["path"].endswith(BATCH_10_SOURCE_ENVELOPE_SUFFIX)
            and source_binding.get("sha256") == BATCH_10_SOURCE_ENVELOPE_SHA256
            and source_binding.get("bytes") == BATCH_10_SOURCE_ENVELOPE_BYTES
            and categories
            == {
                "Elementary flows/emission/air": 53,
                "Elementary flows/emission/ground": 10,
                "Elementary flows/emission/water": 37,
            }
            and codes == {"1.1.3": 37, "1.2.3": 10, "1.3.4": 53}
            and (
                report_sha256 is None
                or report_sha256 == BATCH_11_SUPPORT_REPORT_SHA256
            )
        )
    elif batch_index == 12:
        source_binding = _object(
            input_bindings.get("elementary_source_envelope"),
            "elementary source envelope binding",
        )
        exact_authority = (
            report.get("batch_key") == "c168a5b69724b74e"
            and report.get("input_version") == BATCH_12_SUPPORT_INPUT_VERSION
            and report.get("resolution_version")
            == BATCH_12_SUPPORT_RESOLUTION_VERSION
            and report.get("source_route_authority_version")
            == BATCH_11_NATIVE_EXECUTION_VERSION
            and route_binding.get("sha256") == BATCH_11_NATIVE_REPORT_SHA256
            and isinstance(source_binding.get("path"), str)
            and source_binding["path"].endswith(BATCH_12_SOURCE_ENVELOPE_SUFFIX)
            and source_binding.get("sha256") == BATCH_12_SOURCE_ENVELOPE_SHA256
            and source_binding.get("bytes") == BATCH_12_SOURCE_ENVELOPE_BYTES
            and categories
            == {
                "Elementary flows/emission/air": 54,
                "Elementary flows/emission/ground": 12,
                "Elementary flows/emission/water": 34,
            }
            and codes == {"1.1.3": 34, "1.2.3": 12, "1.3.4": 54}
            and (
                report_sha256 is None
                or report_sha256 == BATCH_12_SUPPORT_REPORT_SHA256
            )
        )
    elif batch_index == 13:
        source_binding = _object(
            input_bindings.get("elementary_source_envelope"),
            "elementary source envelope binding",
        )
        exact_authority = (
            report.get("batch_key") == "4d83c19550a6a282"
            and report.get("input_version") == BATCH_13_SUPPORT_INPUT_VERSION
            and report.get("resolution_version")
            == BATCH_13_SUPPORT_RESOLUTION_VERSION
            and report.get("source_route_authority_version")
            == BATCH_12_NATIVE_EXECUTION_VERSION
            and route_binding.get("sha256") == BATCH_12_NATIVE_REPORT_SHA256
            and isinstance(source_binding.get("path"), str)
            and source_binding["path"].endswith(BATCH_12_SOURCE_ENVELOPE_SUFFIX)
            and source_binding.get("sha256") == BATCH_12_SOURCE_ENVELOPE_SHA256
            and source_binding.get("bytes") == BATCH_12_SOURCE_ENVELOPE_BYTES
            and categories
            == {
                "Elementary flows/emission/air": 59,
                "Elementary flows/emission/ground": 13,
                "Elementary flows/emission/water": 28,
            }
            and codes == {"1.1.3": 28, "1.2.3": 13, "1.3.4": 59}
            and (
                report_sha256 is None
                or report_sha256 == BATCH_13_SUPPORT_REPORT_SHA256
            )
        )
    elif batch_index == 14:
        source_binding = _object(
            input_bindings.get("elementary_source_envelope"),
            "elementary source envelope binding",
        )
        exact_authority = (
            report.get("batch_key") == "3d0478a9d71bfc30"
            and report.get("input_version") == BATCH_14_SUPPORT_INPUT_VERSION
            and report.get("resolution_version")
            == BATCH_14_SUPPORT_RESOLUTION_VERSION
            and report.get("source_route_authority_version")
            == BATCH_13_NATIVE_EXECUTION_VERSION
            and route_binding.get("sha256") == BATCH_13_NATIVE_REPORT_SHA256
            and isinstance(source_binding.get("path"), str)
            and source_binding["path"].endswith(BATCH_12_SOURCE_ENVELOPE_SUFFIX)
            and source_binding.get("sha256") == BATCH_12_SOURCE_ENVELOPE_SHA256
            and source_binding.get("bytes") == BATCH_12_SOURCE_ENVELOPE_BYTES
            and categories
            == {
                "Elementary flows/emission/air": 55,
                "Elementary flows/emission/ground": 9,
                "Elementary flows/emission/water": 36,
            }
            and codes == {"1.1.3": 36, "1.2.3": 9, "1.3.4": 55}
            and (
                report_sha256 is None
                or report_sha256 == BATCH_14_SUPPORT_REPORT_SHA256
            )
        )
    elif batch_index == 15:
        source_binding = _object(
            input_bindings.get("elementary_source_envelope"),
            "elementary source envelope binding",
        )
        exact_authority = (
            report.get("batch_key") == "42c33c64a4a06c05"
            and report.get("input_version") == BATCH_15_SUPPORT_INPUT_VERSION
            and report.get("resolution_version")
            == BATCH_15_SUPPORT_RESOLUTION_VERSION
            and report.get("source_route_authority_version")
            == BATCH_14_NATIVE_EXECUTION_VERSION
            and route_binding.get("sha256") == BATCH_14_NATIVE_REPORT_SHA256
            and isinstance(source_binding.get("path"), str)
            and source_binding["path"].endswith(BATCH_12_SOURCE_ENVELOPE_SUFFIX)
            and source_binding.get("sha256") == BATCH_12_SOURCE_ENVELOPE_SHA256
            and source_binding.get("bytes") == BATCH_12_SOURCE_ENVELOPE_BYTES
            and categories
            == {
                "Elementary flows/emission/air": 48,
                "Elementary flows/emission/ground": 6,
                "Elementary flows/emission/water": 41,
                "Elementary flows/resource/ground": 1,
                "Elementary flows/resource/ground/human-dominated": 1,
                "Elementary flows/resource/ground/human-dominated/agricultural": 1,
                "Elementary flows/resource/ground/human-dominated/industrial": 1,
                "Elementary flows/resource/ground/terrestrial/grassland": 1,
            }
            and codes
            == {"1.1.3": 41, "1.2.3": 6, "1.3.4": 48, "2.1.3": 1, "3.1": 4}
            and report.get("flow_property_counts")
            == {"Area*Time|m2*a": 4, "Energy|MJ": 1, "Mass|kg": 95}
            and report.get("low_priority_review_marker_count") == 6
            and report.get("medium_priority_review_marker_count") == 4
            and (
                report_sha256 is None
                or report_sha256 == BATCH_15_SUPPORT_REPORT_SHA256
            )
        )
    elif batch_index == 16:
        source_binding = _object(
            input_bindings.get("elementary_source_envelope"),
            "elementary source envelope binding",
        )
        exact_authority = (
            report.get("batch_key") == "1388a23ccbf90529"
            and report.get("input_version") == BATCH_16_SUPPORT_INPUT_VERSION
            and report.get("resolution_version")
            == BATCH_16_SUPPORT_RESOLUTION_VERSION
            and report.get("source_route_authority_version")
            == BATCH_15_NATIVE_EXECUTION_VERSION
            and route_binding.get("sha256") == BATCH_15_NATIVE_REPORT_SHA256
            and isinstance(source_binding.get("path"), str)
            and source_binding["path"].endswith(BATCH_16_SOURCE_ENVELOPE_SUFFIX)
            and source_binding.get("sha256") == BATCH_16_SOURCE_ENVELOPE_SHA256
            and source_binding.get("bytes") == BATCH_16_SOURCE_ENVELOPE_BYTES
            and categories
            == {
                "Elementary flows/resource/air": 3,
                "Elementary flows/resource/biotic": 1,
                "Elementary flows/resource/ground": 43,
                "Elementary flows/resource/water": 4,
            }
            and codes
            == {
                "2.1.1": 24,
                "2.1.2": 18,
                "2.1.3": 1,
                "2.1.6": 1,
                "2.2.3": 1,
                "2.2.5": 1,
                "2.2.6": 2,
                "2.3.3": 1,
                "2.3.5": 2,
            }
            and report.get("flow_property_counts")
            == {"Energy|MJ": 6, "Mass|kg": 45}
            and report.get("excluded_property_variant_count") == 1
            and report.get("low_priority_review_marker_count") == 0
            and report.get("medium_priority_review_marker_count") == 49
            and (
                report_sha256 is None
                or report_sha256 == BATCH_16_SUPPORT_REPORT_SHA256
            )
        )
    expected_action_count = 51 if batch_index == 16 else 100
    expected_probe_count = 9 if batch_index == 16 else 5 if batch_index == 15 else 3
    expected_support_count = 2 if batch_index == 16 else 3 if batch_index == 15 else 1
    marker_shape_valid = (
        report.get("low_priority_review_marker_count") == 0
        and report.get("medium_priority_review_marker_count") == 49
        and review_count == 49
        if batch_index == 16
        else report.get("low_priority_review_marker_count") == 6
        and report.get("medium_priority_review_marker_count") == 4
        and review_count == 10
        if batch_index == 15
        else categories.get("Elementary flows/emission/ground") == review_count
        and report.get("low_priority_review_marker_count", review_count)
        == review_count
    )
    if (
        action_count != expected_action_count
        or report.get("logic_version") != expected_logic_version
        or report.get("policy_version") != POLICY_VERSION
        or report.get("selected_intent_count") != action_count
        or report.get("source_observation_count") != action_count
        or report.get("classification_decision_count") != action_count
        or report.get("support_assignment_count") != action_count
        or report.get("ready_intent_count") != action_count
        or report.get("native_classification_probe_count") != expected_probe_count
        or report.get("native_invocation_executed_count") != expected_probe_count
        or report.get("support_contract_reuse_count") != expected_support_count
        or report.get("review_marker_count") != review_count
        or not marker_shape_valid
        or sum(_integer(item, "source category count") for item in categories.values())
        != action_count
        or sum(_integer(item, "classification code count") for item in codes.values())
        != action_count
        or report.get("resolution_failure_count") != 0
        or report.get("saved_plan_compilation_authorized") is not True
        or report.get("entity_creation_authorized") is not False
        or report.get("bulk_entity_execution_authorized") is not False
        or report.get("scientific_comparison_ready") is not False
        or report.get("comparative_lcia_allowed") is not False
        or any(report.get(key) != 0 for key in SUPPORT_AUTHORITY_ZERO_EFFECTS)
        or _object(report.get("routing"), "support routing").get(
            "next_internal_route"
        )
        != f"compile-uslci-flow-authoring-bulk-batch-{batch_index}-saved-plans"
        or _object(report.get("routing"), "support routing").get(
            "pause_internal_work"
        )
        is not False
        or not exact_authority
    ):
        raise ValueError(f"batch-{batch_index} support authority drifted")


def _parse_json(content: bytes, label: str) -> JsonObject:
    try:
        return _object(json.loads(content), label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error


def _parse_jsonl(
    content: bytes, label: str, *, allow_empty: bool = False
) -> List[JsonObject]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    rows = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            rows.append(_object(json.loads(line), f"{label} row {index}"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} row {index} is invalid JSON") from error
    if not rows and not allow_empty:
        raise ValueError(f"{label} is empty")
    return rows


def _binding(value: Mapping[str, object], content: bytes, label: str) -> JsonObject:
    row = _object(value, label)
    path = _text(row.get("path"), f"{label} path")
    digest = _digest(row.get("sha256"), f"{label} SHA-256")
    size = _integer(row.get("bytes"), f"{label} byte count")
    if digest != _sha256(content) or size != len(content):
        raise ValueError(f"{label} hash or byte count drifted")
    return {"path": path, "sha256": digest, "bytes": size}


def _artifact_binding(path: str, content: bytes) -> JsonObject:
    return {"path": path, "sha256": _sha256(content), "bytes": len(content)}


def input_version_for_bindings(
    bindings: Sequence[Mapping[str, object]],
) -> str:
    """Return the identity of the exact ordered activity input set."""

    if len(bindings) != len(EXPECTED_INPUT_NAMES):
        raise ValueError("input binding count does not match expected inputs")
    normalized = []
    for index, value in enumerate(bindings):
        row = _object(value, f"input binding {index + 1}")
        normalized.append(
            {
                "role": EXPECTED_INPUT_NAMES[index],
                "path": _text(row.get("path"), "input binding path"),
                "sha256": _digest(row.get("sha256"), "input binding SHA-256"),
                "bytes": _integer(row.get("bytes"), "input binding byte count"),
            }
        )
    return _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "inputs": normalized,
            }
        )
    )[:16]


def _runtime_identity(
    report_value: Mapping[str, object], manifest_value: Mapping[str, object]
) -> JsonObject:
    report = _object(report_value, "runtime report")
    manifest = _object(manifest_value, "runtime manifest")
    if (
        report.get("status") != "portable_native_tool_runtime_closure_verified"
        or report.get("runtime_content_closed") is not True
    ):
        raise ValueError("copied runtime is not verified and content-closed")
    tree = _digest(report.get("tree_sha256"), "runtime tree SHA-256")
    if manifest.get("tree_sha256") != tree:
        raise ValueError("runtime report and manifest tree disagree")
    cli = _object(manifest.get("tiangong_cli"), "TianGong CLI identity")
    if cli.get("package") != "@tiangong-lca/cli" or cli.get("version") != "0.0.25":
        raise ValueError("unsupported copied TianGong CLI")
    return {
        "runtime_tree_sha256": tree,
        "tiangong_cli_package": cli["package"],
        "tiangong_cli_version": cli["version"],
        "same_platform_relocation_proven": report.get(
            "same_platform_relocation_proven"
        )
        is True,
        "cross_platform_portability_proven": False,
    }


def _digest(value: object, label: str) -> str:
    token = _text(value, label)
    if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return token


def _iso_millis(value: object) -> str:
    token = _text(value, "planning time")
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("planning time is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("planning time must include timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def compile_bulk_flow_plan(
    intent_value: Mapping[str, object],
    observation_value: Mapping[str, object],
    support_contract_value: Mapping[str, object],
    decision_value: Mapping[str, object],
    assignment_value: Mapping[str, object],
    readiness_value: Mapping[str, object],
    risk_value: Optional[Mapping[str, object]],
    *,
    planning_time: str,
    runtime_tree_sha256: str,
    resolution_version: str,
    source_artifact_path: str,
    source_artifact_sha256: str,
) -> Tuple[JsonObject, JsonObject, JsonObject]:
    """Return one exact batch plan, field assertion, and execution descriptor."""

    intent = _object(intent_value, "authoring intent")
    observation = _object(observation_value, "source observation")
    support = _object(support_contract_value, "FlowProperty support contract")
    decision = _object(decision_value, "classification decision")
    assignment = _object(assignment_value, "support assignment")
    readiness = _object(readiness_value, "readiness row")
    intent_key = _text(intent.get("authoring_intent_key"), "authoring intent key")
    source_id = _text(intent.get("source_flow_id"), "source Flow ID")
    target = _object(intent.get("target"), "authoring target")
    target_id = _text(target.get("id"), "target Flow ID")
    target_name = _text(target.get("name"), "target Flow name")
    position = _integer(intent.get("position"), "authoring position")
    batch_position = _integer(decision.get("batch_position"), "batch position")
    batch_index = _integer(decision.get("batch_index"), "batch index")
    if batch_index < 2:
        raise ValueError("batch saved-plan index is outside the bulk continuation")
    identity_rows = (decision, assignment, readiness)
    if any(
        row.get("batch_index") != batch_index
        or row.get("batch_position") != batch_position
        or row.get("authoring_intent_key") != intent_key
        or row.get("source_flow_id") != source_id
        or row.get("target_flow_id") != target_id
        for row in identity_rows
    ):
        raise ValueError("batch saved-plan identity chain drifted")
    if (
        decision.get("position") != position
        or observation.get("source_flow_id") != source_id
        or observation.get("source_flow_name") != target_name
        or decision.get("source_flow_name") != target_name
        or intent.get("source_flow_name") != target_name
    ):
        raise ValueError("batch saved-plan source or technical name drifted")
    if (
        intent.get("source_flow_type") != "ELEMENTARY_FLOW"
        or target.get("type_of_dataset") != "Elementary flow"
        or observation.get("source_flow_type") != "ELEMENTARY_FLOW"
        or decision.get("category_type") != "flow-elementary"
    ):
        raise ValueError("batch saved-plan Flow type drifted")

    property_id = _text(intent.get("flow_property_id"), "FlowProperty ID")
    property_name = _text(intent.get("flow_property_name"), "FlowProperty name")
    reference_unit = _text(intent.get("reference_unit"), "reference unit")
    property_spec = SUPPORT_PROPERTY_SPECS.get(property_id)
    support_hash = _sha256(_json_bytes(support))
    runtime_hash = _digest(runtime_tree_sha256, "runtime tree SHA-256")
    if (
        not isinstance(property_spec, dict)
        or property_name != property_spec.get("flow_property_name")
        or reference_unit != property_spec.get("reference_unit")
        or intent.get("conversion_factor") != "1"
        or target.get("flow_property") != property_name
        or target.get("reference_unit") != reference_unit
        or observation.get("flow_property_id") != property_id
        or observation.get("flow_property_name") != property_name
        or observation.get("reference_unit") != reference_unit
        or support.get("flow_property_id") != property_id
        or support.get("flow_property_name") != property_name
        or support.get("flow_property_version")
        != property_spec.get("flow_property_version")
        or support.get("unit_group_id") != property_spec.get("unit_group_id")
        or support.get("unit_group_version")
        != property_spec.get("unit_group_version")
        or support.get("reference_unit") != reference_unit
        or assignment.get("flow_property_id") != property_id
        or assignment.get("flow_property_name") != property_name
        or assignment.get("reference_unit") != reference_unit
        or assignment.get("support_contract_sha256") != support_hash
        or assignment.get("runtime_tree_sha256") != runtime_hash
    ):
        raise ValueError("batch saved-plan FlowProperty support tuple drifted")
    if (
        support.get("support_status") != "resolved_for_native_saved_plan"
        or support.get("saved_plan_reference_authorized") is not True
        or support.get("entity_creation_authorized") is not False
        or assignment.get("support_status") != property_spec.get("assignment_status")
        or assignment.get("saved_plan_reference_authorized") is not True
        or assignment.get("entity_creation_authorized") is not False
        or decision.get("saved_plan_compilation_authorized") is not True
        or decision.get("entity_creation_authorized") is not False
        or readiness.get("readiness_status") != "ready_for_saved_plan_compilation"
        or readiness.get("classification_resolved") is not True
        or readiness.get("support_resolved") is not True
        or readiness.get("source_evidence_bound") is not True
        or readiness.get("saved_plan_compilation_authorized") is not True
        or readiness.get("entity_creation_authorized") is not False
        or readiness.get("classification_decision_id") != decision.get("decision_id")
    ):
        raise ValueError("batch row is not authorized for saved-plan compilation")

    classification_path = [
        _object(value, "classification path node")
        for value in _array(decision.get("classification_path"), "classification path")
    ]
    classification_code = _text(
        decision.get("classification_code"), "classification code"
    )
    if (
        not classification_path
        or classification_path[-1].get("@catId") != classification_code
        or classification_path[-1].get("#text")
        != decision.get("classification_leaf_label")
        or decision.get("source_observation_sha256")
        != observation.get("source_observation_sha256")
    ):
        raise ValueError("batch saved-plan classification path drifted")

    review_required = decision.get("review_required") is True
    if (
        review_required
        and (
            decision.get("decision_status") != "resolved_with_review_marker"
            or decision.get("review_priority") not in {"low", "medium", "high"}
        )
    ) or (
        not review_required
        and (
            decision.get("decision_status") != "resolved"
            or decision.get("review_priority") is not None
        )
    ):
        raise ValueError("batch saved-plan review status drifted")
    review_markers = []
    if review_required:
        if not isinstance(risk_value, dict):
            raise ValueError("required batch review marker was silently dropped")
        risk = dict(risk_value)
        if (
            risk.get("batch_index") != batch_index
            or risk.get("batch_position") != batch_position
            or risk.get("authoring_intent_key") != intent_key
            or risk.get("source_flow_id") != source_id
            or risk.get("classification_code") != classification_code
            or risk.get("severity") != decision.get("review_priority")
            or risk.get("blocks_saved_plan_compilation") is not False
            or risk.get("blocks_entity_creation") is not True
            or risk.get("review_status") != "retained_for_later_human_review"
        ):
            raise ValueError("batch saved-plan review marker drifted")
        review_markers.append(
            {
                "risk_id": risk.get("risk_id"),
                "priority": risk.get("severity"),
                "risk_type": risk.get("risk_type"),
                "reason": risk.get("reason"),
                "blocks_saved_plan_compilation": False,
                "blocks_entity_creation": True,
                "human_review_retained": True,
            }
        )
    elif risk_value is not None:
        raise ValueError("unexpected batch saved-plan review marker")

    source_observation_hash = _digest(
        observation.get("source_observation_sha256"), "source observation SHA-256"
    )
    source_envelope_hash = _digest(source_artifact_sha256, "source artifact SHA-256")
    source_path = _text(source_artifact_path, "source artifact path")
    decision_hash = _sha256(_json_bytes(decision))
    occurred_at = _iso_millis(planning_time)
    resolution = _text(resolution_version, "batch support resolution version")
    field_paths = (
        "target",
        "identity_decision.decision",
        "unit_of_analysis",
        "name_plan.base_name",
        "target.flow_type",
        "flow_property_plan.reference_property",
        "flow_property_plan.reference_unit",
        "native_classification_plan",
    )
    evidence_bindings = [
        {
            "field_path": field_path,
            "authoring_intent_key": intent_key,
            "source_flow_id": source_id,
            "source_observation_sha256": source_observation_hash,
            "source_artifact_sha256": source_envelope_hash,
            "classification_decision_id": decision.get("decision_id"),
            "classification_decision_sha256": decision_hash,
            "support_contract_sha256": support_hash,
            "basis": (
                "accepted stable-key authoring intent plus bound USLCI observation, "
                "native TianGong classification decision and FlowProperty support contract"
            ),
        }
        for field_path in field_paths
    ]
    plan = {
        "schema_version": 1,
        "kind": "flow",
        "ruleset": {"id": "flow-authoring/strict", "version": "1"},
        "target": {
            "uuid": target_id,
            "version": "00.00.001",
            "flow_type": "Elementary flow",
            "classification_path": [
                _text(node.get("#text"), "classification label")
                for node in classification_path
            ],
            "permanent_uri": f"https://data.tiangong.earth/flows/{target_id}.xml",
            "general_comment": (
                f"Source-bound USLCI Flow authoring batch-{batch_index} plan. The exact source "
                "observation, TianGong classification and FlowProperty support evidence are "
                "retained. Remote duplicate coverage, characterization and publication "
                "readiness remain unresolved."
            ),
        },
        "identity_decision": {
            "decision": "create_new",
            "translation_from": "create_new_intent",
            "source_action_key": intent.get("source_action_key"),
            "source_decision_id": intent.get("source_decision_id"),
            "target_id": target_id,
            "remote_duplicate_unknown": intent.get("remote_duplicate_unknown") is True,
        },
        "name_plan": {
            "base_name": target_name,
            "treatment_standards_routes": (
                "No additional treatment or route asserted beyond the accepted technical name."
            ),
            "mix_and_location_types": (
                "USLCI source geography and time remain provenance, not Flow identity."
            ),
        },
        "native_classification_plan": {
            "category_type": decision.get("category_type"),
            "leaf_code": classification_code,
            "path": classification_path,
            "source_decision_id": decision.get("decision_id"),
            "source_decision_status": decision.get("decision_status"),
            "source_confidence": decision.get("confidence"),
            "apply_required_after_materialization": True,
        },
        "flow_property_plan": {
            "mean_value": 1,
            "reference_property": property_name,
            "reference_property_id": property_id,
            "reference_property_version": support.get("flow_property_version"),
            "reference_property_uri": support.get("flow_property_uri"),
            "reference_unit_group": support.get("unit_group_name"),
            "reference_unit_group_id": support.get("unit_group_id"),
            "reference_unit_group_version": support.get("unit_group_version"),
            "reference_unit_group_uri": support.get("unit_group_uri"),
            "reference_unit": reference_unit,
            "support_reference_status": "resolved_for_native_saved_plan",
        },
        "unit_of_analysis": {
            "decision": "declared_unit_dataset",
            "target_kind": "flow",
            "declared_unit": {"amount": 1, "unit": reference_unit},
            "reference_flow": {
                "id": target_id,
                "name": target_name,
                "reference_amount": 1,
                "reference_unit": reference_unit,
                "flow_property": property_name,
            },
        },
        "administrative_information": {
            "time_stamp": occurred_at,
            "version": "00.00.001",
        },
        "evidence_manifest": {
            "sources": [
                {
                    "id": source_id,
                    "source_kind": "uslci_flow_observation",
                    "title": target_name,
                    "artifact_path": source_path,
                    "artifact_sha256": source_envelope_hash,
                    "observation_sha256": source_observation_hash,
                },
                {
                    "id": property_id,
                    "source_kind": "tiangong_flow_property",
                    "title": property_name,
                    "uri": support.get("flow_property_uri"),
                    "version": support.get("flow_property_version"),
                },
                {
                    "id": support.get("unit_group_id"),
                    "source_kind": "tiangong_unit_group",
                    "title": support.get("unit_group_name"),
                    "uri": support.get("unit_group_uri"),
                    "version": support.get("unit_group_version"),
                },
            ],
            "field_bindings": evidence_bindings,
            "lineage": {
                "batch_index": batch_index,
                "batch_position": batch_position,
                "resolution_version": resolution,
                "runtime_tree_sha256": runtime_hash,
                "authoring_intent_key": intent_key,
            },
            "summary": (
                "Evidence-bound USLCI batch Flow plan; exact classification and property "
                "support are resolved while remote uniqueness and publication remain open."
            ),
        },
        "review_markers": review_markers,
        "execution_policy": {
            "saved_plan_required": True,
            "apply_requires_exact_plan_bytes": True,
            "isolated_local_native_gates_allowed_next": True,
            "flow_creation_authorized_in_this_activity": False,
            "flow_materialization_authorized_in_this_activity": False,
            "save_draft_allowed": False,
            "remote_write_allowed": False,
            "model_connection_allowed": False,
            "publication_allowed": False,
            "bulk_entity_release_allowed": False,
        },
        "generation_manifest": {
            "contract": CONTRACT,
            "logic_version": LOGIC_VERSION,
            "policy_version": POLICY_VERSION,
            "batch_index": batch_index,
            "batch_position": batch_position,
            "source_position": position,
            "source_flow_id": source_id,
            "target_flow_id": target_id,
            "authoring_intent_key": intent_key,
            "resolution_version": resolution,
            "support_contract_sha256": support_hash,
            "runtime_tree_sha256": runtime_hash,
            "remote_duplicate_unknown": True,
            "publish_ready": False,
        },
    }
    assertion = {
        "schema_version": 1,
        "batch_index": batch_index,
        "batch_position": batch_position,
        "source_position": position,
        "authoring_intent_key": intent_key,
        "source_flow_id": source_id,
        "target_flow_id": target_id,
        "required_field_count": len(field_paths),
        "bound_required_field_count": len(evidence_bindings),
        "all_required_fields_bound": True,
        "classification_path_bound": True,
        "support_contract_bound": True,
        "source_artifact_bound": True,
        "review_marker_required": review_required,
        "review_marker_preserved": len(review_markers) == (1 if review_required else 0),
        "missing_fields": [],
        "status": "saved_plan_complete",
    }
    descriptor = {
        "schema_version": 1,
        "batch_index": batch_index,
        "batch_position": batch_position,
        "source_position": position,
        "authoring_intent_key": intent_key,
        "source_flow_id": source_id,
        "target_flow_id": target_id,
        "classification_code": classification_code,
        "flow_property_id": property_id,
        "reference_unit": reference_unit,
        "review_marker_count": len(review_markers),
        "status": "saved_plan_compiled_pending_native_gates",
        "entity_creation_authorized": False,
    }
    return plan, assertion, descriptor


def build_plan_index(
    plans: Sequence[Tuple[Mapping[str, object], bytes]],
    *,
    runtime_tree_sha256: str,
    resolution_version: str,
    source_report_sha256: str,
) -> JsonObject:
    """Bind exact plan bytes into a stable-keyed, ordered content index."""

    if not plans:
        raise ValueError("batch saved Flow plan set is empty")
    runtime_hash = _digest(runtime_tree_sha256, "runtime tree SHA-256")
    report_hash = _digest(source_report_sha256, "source support report SHA-256")
    resolution = _text(resolution_version, "batch support resolution version")
    normalized = []
    batch_index = None
    for descriptor_value, content in plans:
        descriptor = _object(descriptor_value, "saved plan descriptor")
        descriptor_batch_index = _integer(
            descriptor.get("batch_index"), "saved plan batch index"
        )
        if descriptor_batch_index < 2:
            raise ValueError("saved plan descriptor is outside the bulk continuation")
        if batch_index is None:
            batch_index = descriptor_batch_index
        if descriptor_batch_index != batch_index:
            raise ValueError("saved plan descriptor batch index drifted")
        batch_position = _integer(
            descriptor.get("batch_position"), "saved plan batch position"
        )
        source_position = _integer(
            descriptor.get("source_position"), "saved plan source position"
        )
        intent_key = _text(
            descriptor.get("authoring_intent_key"), "saved plan intent key"
        )
        source_id = _text(descriptor.get("source_flow_id"), "saved plan source ID")
        target_id = _text(descriptor.get("target_flow_id"), "saved plan target ID")
        digest = _sha256(content)
        normalized.append(
            {
                "batch_index": batch_index,
                "batch_position": batch_position,
                "source_position": source_position,
                "authoring_intent_key": intent_key,
                "source_flow_id": source_id,
                "target_flow_id": target_id,
                "path": (
                    f"outputs/plans/{batch_position:03d}-{target_id}-flow-build-plan.json"
                ),
                "media_type": PLAN_MEDIA_TYPE,
                "digest": f"sha256:{digest}",
                "size": len(content),
                "sha256": digest,
                "bytes": len(content),
            }
        )
    unique_fields = (
        "batch_position",
        "source_position",
        "authoring_intent_key",
        "source_flow_id",
        "target_flow_id",
        "path",
    )
    for field in unique_fields:
        values = [row[field] for row in normalized]
        if len(set(values)) != len(values):
            raise ValueError(f"saved Flow plans contain duplicate {field}")
    ordered = sorted(normalized, key=lambda row: int(row["batch_position"]))
    positions = [int(row["batch_position"]) for row in ordered]
    if positions != list(range(1, len(ordered) + 1)):
        raise ValueError("saved Flow plan batch positions are not contiguous")
    version = _sha256(
        _json_bytes(
            {
                "contract": f"{CONTRACT}/index",
                "runtime_tree_sha256": runtime_hash,
                "resolution_version": resolution,
                "source_report_sha256": report_hash,
                "plans": ordered,
            }
        )
    )[:16]
    return {
        "schema_version": 1,
        "contract": f"{CONTRACT}/index",
        "plan_set_version": version,
        "runtime_tree_sha256": runtime_hash,
        "resolution_version": resolution,
        "source_report_sha256": report_hash,
        "plan_media_type": PLAN_MEDIA_TYPE,
        "apply_requires_exact_plan_bytes": True,
        "ordered_flow_plans": ordered,
    }


def _execution_dag(descriptors: Sequence[Mapping[str, object]]) -> JsonObject:
    """Describe compile completion separately from future local native gates."""

    nodes = []
    edges = []
    ordered = sorted(
        (_object(value, "execution descriptor") for value in descriptors),
        key=lambda row: _integer(row.get("batch_position"), "batch position"),
    )
    positions = [_integer(row.get("batch_position"), "batch position") for row in ordered]
    if positions != list(range(1, len(ordered) + 1)):
        raise ValueError("execution descriptor positions are not contiguous")
    batch_indices = {
        _integer(row.get("batch_index"), "execution descriptor batch index")
        for row in ordered
    }
    if len(batch_indices) != 1:
        raise ValueError("execution descriptors span multiple batches")
    batch_index = batch_indices.pop()
    if batch_index < 2:
        raise ValueError("execution descriptor is outside the bulk continuation")
    terminal_id = f"batch-{batch_index}-native-analysis-gate"
    for row in ordered:
        position = int(row["batch_position"])
        compile_id = f"compile-batch-{batch_index}-flow-{position:03d}"
        gate_id = f"native-gates-batch-{batch_index}-flow-{position:03d}"
        nodes.extend(
            [
                {
                    "node_id": compile_id,
                    "phase": "saved_plan",
                    "status": "completed",
                    "authoring_intent_key": row.get("authoring_intent_key"),
                },
                {
                    "node_id": gate_id,
                    "phase": "isolated_local_native_gates",
                    "status": "pending",
                    "authoring_intent_key": row.get("authoring_intent_key"),
                },
            ]
        )
        edges.append({"from": compile_id, "to": gate_id})
        edges.append({"from": gate_id, "to": terminal_id})
    nodes.append(
        {
            "node_id": terminal_id,
            "phase": "batch_entity_release_analysis",
            "status": "pending",
            "batch_entity_release_authorized": False,
        }
    )
    return {
        "schema_version": 1,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "batch_entity_release_authorized": False,
        "nodes": nodes,
        "edges": edges,
    }


def _unique_by(
    values: Sequence[Mapping[str, object]], key: str, label: str
) -> Dict[object, JsonObject]:
    result = {}
    for value in values:
        row = _object(value, label)
        identity = row.get(key)
        if identity is None or identity in result:
            raise ValueError(f"{label} {key} values are missing or duplicated")
        result[identity] = row
    return result


def _verify_upstream_binding(
    owner: Mapping[str, object],
    role: str,
    supplied: Mapping[str, object],
    label: str,
) -> None:
    declared = _object(
        _object(owner.get("artifact_bindings"), f"{label} artifact bindings").get(
            role
        ),
        f"{label} {role} binding",
    )
    if (
        declared.get("sha256") != supplied.get("sha256")
        or declared.get("bytes") != supplied.get("bytes")
    ):
        raise ValueError(f"{label} artifact drifted: {role}")


def compile_and_materialize(
    *,
    contents: Mapping[str, bytes],
    bindings: Mapping[str, Mapping[str, object]],
    out_dir: Path,
) -> JsonObject:
    """Verify fourteen inputs and materialize the exact 100-plan batch."""

    missing = [
        name
        for name in EXPECTED_INPUT_NAMES
        if name not in contents or name not in bindings
    ]
    if missing:
        raise ValueError(f"batch saved-plan inputs are incomplete: {missing}")
    verified_bindings = [
        _binding(bindings[name], contents[name], f"{name} binding")
        for name in EXPECTED_INPUT_NAMES
    ]
    verified_by_name = dict(zip(EXPECTED_INPUT_NAMES, verified_bindings))
    input_version = input_version_for_bindings(verified_bindings)
    bulk_report = _parse_json(contents["bulk_support_report"], "bulk support report")
    batch_index = _integer(bulk_report.get("batch_index"), "bulk support batch index")
    batch_key = _text(bulk_report.get("batch_key"), "bulk support batch key")
    action_count = _integer(
        bulk_report.get("selected_intent_count"), "selected intent count"
    )
    review_count = _integer(
        bulk_report.get("review_marker_count"), "review marker count"
    )
    if (
        9 <= batch_index < 16
        and action_count != 100
        or batch_index == 16
        and action_count != 51
    ):
        raise ValueError(f"batch-{batch_index} support authority drifted")
    if batch_index < 2 or action_count < 1 or action_count > 100:
        raise ValueError("accepted batch support has an invalid bulk descriptor")
    expected_status = (
        f"uslci_flow_authoring_bulk_batch_{batch_index}_supports_resolved"
    )
    expected_route = (
        f"compile-uslci-flow-authoring-bulk-batch-{batch_index}-saved-plans"
    )
    next_route = (
        f"execute-uslci-flow-authoring-bulk-batch-{batch_index}-native-gates"
    )
    expected_support_logic = (
        "2026-07-18.1"
        if batch_index == 2
        else "2026-07-18.7"
        if batch_index == 16
        else "2026-07-18.6"
        if batch_index == 15
        else "2026-07-18.5"
        if batch_index >= 10
        else "2026-07-18.3"
        if batch_index >= 9
        else "2026-07-18.2"
    )
    selected_batch = _parse_json(contents["selected_batch"], "selected batch")
    authoring_report = _parse_json(
        contents["authoring_plan_report"], "authoring plan report"
    )
    support_report = _parse_json(contents["support_report"], "support report")
    runtime_report = _parse_json(contents["runtime_report"], "runtime report")
    runtime_manifest = _parse_json(contents["runtime_manifest"], "runtime manifest")
    planning_anchor = _parse_json(contents["planning_anchor"], "planning anchor")
    validate_bulk_support_authority(
        bulk_report,
        batch_index=batch_index,
        action_count=action_count,
        review_count=review_count,
    )
    if (
        bulk_report.get("contract") != BULK_SUPPORT_CONTRACT
        or bulk_report.get("status") != expected_status
        or bulk_report.get("logic_version") != expected_support_logic
        or bulk_report.get("policy_version") != "2026-07-18.1"
        or bulk_report.get("source_observation_count") != action_count
        or bulk_report.get("classification_decision_count") != action_count
        or bulk_report.get("support_assignment_count") != action_count
        or bulk_report.get("ready_intent_count") != action_count
        or review_count < 0
        or review_count > action_count
        or bulk_report.get("resolution_failure_count") != 0
        or bulk_report.get("saved_plan_compilation_authorized") is not True
        or bulk_report.get("entity_creation_authorized") is not False
        or sum(
            _integer(value, "source category count")
            for value in _object(
                bulk_report.get("source_category_counts"), "source category counts"
            ).values()
        )
        != action_count
        or sum(
            _integer(value, "classification code count")
            for value in _object(
                bulk_report.get("classification_code_counts"),
                "classification code counts",
            ).values()
        )
        != action_count
        or _object(bulk_report.get("routing"), "bulk support routing").get(
            "next_internal_route"
        )
        != expected_route
        or _object(bulk_report.get("routing"), "bulk support routing").get(
            "pause_internal_work"
        )
        is not False
    ):
        raise ValueError("accepted batch support authority drifted")
    for role in (
        "selected_batch",
        "source_observations",
        "classification_decisions",
        "support_assignments",
        "readiness",
        "review_markers",
    ):
        _verify_upstream_binding(
            bulk_report, role, verified_by_name[role], "bulk support report"
        )
    if (
        authoring_report.get("contract") != AUTHORING_CONTRACT
        or authoring_report.get("status") != "uslci_flow_authoring_plan_compiled"
        or authoring_report.get("authoring_plan_version") != "44d477987db9e8fe"
        or authoring_report.get("authoring_intent_count") != 1456
        or authoring_report.get("authoring_batch_count") != 16
        or authoring_report.get("maximum_bulk_batch_size") != 100
        or bulk_report.get("source_authoring_plan_version")
        != authoring_report.get("authoring_plan_version")
    ):
        raise ValueError("authoring plan authority drifted")
    _verify_upstream_binding(
        authoring_report,
        "authoring_intents",
        verified_by_name["authoring_intents"],
        "authoring plan",
    )
    if (
        support_report.get("contract") != SUPPORT_CONTRACT
        or support_report.get("status")
        != "uslci_flow_authoring_canary_supports_resolved"
        or support_report.get("support_resolution_version") != "4575e0df62e45cfa"
        or support_report.get("support_contract_count") != 3
        or support_report.get("bulk_release_authorized") is not False
        or bulk_report.get("source_support_resolution_version")
        != support_report.get("support_resolution_version")
    ):
        raise ValueError("Mass support authority drifted")
    _verify_upstream_binding(
        support_report,
        "support_contracts",
        verified_by_name["support_contracts"],
        "support report",
    )

    runtime = _runtime_identity(runtime_report, runtime_manifest)
    runtime_tree = str(runtime["runtime_tree_sha256"])
    if bulk_report.get("runtime_tree_sha256") != runtime_tree:
        raise ValueError("batch support and copied runtime tree disagree")
    resolution_version = _text(
        bulk_report.get("resolution_version"), "batch support resolution version"
    )
    if (
        planning_anchor.get("contract")
        != "lca_research_kernel.uslci_flow_authoring_bulk_saved_plan_anchor/v1"
        or planning_anchor.get("resolution_version") != resolution_version
        or planning_anchor.get("support_resolution_report_sha256")
        != verified_by_name["bulk_support_report"].get("sha256")
    ):
        raise ValueError("planning anchor does not bind the accepted batch support event")
    planning_time = _iso_millis(planning_anchor.get("occurred_at"))

    intents = _parse_jsonl(contents["authoring_intents"], "authoring intents")
    observations = _parse_jsonl(
        contents["source_observations"], "source observations"
    )
    decisions = _parse_jsonl(
        contents["classification_decisions"], "classification decisions"
    )
    assignments = _parse_jsonl(
        contents["support_assignments"], "support assignments"
    )
    readiness_rows = _parse_jsonl(contents["readiness"], "readiness rows")
    risks = _parse_jsonl(
        contents["review_markers"], "review markers", allow_empty=True
    )
    supports = _parse_jsonl(contents["support_contracts"], "support contracts")
    if (
        len(intents) != 1456
        or len(observations) != action_count
        or len(decisions) != action_count
        or len(assignments) != action_count
        or len(readiness_rows) != action_count
        or len(risks) != review_count
        or len(supports) != 3
    ):
        raise ValueError("batch saved-plan upstream cardinality drifted")

    selected_keys = [
        _text(value, "selected authoring intent key")
        for value in _array(
            selected_batch.get("authoring_intent_keys"),
            "selected authoring intent keys",
        )
    ]
    selected_positions = [
        _integer(value, "selected source position")
        for value in _array(selected_batch.get("source_positions"), "source positions")
    ]
    if (
        selected_batch.get("batch_index") != batch_index
        or selected_batch.get("batch_key") != batch_key
        or selected_batch.get("action_count") != action_count
        or selected_batch.get("stable_key_join") != "authoring_intent_key"
        or len(selected_keys) != action_count
        or len(set(selected_keys)) != action_count
        or len(selected_positions) != action_count
        or len(set(selected_positions)) != action_count
    ):
        raise ValueError("selected batch identity or cardinality drifted")
    intent_by_key = _unique_by(intents, "authoring_intent_key", "authoring intent")
    if any(key not in intent_by_key for key in selected_keys):
        raise ValueError("selected batch authoring intent is missing")
    selected_intents = [intent_by_key[key] for key in selected_keys]
    if [row.get("position") for row in selected_intents] != selected_positions:
        raise ValueError("selected batch authoring intent order drifted")

    observation_by_id = _unique_by(
        observations, "source_flow_id", "source observation"
    )
    decision_by_key = _unique_by(
        decisions, "authoring_intent_key", "classification decision"
    )
    assignment_by_key = _unique_by(
        assignments, "authoring_intent_key", "support assignment"
    )
    readiness_by_key = _unique_by(
        readiness_rows, "authoring_intent_key", "readiness row"
    )
    risk_by_key = _unique_by(risks, "authoring_intent_key", "review marker")
    if (
        set(decision_by_key) != set(selected_keys)
        or set(assignment_by_key) != set(selected_keys)
        or set(readiness_by_key) != set(selected_keys)
        or set(observation_by_id)
        != {row.get("source_flow_id") for row in selected_intents}
    ):
        raise ValueError("batch saved-plan stable-key ledgers disagree")
    for key in selected_keys:
        intent = intent_by_key[key]
        source_id = intent.get("source_flow_id")
        observation = observation_by_id.get(source_id, {})
        decision = decision_by_key[key]
        if observation.get("source_category") != decision.get("source_category"):
            raise ValueError("batch source category and classification decision drifted")
    actual_category_counts = dict(
        Counter(
            _text(row.get("source_category"), "source observation category")
            for row in observations
        )
    )
    actual_classification_counts = dict(
        Counter(
            _text(row.get("classification_code"), "classification decision code")
            for row in decisions
        )
    )
    if (
        actual_category_counts
        != _object(bulk_report.get("source_category_counts"), "source category counts")
        or actual_classification_counts
        != _object(
            bulk_report.get("classification_code_counts"),
            "classification code counts",
        )
    ):
        raise ValueError("batch category or classification count ledger drifted")
    expected_risk_keys = {
        key for key, row in decision_by_key.items() if row.get("review_required") is True
    }
    if (
        set(risk_by_key) != expected_risk_keys
        or len(expected_risk_keys) != review_count
    ):
        raise ValueError("batch saved-plan review marker membership drifted")
    batch_positions = [
        _integer(decision_by_key[key].get("batch_position"), "batch position")
        for key in selected_keys
    ]
    if batch_positions != list(range(1, action_count + 1)):
        raise ValueError("classification decision order drifted")

    support_by_property = _unique_by(
        supports, "flow_property_id", "FlowProperty support contract"
    )
    if set(support_by_property) != set(SUPPORT_PROPERTY_SPECS):
        raise ValueError("accepted FlowProperty support contract set drifted")
    mass_support = support_by_property[MASS_PROPERTY_ID]
    mass_support_hash = _sha256(_json_bytes(mass_support))
    if bulk_report.get("mass_support_contract_sha256") != mass_support_hash:
        raise ValueError("batch support report Mass contract hash drifted")
    for row in assignments:
        property_id = row.get("flow_property_id")
        support = support_by_property.get(property_id)
        if (
            not isinstance(support, dict)
            or row.get("batch_index") != batch_index
            or row.get("support_resolution_version")
            != support_report.get("support_resolution_version")
            or row.get("support_contract_sha256") != _sha256(_json_bytes(support))
            or row.get("runtime_tree_sha256") != runtime_tree
        ):
            raise ValueError("support assignment authority drifted")

    source_binding = _object(
        _object(bulk_report.get("input_bindings"), "bulk support input bindings").get(
            "elementary_source_envelope"
        ),
        "elementary source envelope binding",
    )
    source_artifact_path = _text(
        source_binding.get("path"), "elementary source artifact path"
    )
    source_artifact_sha256 = _digest(
        source_binding.get("sha256"), "elementary source artifact SHA-256"
    )

    plan_contents = []
    assertions = []
    descriptors = []
    for intent in selected_intents:
        intent_key = str(intent["authoring_intent_key"])
        source_id = intent.get("source_flow_id")
        plan, assertion, descriptor = compile_bulk_flow_plan(
            intent,
            observation_by_id.get(source_id, {}),
            support_by_property.get(intent.get("flow_property_id"), {}),
            decision_by_key.get(intent_key, {}),
            assignment_by_key.get(intent_key, {}),
            readiness_by_key.get(intent_key, {}),
            risk_by_key.get(intent_key),
            planning_time=planning_time,
            runtime_tree_sha256=runtime_tree,
            resolution_version=resolution_version,
            source_artifact_path=source_artifact_path,
            source_artifact_sha256=source_artifact_sha256,
        )
        content = _json_bytes(plan, pretty=True)
        assertions.append(assertion)
        descriptors.append(descriptor)
        plan_contents.append((descriptor, content))
    index = build_plan_index(
        plan_contents,
        runtime_tree_sha256=runtime_tree,
        resolution_version=resolution_version,
        source_report_sha256=str(verified_by_name["bulk_support_report"]["sha256"]),
    )
    dag = _execution_dag(descriptors)
    review_carry = [
        {
            "schema_version": 1,
            "risk_id": row.get("risk_id"),
            "batch_index": batch_index,
            "batch_position": row.get("batch_position"),
            "authoring_intent_key": row.get("authoring_intent_key"),
            "source_flow_id": row.get("source_flow_id"),
            "severity": row.get("severity"),
            "reason": row.get("reason"),
            "saved_plan_compilation_completed": True,
            "native_gate_review_retained": True,
            "entity_creation_blocked": True,
        }
        for row in sorted(risks, key=lambda value: int(value.get("batch_position", 0)))
    ]
    output_prefix = f"uslci-flow-authoring-bulk-batch-{batch_index}-saved-plan"
    output_rows = {
        "plan_index": (
            f"{output_prefix}-index.json",
            _json_bytes(index, pretty=True),
        ),
        "field_assertions": (
            f"{output_prefix}-field-assertions.jsonl",
            _jsonl_bytes(assertions),
        ),
        "execution_descriptors": (
            f"{output_prefix}-execution-descriptors.jsonl",
            _jsonl_bytes(descriptors),
        ),
        "review_marker_carry_forward": (
            f"{output_prefix}-review-markers.jsonl",
            _jsonl_bytes(review_carry),
        ),
        "execution_dag": (
            f"{output_prefix}-execution-dag.json",
            _json_bytes(dag, pretty=True),
        ),
        "failures": (
            f"{output_prefix}-failures.jsonl",
            b"",
        ),
    }
    artifact_bindings = {
        key: _artifact_binding(filename, content)
        for key, (filename, content) in output_rows.items()
    }
    plan_bindings = list(index["ordered_flow_plans"])
    route_authority_binding = _object(
        bulk_report.get("input_bindings"), "support input bindings"
    ).get("route_authority")
    route_authority_report_sha256 = (
        route_authority_binding.get("sha256")
        if isinstance(route_authority_binding, dict)
        else None
    )
    result = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "policy_version": POLICY_VERSION,
        "status": f"uslci_flow_authoring_bulk_batch_{batch_index}_saved_plans_compiled",
        "scope_id": bulk_report.get("scope_id"),
        "revision": bulk_report.get("revision"),
        "batch_index": batch_index,
        "batch_key": batch_key,
        "input_version": input_version,
        "source_authoring_plan_version": authoring_report.get(
            "authoring_plan_version"
        ),
        "source_support_resolution_version": support_report.get(
            "support_resolution_version"
        ),
        "source_bulk_resolution_version": resolution_version,
        "source_bulk_support_logic_version": bulk_report.get("logic_version"),
        "source_route_authority_version": bulk_report.get(
            "source_route_authority_version"
        ),
        "source_route_authority_report_sha256": route_authority_report_sha256,
        "source_bulk_support_report_sha256": verified_by_name[
            "bulk_support_report"
        ]["sha256"],
        "saved_plan_set_version": index["plan_set_version"],
        "input_bindings": verified_bindings,
        "runtime_identity": runtime,
        "source_artifact_binding": source_binding,
        "mass_support_contract_sha256": mass_support_hash,
        "artifact_bindings": artifact_bindings,
        "saved_plan_bindings": plan_bindings,
        "saved_flow_plan_count": len(plan_contents),
        "content_descriptor_count": len(plan_bindings),
        "complete_plan_count": sum(
            bool(row["all_required_fields_bound"]) for row in assertions
        ),
        "unique_intent_count": len(
            {row["authoring_intent_key"] for row in descriptors}
        ),
        "unique_source_flow_count": len({row["source_flow_id"] for row in descriptors}),
        "unique_target_flow_count": len({row["target_flow_id"] for row in descriptors}),
        "classification_path_bound_count": sum(
            bool(row["classification_path_bound"]) for row in assertions
        ),
        "support_contract_bound_count": sum(
            bool(row["support_contract_bound"]) for row in assertions
        ),
        "source_artifact_bound_count": sum(
            bool(row["source_artifact_bound"]) for row in assertions
        ),
        "represented_support_contract_count": len(
            {row["flow_property_id"] for row in descriptors}
        ),
        "source_category_counts": bulk_report.get("source_category_counts"),
        "classification_code_counts": bulk_report.get(
            "classification_code_counts"
        ),
        "flow_property_counts": dict(
            sorted(
                Counter(
                    f"{row['flow_property_name']}|{row['reference_unit']}"
                    for row in assignments
                ).items()
            )
        ),
        "review_marker_carry_forward_count": len(review_carry),
        "low_priority_review_marker_count": sum(
            row.get("severity") == "low" for row in review_carry
        ),
        "medium_priority_review_marker_count": sum(
            row.get("severity") == "medium" for row in review_carry
        ),
        "missing_required_field_count": sum(
            len(row["missing_fields"]) for row in assertions
        ),
        "execution_dag_node_count": dag["node_count"],
        "execution_dag_edge_count": dag["edge_count"],
        "planning_failure_count": 0,
        "entity_creation_authorized_count": 0,
        "batch_entity_release_authorized": False,
        **{key: 0 for key in ZERO_SIDE_EFFECTS},
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "external_patterns": [
            {
                "project": "hashicorp/terraform",
                "local_landing": "exact_saved_plan_bytes_bound_before_future_native_gates",
                "installed": False,
            },
            {
                "project": "opencontainers/image-spec",
                "local_landing": "per_plan_media_type_digest_size_and_path_descriptors",
                "installed": False,
            },
            {
                "project": "helm/helm",
                "local_landing": "plan_rendering_separated_from_native_materialize_validate_and_save",
                "installed": False,
            },
            {
                "project": "open-policy-agent/opa",
                "local_landing": "accepted_decision_and_review_marker_identity_copied_not_recomputed",
                "installed": False,
            },
        ],
        "routing": {"next_internal_route": next_route, "pause_internal_work": False},
        "next_action": (
            "Execute isolated copied-runtime native materialize, classification, validate "
            "and QA gates for these exact 100 plan bytes; keep save and entity release closed."
        ),
    }
    outputs = out_dir / "outputs"
    plans_dir = outputs / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    content_by_position = {
        int(descriptor["batch_position"]): content
        for descriptor, content in plan_contents
    }
    for row in plan_bindings:
        (out_dir / str(row["path"])).write_bytes(
            content_by_position[int(row["batch_position"])]
        )
    for filename, content in output_rows.values():
        (outputs / filename).write_bytes(content)
    (outputs / f"uslci-flow-authoring-bulk-batch-{batch_index}-saved-plans.json").write_bytes(
        _json_bytes(result, pretty=True)
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile one exact TianGong Flow authoring bulk-batch saved-plan set."
    )
    for name in EXPECTED_INPUT_NAMES:
        option = name.replace("_", "-")
        parser.add_argument(f"--{option}", type=Path, required=True)
        parser.add_argument(f"--{option}-binding", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    contents = {}
    bindings = {}
    for name in EXPECTED_INPUT_NAMES:
        contents[name] = getattr(args, name).read_bytes()
        bindings[name] = _parse_json(
            getattr(args, f"{name}_binding").read_bytes(), f"{name} binding"
        )
    compile_and_materialize(contents=contents, bindings=bindings, out_dir=args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
