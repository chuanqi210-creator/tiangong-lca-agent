"""Execute exact bulk TianGong Flow plans through isolated local native gates."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lca_research_kernel.uslci_flow_authoring_canary_native_gates import (  # noqa: E402
    verify_classified_flow,
)


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_flow_authoring_bulk_batch_native_gates/v1"
LOGIC_VERSION = "2026-07-18.7"
POLICY_VERSION = "2026-07-18.1"
CORPUS_CLOSURE_ROUTE = "analyze-uslci-flow-authoring-corpus-closure"
MAX_PLAN_COUNT = 100
MAX_WORKERS = 4
NATIVE_CALLS_PER_BRANCH = 5
SAVED_PLAN_CONTRACT = (
    "lca_research_kernel.uslci_flow_authoring_bulk_batch_saved_plans/v1"
)
TYPED_QA_CONTRACT = "lca_research_kernel.uslci_elementary_flow_native_qa_harness/v1"
PLAN_MEDIA_TYPE = "application/vnd.tiangong.flow-build-plan.v1+json"
CLI_PATH = "npm-runtime/node_modules/@tiangong-lca/cli/bin/tiangong-lca.js"
KNOWN_MESSAGE = (
    "Flow type is Elementary flow; check whether this batch should exclude it."
)
EXPECTED_RULESET = {
    "ruleset_id": "flow-authoring/strict",
    "ruleset_version": "1",
    "ruleset_source_version": "2026.05.23",
}
EXPECTED_RULE_IDS = [
    "tidas.flow.name.base-name.technical",
    "tidas.flow.type.required",
    "tidas.flow.reference-property-unit.required",
    "tidas.flow.flow-property.mean-value.positive",
    "tidas.flow.classification.elementary.valid",
    "tidas.flow.evidence.field-bindings.required",
    "tidas.flow.identity.alias-equivalence.review",
]
EXPECTED_INPUT_NAMES = (
    "saved_plan_report",
    "plan_index",
    "execution_descriptors",
    "review_marker_carry_forward",
    "saved_plan_manifest",
    "typed_qa_report",
    "applicability_decisions",
    "product_control",
    "runtime_report",
    "runtime_manifest",
)
ZERO_SIDE_EFFECTS = (
    "network_read_executed_count",
    "remote_candidate_search_executed_count",
    "flow_creation_executed_count",
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


def _jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    return b"".join(_json_bytes(dict(row)) for row in rows)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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


def _digest(value: object, label: str) -> str:
    token = _text(value, label)
    if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return token


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
    size = _integer(row.get("bytes"), f"{label} bytes")
    if digest != _sha256(content) or size != len(content):
        raise ValueError(f"{label} hash or byte count drifted")
    return {"path": path, "sha256": digest, "bytes": size}


def _artifact_binding(path: str, content: bytes) -> JsonObject:
    return {
        "path": path,
        "media_type": mimetypes.guess_type(path)[0] or "application/octet-stream",
        "sha256": _sha256(content),
        "digest": f"sha256:{_sha256(content)}",
        "bytes": len(content),
        "size": len(content),
    }


def validate_saved_plan_authority(
    saved_report_value: Mapping[str, object], *, report_sha256: Optional[str] = None
) -> JsonObject:
    """Validate the exact saved-plan authority before any native branch starts."""

    report = _object(saved_report_value, "saved-plan report")
    batch_index = _integer(report.get("batch_index"), "saved-plan batch index")
    plan_count = _integer(report.get("saved_flow_plan_count"), "saved-plan count")
    review_count = _integer(
        report.get("review_marker_carry_forward_count"),
        "saved-plan review marker count",
    )
    expected_plan_count = 100 if batch_index < 16 else 51
    expected_logic = (
        "2026-07-18.1"
        if batch_index == 2
        else "2026-07-18.2"
        if batch_index < 9
        else "2026-07-18.6"
        if batch_index == 16
        else "2026-07-18.5"
        if batch_index == 15
        else "2026-07-18.4"
        if batch_index >= 10
        else "2026-07-18.3"
    )
    expected_route = (
        f"execute-uslci-flow-authoring-bulk-batch-{batch_index}-native-gates"
    )
    next_route = (
        CORPUS_CLOSURE_ROUTE
        if batch_index == 16
        else f"resolve-uslci-flow-authoring-bulk-batch-{batch_index + 1}-supports"
    )
    categories = _object(
        report.get("source_category_counts"), "saved-plan source category counts"
    )
    routing = _object(report.get("routing"), "saved-plan routing")
    if (
        batch_index < 2
        or batch_index > 16
        or plan_count != expected_plan_count
        or review_count < 0
        or review_count > plan_count
        or (
            batch_index not in {15, 16}
            and review_count != categories.get("Elementary flows/emission/ground")
        )
        or report.get("contract") != SAVED_PLAN_CONTRACT
        or report.get("logic_version") != expected_logic
        or report.get("policy_version") != POLICY_VERSION
        or report.get("status")
        != f"uslci_flow_authoring_bulk_batch_{batch_index}_saved_plans_compiled"
        or report.get("content_descriptor_count") != plan_count
        or report.get("complete_plan_count") != plan_count
        or report.get("planning_failure_count") != 0
        or report.get("batch_entity_release_authorized") is not False
        or routing.get("next_internal_route") != expected_route
        or routing.get("pause_internal_work") is not False
    ):
        raise ValueError(
            f"accepted batch-{batch_index} saved-plan authority drifted"
        )
    if batch_index == 9:
        exact = {
            "batch_key": "aafe6ff62efd4f89",
            "input_version": "07c12c14dc1dca0c",
            "saved_plan_set_version": "3c3a9424a7cb7a22",
            "source_bulk_support_logic_version": "2026-07-18.3",
            "source_bulk_resolution_version": "5160dd3a971e0af0",
            "source_bulk_support_report_sha256": (
                "9e3dd2a0"
                "27012cb0"
                "6994b6a6"
                "2abd218d"
                "540c8cea"
                "93ba2514"
                "97b6ac70"
                "f4fa8c18"
            ),
            "source_authoring_plan_version": "44d477987db9e8fe",
            "source_support_resolution_version": "4575e0df62e45cfa",
            "mass_support_contract_sha256": (
                "a6b8e90c"
                "308fe840"
                "2f96dbb4"
                "0ac65629"
                "e6ed3968"
                "6b35bae9"
                "52392031"
                "06a957dc"
            ),
            "source_route_authority_version": "b208fec5b25c212a",
            "source_route_authority_report_sha256": (
                "cffc9886"
                "e9b1b091"
                "1b03f5cf"
                "5633757a"
                "8e300301"
                "680b0cf6"
                "a6f4f237"
                "1463b6b7"
            ),
        }
        if (
            any(report.get(key) != value for key, value in exact.items())
            or categories
            != {
                "Elementary flows/emission/air": 38,
                "Elementary flows/emission/ground": 8,
                "Elementary flows/emission/water": 54,
            }
            or report_sha256
            != "0900fb8e5048803b02915906e967fb4a56ec03f15e4d0d3b19b7ba03c0a0c76d"
        ):
            raise ValueError("accepted batch-9 saved-plan authority drifted")
    elif batch_index == 10:
        exact = {
            "batch_key": "183bcb1cf880f679",
            "input_version": "01f9068619c0ee41",
            "saved_plan_set_version": "b238bd643791013c",
            "source_bulk_support_logic_version": "2026-07-18.5",
            "source_bulk_resolution_version": "1284b1d20a584a48",
            "source_bulk_support_report_sha256": (
                "dbc989e1"
                "95106db7"
                "c9f15840"
                "75a7bbf4"
                "5a8451b7"
                "a9ade3bb"
                "c85090b2"
                "a11cb551"
            ),
            "source_authoring_plan_version": "44d477987db9e8fe",
            "source_support_resolution_version": "4575e0df62e45cfa",
            "mass_support_contract_sha256": (
                "a6b8e90c"
                "308fe840"
                "2f96dbb4"
                "0ac65629"
                "e6ed3968"
                "6b35bae9"
                "52392031"
                "06a957dc"
            ),
            "source_route_authority_version": "56870a987f96fb17",
            "source_route_authority_report_sha256": (
                "655a58b0"
                "6c565f42"
                "3b16919f"
                "8db06673"
                "db13e4c8"
                "fc8a2647"
                "e46b9965"
                "24dddb84"
            ),
        }
        if (
            any(report.get(key) != value for key, value in exact.items())
            or categories
            != {
                "Elementary flows/emission/air": 57,
                "Elementary flows/emission/ground": 14,
                "Elementary flows/emission/water": 29,
            }
            or report_sha256
            != "265009f491b1d951fc02ac32c20b9185f6a5b0ef6dcae2a2b47ffaaf7d3325dd"
        ):
            raise ValueError("accepted batch-10 saved-plan authority drifted")
    elif batch_index == 11:
        exact = {
            "batch_key": "48d7fdcb315d1dda",
            "input_version": "49b055931e404a01",
            "saved_plan_set_version": "340858c063024f8b",
            "source_bulk_support_logic_version": "2026-07-18.5",
            "source_bulk_resolution_version": "4d16ff032fda285b",
            "source_bulk_support_report_sha256": (
                "5595dab3"
                "6555382a"
                "6e9bed2b"
                "3a8cf9e0"
                "77a49558"
                "34c4c545"
                "c0290fa8"
                "8a69eb5f"
            ),
            "source_authoring_plan_version": "44d477987db9e8fe",
            "source_support_resolution_version": "4575e0df62e45cfa",
            "mass_support_contract_sha256": (
                "a6b8e90c"
                "308fe840"
                "2f96dbb4"
                "0ac65629"
                "e6ed3968"
                "6b35bae9"
                "52392031"
                "06a957dc"
            ),
            "source_route_authority_version": "a8deb2b37461e3f7",
            "source_route_authority_report_sha256": (
                "1dcbb3c5"
                "e2fa9fb8"
                "65c6e10b"
                "07461f27"
                "96ea9aed"
                "7aef4e22"
                "0a907a66"
                "7a161913"
            ),
        }
        if (
            any(report.get(key) != value for key, value in exact.items())
            or categories
            != {
                "Elementary flows/emission/air": 53,
                "Elementary flows/emission/ground": 10,
                "Elementary flows/emission/water": 37,
            }
            or report_sha256
            != "b0cf6ba4752baf26a4bbbbf130ffeee570b963bd91a47451cba39240cbb1d20c"
        ):
            raise ValueError("accepted batch-11 saved-plan authority drifted")
    elif batch_index == 12:
        exact = {
            "batch_key": "c168a5b69724b74e",
            "input_version": "a43b51abf309f13d",
            "saved_plan_set_version": "e21a6c5d8654c16a",
            "source_bulk_support_logic_version": "2026-07-18.5",
            "source_bulk_resolution_version": "388eb4b3fd78ea7d",
            "source_bulk_support_report_sha256": (
                "20bb485d"
                "3ad0bbef"
                "9ef3673b"
                "c8737957"
                "ffdc9e5b"
                "a6c38d92"
                "f64b3e0e"
                "02525b99"
            ),
            "source_authoring_plan_version": "44d477987db9e8fe",
            "source_support_resolution_version": "4575e0df62e45cfa",
            "mass_support_contract_sha256": (
                "a6b8e90c"
                "308fe840"
                "2f96dbb4"
                "0ac65629"
                "e6ed3968"
                "6b35bae9"
                "52392031"
                "06a957dc"
            ),
            "source_route_authority_version": "f93d909c6218b6e1",
            "source_route_authority_report_sha256": (
                "4be9110f"
                "faa74fbb"
                "dab82075"
                "98a9744d"
                "4ce6c3da"
                "ce3ef1ef"
                "11b2704f"
                "34ae41cd"
            ),
        }
        source = _object(
            report.get("source_artifact_binding"), "saved-plan source artifact binding"
        )
        if (
            any(report.get(key) != value for key, value in exact.items())
            or categories
            != {
                "Elementary flows/emission/air": 54,
                "Elementary flows/emission/ground": 12,
                "Elementary flows/emission/water": 34,
            }
            or not isinstance(source.get("path"), str)
            or not source["path"].endswith(
                "raw-public-responses/026-45ef7d06-b57a-36cd-a6ce-37d5ded64f60.json"
            )
            or source.get("sha256")
            != "268c0977394b659f7abc609807b1ae357c4c32fbf6627326f2ecaa9e00cc13ed"
            or source.get("bytes") != 1428215
            or report_sha256
            != "daa39b6e86735efdfdf113c246140446a83fc1d8a6d805e27f918d9f25e4ed73"
        ):
            raise ValueError("accepted batch-12 saved-plan authority drifted")
    elif batch_index == 13:
        exact = {
            "batch_key": "4d83c19550a6a282",
            "input_version": "13ad58cf0b5df066",
            "saved_plan_set_version": "9f872eb1c10cbad6",
            "source_bulk_support_logic_version": "2026-07-18.5",
            "source_bulk_resolution_version": "66cfe44ebf186a31",
            "source_bulk_support_report_sha256": (
                "5cf58e1e"
                "0c68ebb4"
                "348262e3"
                "1c9ef883"
                "2b0d872d"
                "beacd05c"
                "8c629c0e"
                "930ae690"
            ),
            "source_authoring_plan_version": "44d477987db9e8fe",
            "source_support_resolution_version": "4575e0df62e45cfa",
            "mass_support_contract_sha256": (
                "a6b8e90c"
                "308fe840"
                "2f96dbb4"
                "0ac65629"
                "e6ed3968"
                "6b35bae9"
                "52392031"
                "06a957dc"
            ),
            "source_route_authority_version": "1170c3f1be7ed8ce",
            "source_route_authority_report_sha256": (
                "0d7c3419"
                "d2f7310c"
                "88020be9"
                "257e84d8"
                "0abcaa42"
                "9a779926"
                "fe066db0"
                "0d1bd1db"
            ),
        }
        source = _object(
            report.get("source_artifact_binding"), "saved-plan source artifact binding"
        )
        if (
            any(report.get(key) != value for key, value in exact.items())
            or categories
            != {
                "Elementary flows/emission/air": 59,
                "Elementary flows/emission/ground": 13,
                "Elementary flows/emission/water": 28,
            }
            or not isinstance(source.get("path"), str)
            or not source["path"].endswith(
                "raw-public-responses/026-45ef7d06-b57a-36cd-a6ce-37d5ded64f60.json"
            )
            or source.get("sha256")
            != "268c0977394b659f7abc609807b1ae357c4c32fbf6627326f2ecaa9e00cc13ed"
            or source.get("bytes") != 1428215
            or report_sha256
            != "cfd5d211e3c92151dcfb49ca71d7fd300e4db172cfc4844aa18dfa727f3aa996"
        ):
            raise ValueError("accepted batch-13 saved-plan authority drifted")
    elif batch_index == 14:
        exact = {
            "batch_key": "3d0478a9d71bfc30",
            "input_version": "5fed25f62bee0917",
            "saved_plan_set_version": "624a32d07a2c8ed6",
            "source_bulk_support_logic_version": "2026-07-18.5",
            "source_bulk_resolution_version": "4487c6e7113b3346",
            "source_bulk_support_report_sha256": (
                "1aa9bfa0"
                "58362359"
                "d0c3d259"
                "d31de74a"
                "6f626c19"
                "8ee1a7bb"
                "15a1b4be"
                "82396cf7"
            ),
            "source_authoring_plan_version": "44d477987db9e8fe",
            "source_support_resolution_version": "4575e0df62e45cfa",
            "mass_support_contract_sha256": (
                "a6b8e90c"
                "308fe840"
                "2f96dbb4"
                "0ac65629"
                "e6ed3968"
                "6b35bae9"
                "52392031"
                "06a957dc"
            ),
            "source_route_authority_version": "988668c8c56e7400",
            "source_route_authority_report_sha256": (
                "2a3240ba"
                "b1b88d30"
                "2e43e154"
                "3fd8d4c3"
                "ab8f711e"
                "f09dab4b"
                "b1c94624"
                "1ae376c3"
            ),
        }
        source = _object(
            report.get("source_artifact_binding"), "saved-plan source artifact binding"
        )
        if (
            any(report.get(key) != value for key, value in exact.items())
            or categories
            != {
                "Elementary flows/emission/air": 55,
                "Elementary flows/emission/ground": 9,
                "Elementary flows/emission/water": 36,
            }
            or not isinstance(source.get("path"), str)
            or not source["path"].endswith(
                "raw-public-responses/026-45ef7d06-b57a-36cd-a6ce-37d5ded64f60.json"
            )
            or source.get("sha256")
            != "268c0977394b659f7abc609807b1ae357c4c32fbf6627326f2ecaa9e00cc13ed"
            or source.get("bytes") != 1428215
            or report_sha256
            != "72fe69a8896773e031364344cf9822bbc129eb7d9c2de43fa73ad45c81ce6ae8"
        ):
            raise ValueError("accepted batch-14 saved-plan authority drifted")
    elif batch_index == 15:
        exact = {
            "batch_key": "42c33c64a4a06c05",
            "input_version": "ed20693a595d1723",
            "saved_plan_set_version": "534c3aa6dcc0e7b6",
            "source_bulk_support_logic_version": "2026-07-18.6",
            "source_bulk_resolution_version": "5c2d82a6fe8f1d25",
            "source_bulk_support_report_sha256": (
                "f960ff12"
                "6506bccd"
                "82cea2e5"
                "f4cdda7d"
                "b867f2fc"
                "22cd7e8e"
                "8d03e1c6"
                "b9859140"
            ),
            "source_authoring_plan_version": "44d477987db9e8fe",
            "source_support_resolution_version": "4575e0df62e45cfa",
            "mass_support_contract_sha256": (
                "a6b8e90c"
                "308fe840"
                "2f96dbb4"
                "0ac65629"
                "e6ed3968"
                "6b35bae9"
                "52392031"
                "06a957dc"
            ),
            "source_route_authority_version": "0b80309731f4b0b4",
            "source_route_authority_report_sha256": (
                "2232bf0c"
                "b539314e"
                "62004d53"
                "7a3ff824"
                "5ebe46a3"
                "4fdbe1e8"
                "3222c9bf"
                "92acb2cf"
            ),
            "represented_support_contract_count": 3,
            "low_priority_review_marker_count": 6,
            "medium_priority_review_marker_count": 4,
        }
        source = _object(
            report.get("source_artifact_binding"), "saved-plan source artifact binding"
        )
        if (
            any(report.get(key) != value for key, value in exact.items())
            or categories
            != {
                "Elementary flows/emission/air": 48,
                "Elementary flows/emission/ground": 6,
                "Elementary flows/emission/water": 41,
                "Elementary flows/resource/ground": 1,
                "Elementary flows/resource/ground/human-dominated": 1,
                "Elementary flows/resource/ground/human-dominated/agricultural": 1,
                "Elementary flows/resource/ground/human-dominated/industrial": 1,
                "Elementary flows/resource/ground/terrestrial/grassland": 1,
            }
            or report.get("flow_property_counts")
            != {"Area*Time|m2*a": 4, "Energy|MJ": 1, "Mass|kg": 95}
            or review_count != 10
            or not isinstance(source.get("path"), str)
            or not source["path"].endswith(
                "raw-public-responses/026-45ef7d06-b57a-36cd-a6ce-37d5ded64f60.json"
            )
            or source.get("sha256")
            != "268c0977394b659f7abc609807b1ae357c4c32fbf6627326f2ecaa9e00cc13ed"
            or source.get("bytes") != 1428215
            or report_sha256
            != "a4042c6990a54932d63ee8d5437124ea2dbc616dede8533d0487e1de5c35922e"
        ):
            raise ValueError("accepted batch-15 saved-plan authority drifted")
    elif batch_index == 16:
        exact = {
            "batch_key": "1388a23ccbf90529",
            "input_version": "abc7ed216aedfca0",
            "saved_plan_set_version": "88becab5ea06c78a",
            "source_bulk_support_logic_version": "2026-07-18.7",
            "source_bulk_resolution_version": "2bbadf526faae1d3",
            "source_bulk_support_report_sha256": (
                "9486eda4"
                "1ea78283"
                "bb8d93d5"
                "cedaf120"
                "55e308bf"
                "94e6eeb5"
                "01dd1e3d"
                "0f4a246e"
            ),
            "source_authoring_plan_version": "44d477987db9e8fe",
            "source_support_resolution_version": "4575e0df62e45cfa",
            "mass_support_contract_sha256": (
                "a6b8e90c"
                "308fe840"
                "2f96dbb4"
                "0ac65629"
                "e6ed3968"
                "6b35bae9"
                "52392031"
                "06a957dc"
            ),
            "source_route_authority_version": "4c35d988ad94df48",
            "source_route_authority_report_sha256": (
                "38ac5ef1"
                "311a48a5"
                "a886d430"
                "e67630be"
                "262a189c"
                "23ac3a3c"
                "6e1eeacb"
                "58a479ec"
            ),
            "represented_support_contract_count": 2,
            "low_priority_review_marker_count": 0,
            "medium_priority_review_marker_count": 49,
        }
        source = _object(
            report.get("source_artifact_binding"), "saved-plan source artifact binding"
        )
        if (
            any(report.get(key) != value for key, value in exact.items())
            or categories
            != {
                "Elementary flows/resource/air": 3,
                "Elementary flows/resource/biotic": 1,
                "Elementary flows/resource/ground": 43,
                "Elementary flows/resource/water": 4,
            }
            or report.get("flow_property_counts")
            != {"Energy|MJ": 6, "Mass|kg": 45}
            or report.get("classification_code_counts")
            != {
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
            or review_count != 49
            or not isinstance(source.get("path"), str)
            or not source["path"].endswith(
                "raw-public-responses/008-485a2faf-7d1c-3f3b-819a-804141bd6ee6.json"
            )
            or source.get("sha256")
            != "04f7ae10990ec49bb7f0f3fa5f92d0b4294bbbd43495abc9b3a2e7743fbb48e3"
            or source.get("bytes") != 1323034
            or report_sha256
            != "1cfcc0ae9cef919c59fea831f3fb3cd1583cc3ecb53fe2a509a92b0074da0fd0"
        ):
            raise ValueError("accepted batch-16 saved-plan authority drifted")
    return {
        "batch_index": batch_index,
        "batch_key": _text(report.get("batch_key"), "saved-plan batch key"),
        "plan_count": plan_count,
        "review_count": review_count,
        "logic_version": expected_logic,
        "expected_route": expected_route,
        "next_route": next_route,
    }


def input_version_for_bindings(
    bindings: Sequence[Mapping[str, object]],
) -> str:
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
                "bytes": _integer(row.get("bytes"), "input binding bytes"),
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


def _safe_path(root: Path, relative_value: object, label: str) -> Path:
    relative = _text(relative_value, label)
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
        or str(pure) != relative
    ):
        raise ValueError(f"{label} is unsafe")
    resolved_root = root.resolve()
    path = (resolved_root / relative).resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"{label} escaped artifact root") from error
    return path


def _runtime_identity(
    report_value: Mapping[str, object], manifest_value: Mapping[str, object]
) -> JsonObject:
    report = _object(report_value, "runtime report")
    manifest = _object(manifest_value, "runtime manifest")
    if (
        report.get("status") != "portable_native_tool_runtime_closure_verified"
        or report.get("runtime_content_closed") is not True
    ):
        raise ValueError("copied native runtime is not verified and content-closed")
    tree = _digest(report.get("tree_sha256"), "runtime tree SHA-256")
    if manifest.get("tree_sha256") != tree:
        raise ValueError("runtime report and manifest disagree")
    cli = _object(manifest.get("tiangong_cli"), "TianGong CLI identity")
    if cli.get("package") != "@tiangong-lca/cli" or cli.get("version") != "0.0.25":
        raise ValueError("unsupported copied TianGong CLI")
    return {
        "runtime_tree_sha256": tree,
        "tiangong_cli_package": cli["package"],
        "tiangong_cli_version": cli["version"],
    }


def _verify_typed_qa_calibration(
    report: Mapping[str, object],
    applicability_decisions: Sequence[Mapping[str, object]],
    product_control: Mapping[str, object],
    *,
    runtime_tree_sha256: str,
    applicability_binding: Mapping[str, object],
    product_binding: Mapping[str, object],
) -> JsonObject:
    value = _object(report, "typed-QA calibration report")
    applicability = [
        _object(row, "typed-QA applicability decision")
        for row in applicability_decisions
    ]
    product = _object(product_control, "typed-QA Product control")
    bindings = _object(value.get("artifact_bindings"), "typed-QA artifact bindings")
    if (
        value.get("contract") != TYPED_QA_CONTRACT
        or value.get("status") != "uslci_elementary_flow_native_qa_harness_resolved"
        or value.get("logic_version") != "2026-07-18.1"
        or value.get("policy_version") != "2026-07-18.1"
        or value.get("typed_qa_pass_count") != 5
        or value.get("strict_product_qa_pass_count") != 1
        or value.get("elementary_not_applicable_count") != 4
        or value.get("unexpected_finding_count") != 0
        or value.get("bulk_planning_release_authorized") is not True
        or value.get("bulk_entity_execution_authorized") is not False
        or _object(value.get("runtime_identity"), "typed-QA runtime").get(
            "runtime_tree_sha256"
        )
        != runtime_tree_sha256
        or len(applicability) != 4
    ):
        raise ValueError("typed-QA calibration authority drifted")
    for name, supplied in (
        ("applicability_decisions", applicability_binding),
        ("product_control", product_binding),
    ):
        declared = _object(bindings.get(name), f"typed-QA {name} binding")
        if (
            declared.get("sha256") != supplied.get("sha256")
            or declared.get("bytes") != supplied.get("bytes")
        ):
            raise ValueError(f"typed-QA calibration artifact drifted: {name}")
    if any(
        row.get("flow_type") != "Elementary flow"
        or row.get("native_schema_validation") != "passed"
        or row.get("outcome")
        != "not_applicable_to_intentional_elementary_authoring_batch"
        or row.get("exception_applied") is not True
        or row.get("native_rule_id") != "elementary_flow_in_flow_qa"
        or row.get("methodology_rule_id") != "tidas.flow.type.required"
        or row.get("native_finding_preserved") is not True
        or row.get("scientific_mapping_invalidated") is not False
        for row in applicability
    ):
        raise ValueError("typed-QA Elementary calibration decision drifted")
    if (
        product.get("flow_type") != "Product flow"
        or product.get("native_schema_validation") != "passed"
        or product.get("outcome") != "strict_native_pass"
        or product.get("exception_applied") is not False
    ):
        raise ValueError("typed-QA strict Product control drifted")
    return {
        "contract": value.get("contract"),
        "policy_version": value.get("policy_version"),
        "calibration_input_version": value.get("input_version"),
        "elementary_calibration_count": 4,
        "strict_product_control_count": 1,
        "runtime_tree_sha256": runtime_tree_sha256,
    }


def evaluate_elementary_typed_qa(
    report_value: Mapping[str, object],
    *,
    target_flow_id: str,
    batch_index: int,
    batch_position: int,
    authoring_intent_key: str,
    schema_valid: bool,
    semantic_assertions_valid: bool,
) -> JsonObject:
    """Fail closed unless one native finding exactly matches the calibrated gap."""

    def reject(reason: str) -> None:
        raise ValueError(f"typed QA policy rejected: {reason}")

    if (
        batch_index < 2
        or batch_position < 1
        or batch_position > MAX_PLAN_COUNT
        or not target_flow_id
        or not authoring_intent_key
        or schema_valid is not True
        or semantic_assertions_valid is not True
    ):
        reject("batch identity or prerequisite assertions drifted")
    report = _object(report_value, "native Flow QA report")
    gate = _object(report.get("ruleset_gate"), "native Flow QA ruleset gate")
    if any(report.get(key) != expected for key, expected in EXPECTED_RULESET.items()):
        reject("ruleset identity drifted")
    if any(gate.get(key) != expected for key, expected in EXPECTED_RULESET.items()):
        reject("ruleset gate identity drifted")
    if (
        report.get("status") != "completed_local_flow_qa"
        or report.get("logic_version") != "flow-v1.0-cli"
        or report.get("ruleset_rule_ids") != EXPECTED_RULE_IDS
        or report.get("flow_count") != 1
        or report.get("rule_finding_count") != 1
        or report.get("llm_finding_count") != 0
        or report.get("finding_count") != 1
        or report.get("blocker_count") != 1
        or report.get("severity_counts") != {"warning": 1}
        or report.get("rule_counts") != {"elementary_flow_in_flow_qa": 1}
        or report.get("methodology_rule_counts")
        != {"tidas.flow.type.required": 1}
        or gate.get("status") != "blocked"
        or gate.get("ruleset_rule_ids") != EXPECTED_RULE_IDS
        or gate.get("counts") != {"findings": 1, "blockers": 1}
    ):
        reject("native QA aggregate fingerprint drifted")
    llm = _object(report.get("llm"), "native Flow QA LLM status")
    if llm.get("enabled") is not False or llm.get("reviewed_flow_count") != 0:
        reject("LLM finding path was not disabled")
    blockers = [
        _object(value, "native Flow QA blocker")
        for value in _array(gate.get("blockers"), "native Flow QA blockers")
    ]
    if len(blockers) != 1:
        reject("native QA blocker cardinality drifted")
    blocker = blockers[0]
    expected = {
        "flow_uuid": target_flow_id,
        "base_version": "00.00.001",
        "severity": "warning",
        "rule_id": "elementary_flow_in_flow_qa",
        "ruleset_id": "flow-authoring/strict",
        "methodology_rule_id": "tidas.flow.type.required",
        "message": KNOWN_MESSAGE,
        "fixability": "review-needed",
        "source": "rule",
        "evidence": {"typeOfDataSet": "Elementary flow"},
    }
    if blocker != expected:
        reject("native Elementary finding fingerprint drifted")
    return {
        "schema_version": 1,
        "batch_index": batch_index,
        "batch_position": batch_position,
        "authoring_intent_key": authoring_intent_key,
        "target_flow_id": target_flow_id,
        "flow_type": "Elementary flow",
        "native_schema_validation": "passed",
        "upstream_native_qa_status": "blocked",
        "outcome": "not_applicable_to_intentional_elementary_authoring_batch",
        "exception_applied": True,
        "native_rule_id": "elementary_flow_in_flow_qa",
        "methodology_rule_id": "tidas.flow.type.required",
        "native_finding_preserved": True,
        "scientific_mapping_invalidated": False,
        "entity_creation_authorized": False,
    }


def build_terminal_dag(
    branch_rows: Sequence[Mapping[str, object]], *, expected_plan_count: int = 100
) -> JsonObject:
    ordered = sorted(
        (_object(value, "branch terminal") for value in branch_rows),
        key=lambda row: _integer(row.get("batch_position"), "batch position"),
    )
    positions = [int(row["batch_position"]) for row in ordered]
    if (
        isinstance(expected_plan_count, bool)
        or not isinstance(expected_plan_count, int)
        or expected_plan_count < 1
        or expected_plan_count > MAX_PLAN_COUNT
    ):
        raise ValueError("expected terminal cardinality is outside the bulk limit")
    plan_count = expected_plan_count
    if len(ordered) != plan_count:
        raise ValueError(
            f"branch terminal positions are not contiguous 1..{plan_count}"
        )
    if positions != list(range(1, plan_count + 1)):
        raise ValueError(
            f"branch terminal positions are not contiguous 1..{plan_count}"
        )
    batch_indices = {
        _integer(row.get("batch_index", 2), "branch terminal batch index")
        for row in ordered
    }
    if len(batch_indices) != 1:
        raise ValueError("branch terminals span multiple batches")
    batch_index = batch_indices.pop()
    if batch_index < 2:
        raise ValueError("branch terminal batch index is outside the continuation")
    terminal_id = f"batch-{batch_index}-native-analysis-gate"
    nodes = []
    edges = []
    successful = 0
    failed = 0
    for row in ordered:
        position = int(row["batch_position"])
        intent_key = _text(row.get("authoring_intent_key"), "authoring intent key")
        status = row.get("terminal_status")
        if status == "succeeded":
            successful += 1
        elif status == "failed":
            failed += 1
        else:
            raise ValueError("branch terminal status is unsupported")
        compile_id = f"compile-batch-{batch_index}-flow-{position:03d}"
        gate_id = f"native-gates-batch-{batch_index}-flow-{position:03d}"
        nodes.extend(
            [
                {
                    "node_id": compile_id,
                    "phase": "saved_plan",
                    "status": "completed",
                    "authoring_intent_key": intent_key,
                },
                {
                    "node_id": gate_id,
                    "phase": "isolated_local_native_gates",
                    "status": "completed" if status == "succeeded" else "failed",
                    "authoring_intent_key": intent_key,
                },
            ]
        )
        edges.append({"from": compile_id, "to": gate_id})
        edges.append({"from": gate_id, "to": terminal_id})
    analysis_status = "completed" if failed == 0 else "blocked"
    nodes.append(
        {
            "node_id": terminal_id,
            "phase": "batch_native_analysis",
            "status": analysis_status,
            "batch_entity_release_authorized": False,
        }
    )
    return {
        "schema_version": 1,
        "batch_index": batch_index,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "successful_branch_count": successful,
        "failed_branch_count": failed,
        "analysis_gate_status": analysis_status,
        "batch_entity_release_authorized": False,
        "nodes": nodes,
        "edges": edges,
    }


def _run_command(command: Sequence[str], *, stdout_path: Path) -> None:
    allowed = {
        ("flow", "build-plan", "validate"),
        ("flow", "build-plan", "materialize"),
        ("dataset", "classification", "apply"),
        ("dataset", "validate"),
        ("qa", "flow"),
    }
    operation = tuple(command[2:5])
    if not any(operation[: len(prefix)] == prefix for prefix in allowed):
        raise ValueError("copied TianGong command is outside the local native allowlist")
    if operation[:3] in {
        ("flow", "build-plan", "validate"),
        ("flow", "build-plan", "materialize"),
    } and "--report-only" not in command:
        raise ValueError("copied TianGong build-plan command must remain report-only")
    sensitive = (
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "CREDENTIAL",
        "API_KEY",
        "ACCESS_KEY",
        "PRIVATE_KEY",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(fragment in key.upper() for fragment in sensitive)
    }
    environment.update(
        {
            "TIANGONG_NETWORK_MODE": "offline",
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
    )
    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
        env=environment,
    )
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_bytes(completed.stdout)
    if completed.stderr:
        stdout_path.with_suffix(".stderr.txt").write_bytes(completed.stderr)
    if completed.returncode != 0:
        raise ValueError(
            "copied TianGong native command failed: "
            + completed.stderr.decode("utf-8", errors="replace").strip()
        )


def _read_json_file(path: Path, label: str) -> JsonObject:
    if not path.is_file():
        raise ValueError(f"{label} is missing")
    return _parse_json(path.read_bytes(), label)


def _verify_schema_report(
    report_value: Mapping[str, object], *, target_flow_id: str
) -> None:
    report = _object(report_value, "native FlowSchema report")
    counts = _object(report.get("counts"), "native FlowSchema counts")
    rows = [
        _object(value, "native FlowSchema row")
        for value in _array(report.get("rows"), "native FlowSchema rows")
    ]
    if (
        report.get("status") != "completed"
        or report.get("requested_type") != "flow"
        or counts.get("total") != 1
        or counts.get("valid") != 1
        or counts.get("invalid") != 0
        or len(rows) != 1
    ):
        raise ValueError("native FlowSchema aggregate validation failed")
    row = rows[0]
    if (
        row.get("id") != target_flow_id
        or row.get("version") != "00.00.001"
        or row.get("type") != "flow"
        or row.get("status") != "valid"
        or row.get("validator") != "@tiangong-lca/tidas-sdk/FlowSchema"
        or row.get("issue_count") != 0
        or row.get("issues") != []
    ):
        raise ValueError("native FlowSchema row validation failed")


def _archive_native_runs(
    source_root: Path, archive_path: Path
) -> Tuple[bytes, List[JsonObject]]:
    files = sorted(path for path in source_root.rglob("*") if path.is_file())
    manifest = []
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            relative = path.relative_to(source_root).as_posix()
            content = path.read_bytes()
            info = tarfile.TarInfo(name=relative)
            info.size = len(content)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
            media_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
            manifest.append(
                {
                    "schema_version": 1,
                    "path": relative,
                    "media_type": media_type,
                    "digest": f"sha256:{_sha256(content)}",
                    "sha256": _sha256(content),
                    "bytes": len(content),
                    "size": len(content),
                }
            )
    return archive_path.read_bytes(), manifest


def _execute_branch(
    *,
    row: Mapping[str, object],
    plan_path: Path,
    plan: Mapping[str, object],
    node: Path,
    cli: Path,
    scratch: Path,
) -> JsonObject:
    batch_index = _integer(row.get("batch_index"), "batch index")
    if batch_index < 2:
        raise ValueError("branch batch index is outside the bulk continuation")
    position = _integer(row.get("batch_position"), "batch position")
    intent_key = _text(row.get("authoring_intent_key"), "authoring intent key")
    target = _object(plan.get("target"), "saved plan target")
    target_id = _text(target.get("uuid"), "target Flow ID")
    classification = _object(
        plan.get("native_classification_plan"), "saved plan classification"
    )
    run = scratch / f"branch-{position:03d}"
    validate_dir = run / "plan-validate"
    materialize_dir = run / "materialize"
    classify_dir = run / "classify"
    schema_dir = run / "schema-validate"
    qa_dir = run / "qa"
    stdout_dir = run / "command-stdout"
    native_invocations = 0

    def run_native(command: Sequence[str], stdout_path: Path) -> None:
        nonlocal native_invocations
        native_invocations += 1
        _run_command(command, stdout_path=stdout_path)

    base_terminal = {
        "schema_version": 1,
        "batch_index": batch_index,
        "batch_position": position,
        "authoring_intent_key": intent_key,
        "source_flow_id": row.get("source_flow_id"),
        "target_flow_id": target_id,
        "saved_plan_sha256": row.get("sha256"),
    }
    try:
        run_native(
            [
                str(node),
                str(cli),
                "flow",
                "build-plan",
                "validate",
                "--input",
                str(plan_path),
                "--out-dir",
                str(validate_dir),
                "--report-only",
                "--json",
            ],
            stdout_dir / "01-plan-validate.json",
        )
        validate_report = _read_json_file(
            validate_dir / "outputs/build-plan-gate-report.json",
            f"branch {position} native plan validation report",
        )
        if (
            validate_report.get("status") != "passed"
            or validate_report.get("action") != "validate"
            or validate_report.get("blockers") != []
            or _object(
                validate_report.get("required_fields"), "required fields"
            ).get("missing")
            != []
        ):
            raise ValueError("native saved-plan validation failed")

        run_native(
            [
                str(node),
                str(cli),
                "flow",
                "build-plan",
                "materialize",
                "--input",
                str(plan_path),
                "--out-dir",
                str(materialize_dir),
                "--report-only",
                "--json",
            ],
            stdout_dir / "02-materialize.json",
        )
        materialize_report = _read_json_file(
            materialize_dir / "outputs/build-plan-gate-report.json",
            f"branch {position} native materialize report",
        )
        materialized_path = materialize_dir / "outputs/materialized-flow.json"
        _read_json_file(materialized_path, f"branch {position} materialized Flow")
        if (
            materialize_report.get("status") != "passed"
            or materialize_report.get("action") != "materialize"
            or materialize_report.get("blockers") != []
            or _object(
                materialize_report.get("schema_validation"), "schema validation"
            ).get("status")
            != "passed"
            or _object(
                materialize_report.get("schema_validation"), "schema validation"
            ).get("issue_count")
            != 0
        ):
            raise ValueError("native local materialization failed")

        decision_payload = {
            "row_index": 0,
            "category_type": classification.get("category_type"),
            "code": classification.get("leaf_code"),
            "basis": (
                "Accepted frozen plan classification decision "
                + _text(
                    classification.get("source_decision_id"),
                    "classification decision ID",
                )
            ),
            "evidence": {
                "authoring_intent_key": intent_key,
                "saved_plan_sha256": row.get("sha256"),
                "runtime_tree_sha256": _object(
                    plan.get("generation_manifest"), "generation manifest"
                ).get("runtime_tree_sha256"),
            },
        }
        decision_path = run / "classification-decision.json"
        decision_path.parent.mkdir(parents=True, exist_ok=True)
        decision_path.write_bytes(_json_bytes(decision_payload, pretty=True))
        classified_path = classify_dir / "classified-flow.jsonl"
        run_native(
            [
                str(node),
                str(cli),
                "dataset",
                "classification",
                "apply",
                "--input",
                str(materialized_path),
                "--decisions",
                str(decision_path),
                "--out",
                str(classified_path),
                "--type",
                str(classification.get("category_type")),
                "--out-dir",
                str(classify_dir),
                "--json",
            ],
            stdout_dir / "03-classification-apply.json",
        )
        classification_report = _read_json_file(
            classify_dir / "outputs/classification-apply-report.json",
            f"branch {position} classification report",
        )
        if (
            classification_report.get("status") != "completed"
            or classification_report.get("counts")
            != {"rows": 1, "decisions": 1, "applied": 1, "blockers": 0}
            or classification_report.get("blockers") != []
        ):
            raise ValueError("native exact classification apply failed")
        classified_rows = _parse_jsonl(
            classified_path.read_bytes(), f"branch {position} classified Flow"
        )
        if len(classified_rows) != 1:
            raise ValueError("classified Flow row cardinality drifted")
        semantic = verify_classified_flow(plan, classified_rows[0])
        semantic.update(
            {
                "schema_version": 1,
                "batch_index": batch_index,
                "batch_position": position,
                "authoring_intent_key": intent_key,
                "classified_flow_sha256": _sha256(classified_path.read_bytes()),
            }
        )
        support = {
            "schema_version": 1,
            "batch_index": batch_index,
            "batch_position": position,
            "authoring_intent_key": intent_key,
            "target_flow_id": target_id,
            "flow_property_id": semantic["flow_property_id"],
            "flow_property_version": semantic["flow_property_version"],
            "support_reference_preserved": True,
        }

        run_native(
            [
                str(node),
                str(cli),
                "dataset",
                "validate",
                "--input",
                str(classified_path),
                "--type",
                "flow",
                "--out-dir",
                str(schema_dir),
                "--json",
            ],
            stdout_dir / "04-dataset-validate.json",
        )
        schema_report_path = schema_dir / "outputs/validation-report.json"
        schema_report = _read_json_file(
            schema_report_path, f"branch {position} FlowSchema report"
        )
        _verify_schema_report(schema_report, target_flow_id=target_id)

        run_native(
            [
                str(node),
                str(cli),
                "qa",
                "flow",
                "--rows-file",
                str(classified_path),
                "--out-dir",
                str(qa_dir),
                "--json",
            ],
            stdout_dir / "05-flow-qa.json",
        )
        qa_report_path = qa_dir / "flow_qa_report.json"
        qa_report = _read_json_file(
            qa_report_path, f"branch {position} native Flow QA report"
        )
        typed = evaluate_elementary_typed_qa(
            qa_report,
            target_flow_id=target_id,
            batch_index=batch_index,
            batch_position=position,
            authoring_intent_key=intent_key,
            schema_valid=True,
            semantic_assertions_valid=True,
        )
        typed.update(
            {
                "native_qa_report_sha256": _sha256(qa_report_path.read_bytes()),
                "classified_flow_sha256": semantic["classified_flow_sha256"],
            }
        )
        blocker = _object(
            _array(
                _object(qa_report.get("ruleset_gate"), "QA ruleset gate").get(
                    "blockers"
                ),
                "QA blockers",
            )[0],
            "QA blocker",
        )
        native_finding = {
            "schema_version": 1,
            "batch_index": batch_index,
            "batch_position": position,
            "authoring_intent_key": intent_key,
            "target_flow_id": target_id,
            "native_qa_report_sha256": typed["native_qa_report_sha256"],
            "finding": blocker,
            "finding_preserved": True,
            "typed_applicability_outcome": typed["outcome"],
        }
        terminal = {
            **base_terminal,
            "terminal_status": "succeeded",
            "native_invocation_count": native_invocations,
            "native_plan_validation": "passed",
            "local_schema_materialization": "passed",
            "exact_classification_apply": "passed",
            "support_reference_preservation": "passed",
            "native_flow_schema_validation": "passed",
            "native_flow_qa": "blocked_with_exact_elementary_applicability_finding",
            "typed_flow_qa": "passed_with_scoped_not_applicable_finding",
            "save_draft_executed": False,
            "remote_entity_created": False,
            "batch_entity_release_authorized": False,
        }
        return {
            "terminal": terminal,
            "classification_assertion": semantic,
            "support_assertion": support,
            "native_finding": native_finding,
            "typed_decision": typed,
            "failure": None,
        }
    except Exception as error:  # branch failures must not hide later branch outcomes
        return {
            "terminal": {
                **base_terminal,
                "terminal_status": "failed",
                "native_invocation_count": native_invocations,
                "failure_type": type(error).__name__,
                "failure_message": str(error),
                "save_draft_executed": False,
                "remote_entity_created": False,
                "batch_entity_release_authorized": False,
            },
            "classification_assertion": None,
            "support_assertion": None,
            "native_finding": None,
            "typed_decision": None,
            "failure": {
                **base_terminal,
                "failure_type": type(error).__name__,
                "failure_message": str(error),
            },
        }


def execute_and_materialize(
    *,
    contents: Mapping[str, bytes],
    bindings: Mapping[str, Mapping[str, object]],
    artifact_root: Path,
    runtime_root: Path,
    out_dir: Path,
) -> JsonObject:
    missing = [
        name
        for name in EXPECTED_INPUT_NAMES
        if name not in contents or name not in bindings
    ]
    if missing:
        raise ValueError(f"bulk native-gate inputs are incomplete: {missing}")
    verified_bindings = [
        _binding(bindings[name], contents[name], f"{name} binding")
        for name in EXPECTED_INPUT_NAMES
    ]
    verified_by_name = dict(zip(EXPECTED_INPUT_NAMES, verified_bindings))
    input_version = input_version_for_bindings(verified_bindings)
    saved_report = _parse_json(contents["saved_plan_report"], "saved-plan report")
    authority = validate_saved_plan_authority(
        saved_report,
        report_sha256=verified_by_name["saved_plan_report"]["sha256"],
    )
    batch_index = int(authority["batch_index"])
    batch_key = str(authority["batch_key"])
    plan_count = int(authority["plan_count"])
    review_count = int(authority["review_count"])
    expected_saved_logic = str(authority["logic_version"])
    expected_saved_status = (
        f"uslci_flow_authoring_bulk_batch_{batch_index}_saved_plans_compiled"
    )
    expected_saved_route = str(authority["expected_route"])
    next_route = str(authority["next_route"])
    plan_index = _parse_json(contents["plan_index"], "saved-plan index")
    descriptors = _parse_jsonl(
        contents["execution_descriptors"], "execution descriptors"
    )
    reviews = _parse_jsonl(
        contents["review_marker_carry_forward"], "review marker carry-forward"
    )
    plan_manifest = _parse_json(
        contents["saved_plan_manifest"], "saved-plan execution manifest"
    )
    typed_report = _parse_json(contents["typed_qa_report"], "typed-QA report")
    applicability = _parse_jsonl(
        contents["applicability_decisions"], "typed-QA applicability decisions"
    )
    product_control = _parse_json(contents["product_control"], "Product QA control")
    runtime_report = _parse_json(contents["runtime_report"], "runtime report")
    runtime_manifest = _parse_json(contents["runtime_manifest"], "runtime manifest")
    runtime = _runtime_identity(runtime_report, runtime_manifest)
    if (
        saved_report.get("contract") != SAVED_PLAN_CONTRACT
        or saved_report.get("logic_version") != expected_saved_logic
        or saved_report.get("policy_version") != "2026-07-18.1"
        or saved_report.get("status") != expected_saved_status
        or saved_report.get("content_descriptor_count") != plan_count
        or saved_report.get("complete_plan_count") != plan_count
        or saved_report.get("planning_failure_count") != 0
        or saved_report.get("batch_entity_release_authorized") is not False
        or _object(saved_report.get("routing"), "saved-plan routing").get(
            "next_internal_route"
        )
        != expected_saved_route
        or len(descriptors) != plan_count
        or len(reviews) != review_count
    ):
        raise ValueError(
            f"accepted batch-{batch_index} saved-plan authority drifted"
        )
    for name in (
        "plan_index",
        "execution_descriptors",
        "review_marker_carry_forward",
    ):
        declared = _object(
            _object(saved_report.get("artifact_bindings"), "saved-plan bindings").get(
                name
            ),
            f"saved-plan {name} binding",
        )
        supplied = verified_by_name[name]
        if (
            declared.get("sha256") != supplied.get("sha256")
            or declared.get("bytes") != supplied.get("bytes")
        ):
            raise ValueError(f"accepted saved-plan artifact drifted: {name}")
    runtime_tree = str(runtime["runtime_tree_sha256"])
    if (
        _object(saved_report.get("runtime_identity"), "saved-plan runtime").get(
            "runtime_tree_sha256"
        )
        != runtime_tree
        or plan_index.get("runtime_tree_sha256") != runtime_tree
        or plan_index.get("plan_set_version")
        != saved_report.get("saved_plan_set_version")
        or plan_index.get("plan_media_type") != PLAN_MEDIA_TYPE
        or plan_index.get("apply_requires_exact_plan_bytes") is not True
    ):
        raise ValueError("saved-plan and runtime identities disagree")
    calibration = _verify_typed_qa_calibration(
        typed_report,
        applicability,
        product_control,
        runtime_tree_sha256=runtime_tree,
        applicability_binding=verified_by_name["applicability_decisions"],
        product_binding=verified_by_name["product_control"],
    )

    index_rows = [
        _object(value, "saved-plan index row")
        for value in _array(
            plan_index.get("ordered_flow_plans"), "saved-plan index rows"
        )
    ]
    manifest_rows = [
        _object(value, "saved-plan manifest row")
        for value in _array(plan_manifest.get("plans"), "saved-plan manifest rows")
    ]
    if (
        plan_manifest.get("contract")
        != "lca_research_kernel.uslci_flow_bulk_saved_plan_execution_manifest/v1"
        or plan_manifest.get("batch_index") != batch_index
        or plan_manifest.get("batch_key") != batch_key
        or plan_manifest.get("plan_count") != plan_count
        or plan_manifest.get("saved_plan_set_version")
        != saved_report.get("saved_plan_set_version")
        or plan_manifest.get("runtime_tree_sha256") != runtime_tree
        or len(index_rows) != plan_count
        or len(manifest_rows) != plan_count
    ):
        raise ValueError("saved-plan execution manifest drifted")

    def by_position(values: Sequence[Mapping[str, object]], label: str) -> Dict[int, JsonObject]:
        result = {}
        for value in values:
            row = _object(value, label)
            position = _integer(row.get("batch_position"), f"{label} position")
            if position in result:
                raise ValueError(f"{label} position is duplicated")
            result[position] = row
        if sorted(result) != list(range(1, plan_count + 1)):
            raise ValueError(
                f"{label} positions are not contiguous 1..{plan_count}"
            )
        return result

    index_by_position = by_position(index_rows, "saved-plan index row")
    descriptor_by_position = by_position(descriptors, "execution descriptor")
    manifest_by_position = by_position(manifest_rows, "saved-plan manifest row")
    saved_bindings = [
        _object(value, "saved-plan report descriptor")
        for value in _array(
            saved_report.get("saved_plan_bindings"), "saved-plan report descriptors"
        )
    ]
    report_by_position = by_position(saved_bindings, "saved-plan report descriptor")
    root = artifact_root.resolve()
    plans = []
    for position in range(1, plan_count + 1):
        index_row = index_by_position[position]
        descriptor = descriptor_by_position[position]
        manifest_row = manifest_by_position[position]
        report_row = report_by_position[position]
        identity_keys = (
            "batch_index",
            "batch_position",
            "authoring_intent_key",
            "source_flow_id",
            "source_position",
            "target_flow_id",
        )
        if any(
            index_row.get(key) != descriptor.get(key)
            or index_row.get(key) != manifest_row.get(key)
            or index_row.get(key) != report_row.get(key)
            for key in identity_keys
        ):
            raise ValueError(f"saved-plan identity ledgers disagree at {position}")
        if (
            descriptor.get("status") != "saved_plan_compiled_pending_native_gates"
            or descriptor.get("entity_creation_authorized") is not False
            or manifest_row.get("original_plan_path") != index_row.get("path")
            or index_row.get("path") != report_row.get("path")
            or index_row.get("media_type") != PLAN_MEDIA_TYPE
            or index_row.get("digest") != f"sha256:{index_row.get('sha256')}"
            or index_row.get("size") != index_row.get("bytes")
        ):
            raise ValueError(f"saved-plan descriptor contract drifted at {position}")
        for row in (manifest_row, report_row):
            if (
                row.get("sha256") != index_row.get("sha256")
                or row.get("bytes") != index_row.get("bytes")
            ):
                raise ValueError(f"saved-plan content descriptor drifted at {position}")
        plan_path = _safe_path(
            root,
            manifest_row.get("workspace_path"),
            f"saved plan {position} workspace path",
        )
        if not plan_path.is_file():
            raise ValueError(f"saved plan {position} is missing")
        content = plan_path.read_bytes()
        if (
            _sha256(content) != index_row.get("sha256")
            or len(content) != index_row.get("bytes")
        ):
            raise ValueError(f"saved plan {position} exact bytes drifted")
        plan = _parse_json(content, f"saved plan {position}")
        generation = _object(plan.get("generation_manifest"), "generation manifest")
        target = _object(plan.get("target"), "saved plan target")
        classification = _object(
            plan.get("native_classification_plan"), "saved plan classification"
        )
        support = _object(plan.get("flow_property_plan"), "saved plan support")
        if (
            generation.get("batch_index") != batch_index
            or generation.get("batch_position") != position
            or generation.get("authoring_intent_key")
            != index_row.get("authoring_intent_key")
            or generation.get("source_flow_id") != index_row.get("source_flow_id")
            or generation.get("target_flow_id") != index_row.get("target_flow_id")
            or generation.get("runtime_tree_sha256") != runtime_tree
            or target.get("uuid") != index_row.get("target_flow_id")
            or target.get("flow_type") != "Elementary flow"
            or target.get("version") != "00.00.001"
            or classification.get("category_type") != "flow-elementary"
            or descriptor.get("classification_code") != classification.get("leaf_code")
            or support.get("reference_property_id") != descriptor.get("flow_property_id")
            or support.get("reference_unit") != descriptor.get("reference_unit")
        ):
            raise ValueError(f"saved plan semantic descriptor drifted at {position}")
        plans.append((index_row, plan_path, plan))
    review_positions = sorted(
        _integer(row.get("batch_position"), "review marker position") for row in reviews
    )
    descriptor_review_positions = sorted(
        int(row["batch_position"])
        for row in descriptors
        if row.get("review_marker_count") == 1
    )
    if (
        review_positions != descriptor_review_positions
        or len(review_positions) != review_count
        or any(
            row.get("native_gate_review_retained") is not True
            or row.get("entity_creation_blocked") is not True
            for row in reviews
        )
    ):
        raise ValueError("saved-plan review marker coverage drifted")

    node = runtime_root / "bin/node"
    cli = runtime_root / CLI_PATH
    if not node.is_file() or not cli.is_file():
        raise ValueError("copied runtime executable is missing")
    outputs = out_dir / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    branch_payloads = []
    active = 0
    peak = 0
    lock = threading.Lock()
    with tempfile.TemporaryDirectory(prefix="uslci-flow-bulk-native-gates-") as temp:
        scratch = Path(temp)

        def tracked_execute(item: Tuple[JsonObject, Path, JsonObject]) -> JsonObject:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                row, plan_path, plan = item
                return _execute_branch(
                    row=row,
                    plan_path=plan_path,
                    plan=plan,
                    node=node,
                    cli=cli,
                    scratch=scratch,
                )
            finally:
                with lock:
                    active -= 1

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(tracked_execute, item) for item in plans]
            for future in as_completed(futures):
                branch_payloads.append(future.result())
        branch_payloads.sort(
            key=lambda value: int(value["terminal"]["batch_position"])
        )
        archive_path = (
            outputs
            / f"uslci-flow-authoring-bulk-batch-{batch_index}-native-raw.tar"
        )
        archive_bytes, raw_manifest = _archive_native_runs(scratch, archive_path)

    terminals = [dict(value["terminal"]) for value in branch_payloads]
    classifications = [
        dict(value["classification_assertion"])
        for value in branch_payloads
        if isinstance(value.get("classification_assertion"), dict)
    ]
    supports = [
        dict(value["support_assertion"])
        for value in branch_payloads
        if isinstance(value.get("support_assertion"), dict)
    ]
    findings = [
        dict(value["native_finding"])
        for value in branch_payloads
        if isinstance(value.get("native_finding"), dict)
    ]
    typed_decisions = [
        dict(value["typed_decision"])
        for value in branch_payloads
        if isinstance(value.get("typed_decision"), dict)
    ]
    failures = [
        dict(value["failure"])
        for value in branch_payloads
        if isinstance(value.get("failure"), dict)
    ]
    preserved_reviews = [
        {
            **row,
            "native_gate_execution_completed": not failures,
            "native_gate_review_retained": True,
            "entity_creation_blocked": True,
        }
        for row in reviews
    ]
    dag = build_terminal_dag(terminals, expected_plan_count=plan_count)
    output_prefix = f"uslci-flow-authoring-bulk-batch-{batch_index}-native"
    output_rows = {
        "branch_terminals": (
            f"{output_prefix}-branch-terminals.jsonl",
            _jsonl_bytes(terminals),
        ),
        "classification_assertions": (
            f"{output_prefix}-classification-assertions.jsonl",
            _jsonl_bytes(classifications),
        ),
        "support_assertions": (
            f"{output_prefix}-support-assertions.jsonl",
            _jsonl_bytes(supports),
        ),
        "native_qa_findings": (
            f"{output_prefix}-qa-findings.jsonl",
            _jsonl_bytes(findings),
        ),
        "typed_qa_decisions": (
            f"uslci-flow-authoring-bulk-batch-{batch_index}-typed-qa-decisions.jsonl",
            _jsonl_bytes(typed_decisions),
        ),
        "review_marker_carry_forward": (
            f"{output_prefix}-review-markers.jsonl",
            _jsonl_bytes(preserved_reviews),
        ),
        "unexpected_failures": (
            f"{output_prefix}-unexpected-failures.jsonl",
            _jsonl_bytes(failures),
        ),
        "terminal_dag": (
            f"{output_prefix}-terminal-dag.json",
            _json_bytes(dag, pretty=True),
        ),
        "raw_artifact_manifest": (
            f"{output_prefix}-raw-manifest.jsonl",
            _jsonl_bytes(raw_manifest),
        ),
        "raw_artifact_archive": (
            f"{output_prefix}-raw.tar",
            archive_bytes,
        ),
    }
    artifact_bindings = {
        key: _artifact_binding(filename, content)
        for key, (filename, content) in output_rows.items()
    }
    native_invocations = sum(int(row["native_invocation_count"]) for row in terminals)
    execution_version = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "input_version": input_version,
                "branch_terminals_sha256": artifact_bindings["branch_terminals"][
                    "sha256"
                ],
                "typed_qa_decisions_sha256": artifact_bindings[
                    "typed_qa_decisions"
                ]["sha256"],
                "raw_artifact_archive_sha256": artifact_bindings[
                    "raw_artifact_archive"
                ]["sha256"],
            }
        )
    )[:16]
    status = (
        "uslci_flow_authoring_bulk_batch_native_gates_completed"
        if not failures
        else "uslci_flow_authoring_bulk_batch_native_gates_completed_with_failures"
    )
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "policy_version": POLICY_VERSION,
        "status": status,
        "scope_id": saved_report.get("scope_id"),
        "revision": saved_report.get("revision"),
        "batch_index": batch_index,
        "batch_key": batch_key,
        "input_version": input_version,
        "native_gate_execution_version": execution_version,
        "source_saved_plan_set_version": saved_report.get("saved_plan_set_version"),
        "input_bindings": verified_bindings,
        "runtime_identity": runtime,
        "typed_qa_calibration": calibration,
        "artifact_bindings": artifact_bindings,
        "configured_parallel_branch_limit": MAX_WORKERS,
        "peak_concurrent_branch_count": peak,
        "branch_terminal_count": len(terminals),
        "successful_branch_count": dag["successful_branch_count"],
        "failed_branch_count": dag["failed_branch_count"],
        "native_plan_validation_pass_count": len(classifications),
        "local_flow_materialization_pass_count": len(classifications),
        "exact_classification_apply_pass_count": len(classifications),
        "support_reference_preservation_pass_count": len(supports),
        "native_flow_schema_validation_pass_count": len(typed_decisions),
        "native_flow_qa_execution_count": len(findings),
        "native_qa_finding_preserved_count": len(findings),
        "typed_qa_pass_count": len(typed_decisions),
        "elementary_not_applicable_count": len(typed_decisions),
        "review_marker_preserved_count": len(preserved_reviews),
        "unexpected_failure_count": len(failures),
        "native_invocation_executed_count": native_invocations,
        "native_plan_validation_executed_count": len(terminals),
        "local_flow_materialization_executed_count": sum(
            int(row["native_invocation_count"]) >= 2 for row in terminals
        ),
        "exact_classification_apply_executed_count": sum(
            int(row["native_invocation_count"]) >= 3 for row in terminals
        ),
        "native_flow_schema_validation_executed_count": sum(
            int(row["native_invocation_count"]) >= 4 for row in terminals
        ),
        "native_flow_qa_executed_count": sum(
            int(row["native_invocation_count"]) >= 5 for row in terminals
        ),
        "raw_artifact_member_count": len(raw_manifest),
        "terminal_dag_node_count": dag["node_count"],
        "terminal_dag_edge_count": dag["edge_count"],
        "batch_entity_release_authorized": False,
        "save_draft_authorized": False,
        **{key: 0 for key in ZERO_SIDE_EFFECTS},
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "failure_diagnosis": {
            "prompt_failure_count": 0,
            "context_failure_count": 0,
            "harness_failure_count": len(failures),
            "loop_failure_count": 0,
            "model_limitation_count": 0,
        },
        "external_patterns": [
            {
                "project": "kubernetes/kubernetes",
                "stars_observed_2026_07_18": 123790,
                "local_landing": "isolated_branch_directories_and_complete_terminal_collection",
                "installed": False,
            },
            {
                "project": "bazelbuild/bazel",
                "stars_observed_2026_07_18": 25630,
                "local_landing": "exact_input_action_identity_and_content_addressed_outputs",
                "installed": False,
            },
            {
                "project": "argoproj/argo-workflows",
                "stars_observed_2026_07_18": 16832,
                "local_landing": "bounded_parallel_dag_branches_and_terminal_archive",
                "installed": False,
            },
            {
                "project": "opencontainers/image-spec",
                "stars_observed_2026_07_18": 4395,
                "local_landing": "raw_member_media_digest_size_and_path_descriptors",
                "installed": False,
            },
        ],
        "routing": {
            "next_internal_route": (
                next_route
                if not failures
                else f"repair-uslci-flow-authoring-bulk-batch-{batch_index}-native-gates"
            ),
            "pause_internal_work": False,
        },
        "next_action": (
            (
                "Analyze closure of the complete 1,456-intent Flow-authoring corpus; "
                "keep Flow entity creation/save, model mutation, comparison and LCIA closed."
                if batch_index == 16
                else f"Resolve exact classification and support evidence for authoring batch {batch_index + 1}; "
                "keep Flow entity creation/save, model mutation, comparison and LCIA closed."
            )
            if not failures
            else "Repair the recorded failed native branches and replay only from the same exact inputs."
        ),
    }
    for filename, content in output_rows.values():
        path = outputs / filename
        if path.name.endswith(".tar"):
            continue
        path.write_bytes(content)
    (
        outputs
        / f"uslci-flow-authoring-bulk-batch-{batch_index}-native-gates.json"
    ).write_bytes(
        _json_bytes(report, pretty=True)
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute exact bulk Flow plans through copied TianGong native gates."
    )
    for name in EXPECTED_INPUT_NAMES:
        option = name.replace("_", "-")
        parser.add_argument(f"--{option}", type=Path, required=True)
        parser.add_argument(f"--{option}-binding", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
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
    execute_and_materialize(
        contents=contents,
        bindings=bindings,
        artifact_root=args.artifact_root,
        runtime_root=args.runtime_root,
        out_dir=args.out_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
