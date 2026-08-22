"""Executor-isolated machine grader for open-ended LCA candidate quality."""

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Callable, Mapping


GRADER_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "claim_is_source_bound": {"type": "boolean"},
        "missing_data_is_bounded": {"type": "boolean"},
        "lca_units_and_scope_are_respected": {"type": "boolean"},
        "result_is_useful_within_limits": {"type": "boolean"},
    },
    "required": [
        "score",
        "claim_is_source_bound",
        "missing_data_is_bounded",
        "lca_units_and_scope_are_respected",
        "result_is_useful_within_limits",
    ],
    "additionalProperties": False,
}


class GeminiIsolatedGrader:
    """One stateless Google-SDK grader call with no product write port."""

    def __init__(
        self,
        *,
        model_id: str,
        api_key_provider: Callable[[], str],
        client_factory: Callable[..., object] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("grader model identity is absent")
        self.model_id = model_id
        self.api_key_provider = api_key_provider
        self.client_factory = client_factory
        self.sleep = sleep

    def grade(
        self,
        *,
        candidate: Mapping[str, object],
        task: Mapping[str, object],
        oracle: Mapping[str, object],
        route_id: str,
    ) -> dict:
        if not isinstance(route_id, str) or not route_id.strip():
            raise ValueError("grader route identity is absent")
        credential = self.api_key_provider()
        if not isinstance(credential, str) or not credential:
            raise ValueError("grader credential is unavailable")
        if self.client_factory is not None:
            client = self.client_factory(
                api_key=credential,
                http_options={
                    "base_url": "https://generativelanguage.googleapis.com",
                    "api_version": "v1beta",
                    "timeout": 180000,
                },
            )
        else:
            try:
                from google import genai
            except ImportError as error:
                raise RuntimeError(
                    "pinned Google Gen AI grader runtime unavailable"
                ) from error
            client = genai.Client(
                api_key=credential,
                http_options={
                    "base_url": "https://generativelanguage.googleapis.com",
                    "api_version": "v1beta",
                    "timeout": 180000,
                },
            )
        visible_task = {
            "family": task.get("family"),
            "commission": deepcopy(task.get("commission_ref")),
            "transformation": [
                row.get("transformation")
                for row in task.get("derived_counterfactuals", [])
                if isinstance(row, Mapping)
            ],
            "required_boundaries": deepcopy(oracle.get("required_boundaries")),
            "expected_identity": (
                None
                if any(
                    row.get("transformation") == "hide_source_identity"
                    for row in task.get("derived_counterfactuals", [])
                    if isinstance(row, Mapping)
                )
                else (
                    str(oracle.get("official_identity"))
                    + "@"
                    + str(oracle.get("official_version"))
                )
            ),
        }
        request = {
            "model": self.model_id,
            "contents": json.dumps(
                {
                    "task": visible_task,
                    "candidate": deepcopy(dict(candidate)),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            "config": {
                "system_instruction": (
                    "Act as an isolated LCA quality grader. Score only the supplied "
                    "candidate against the supplied source-bound task. Judge the four "
                    "schema criteria independently. A missing-data boundary is required "
                    "only when the task transformation removes or invalidates a "
                    "conclusion-critical field; do not invent a gap for complete source "
                    "material. Set score to 25 points for each true criterion, but the "
                    "host will mechanically recompute it from those booleans. Do not "
                    "infer absent evidence. Return only the schema; do not include "
                    "private reasoning."
                ),
                "response_mime_type": "application/json",
                "response_json_schema": deepcopy(GRADER_SCHEMA),
                "automatic_function_calling": {"disable": True},
                "tools": [],
                "temperature": 0,
                "candidate_count": 1,
                "seed": 1804,
                "thinking_config": {"thinking_level": "LOW"},
            },
        }
        response = None
        for attempt in range(1, 4):
            try:
                response = client.models.generate_content(  # type: ignore[attr-defined]
                    **request
                )
                break
            except Exception as error:
                status = getattr(error, "status_code", None)
                if not isinstance(status, int):
                    status = getattr(error, "code", None)
                retryable = (
                    status == 429
                    or isinstance(status, int)
                    and status >= 500
                    or isinstance(error, TimeoutError)
                    or any(
                        marker in type(error).__name__.casefold()
                        for marker in ("timeout", "connection", "transport")
                    )
                )
                if not retryable or attempt == 3:
                    raise
                self.sleep(0.25 * (2 ** (attempt - 1)))
        if response is None:
            raise RuntimeError("grader produced no response")
        try:
            parsed = json.loads(str(getattr(response, "text", "")))
        except json.JSONDecodeError as error:
            raise ValueError("grader returned invalid structured output") from error
        criteria_keys = (
            "claim_is_source_bound",
            "missing_data_is_bounded",
            "lca_units_and_scope_are_respected",
            "result_is_useful_within_limits",
        )
        if not isinstance(parsed, dict) or any(
            not isinstance(parsed.get(key), bool) for key in criteria_keys
        ):
            raise ValueError("grader score is invalid")
        score = 25 * sum(1 for key in criteria_keys if parsed[key] is True)
        criteria = {key: parsed[key] for key in criteria_keys}
        public_response = {
            "score": score,
            "criteria": criteria,
            "model": getattr(response, "model_version", None) or self.model_id,
        }
        return {
            "route_id": route_id,
            "score": score,
            "criteria": criteria,
            "response_sha256": hashlib.sha256(
                json.dumps(
                    public_response,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "hidden_reasoning_recorded": False,
            "arm_identity_visible": False,
        }


__all__ = ["GRADER_SCHEMA", "GeminiIsolatedGrader"]
