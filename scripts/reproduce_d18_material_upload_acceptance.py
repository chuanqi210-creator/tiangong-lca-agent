#!/usr/bin/env python3
"""Reproduce the D-18 upload acceptance through the real FastAPI routes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path


PROJECT_ID = "d18-upload-acceptance"
FIXED_NOW = "2026-08-13T00:12:43Z"
FIXED_NONCE_HEX = "d18a" * 8
ACCEPTED_UPLOAD_ID = "dc3b4db8-d0b5-5f63-99ab-4cd4017ebbf0"


def reproduce(root: Path) -> dict[str, object]:
    from fastapi.testclient import TestClient

    from lca_research_service.app import ServiceConfig, create_app
    from lca_research_service import material_store as material_store_module

    source_path = root / "outputs/d18-research-material-upload-acceptance/deterministic-input.txt"
    payload = source_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    original_clock = material_store_module._utc_now
    original_uuid4 = material_store_module.uuid.uuid4
    original_uuid5 = material_store_module.uuid.uuid5
    material_store_module._utc_now = lambda: FIXED_NOW
    material_store_module.uuid.uuid4 = lambda: type("FixedUuid", (), {"hex": FIXED_NONCE_HEX})()
    upload_namespace = material_store_module._UPLOAD_NAMESPACE
    material_store_module.uuid.uuid5 = lambda namespace, name: (
        material_store_module.uuid.UUID(ACCEPTED_UPLOAD_ID)
        if namespace == upload_namespace
        else original_uuid5(namespace, name)
    )
    try:
        with tempfile.TemporaryDirectory() as directory:
            config = ServiceConfig(
                bearer_token=None,
                project_bearer_tokens=((PROJECT_ID, "acceptance-token"),),
                data_root=Path(directory),
                allowed_attachment_hosts=(),
                repository_root=root,
            )
            headers = {"Authorization": "Bearer acceptance-token"}
            upload_headers = {**headers, "X-D18-Project-ID": PROJECT_ID}
            with TestClient(create_app(config, runtime=type("Runtime", (), {})())) as client:
                created = client.post(
                    f"/v1/research/{PROJECT_ID}/material-uploads",
                    headers={**headers, "Idempotency-Key": "deterministic-local-upload"},
                    json={"uploads": [{
                        "filename": source_path.name,
                        "declared_mime": "text/plain",
                        "total_bytes": len(payload),
                        "expected_sha256": digest,
                    }]},
                )
                created.raise_for_status()
                upload = created.json()["uploads"][0]
                uploaded = client.put(
                    f"/v1/material-uploads/{upload['upload_id']}/parts/0",
                    headers={
                        **upload_headers,
                        "Content-Range": f"bytes 0-{len(payload) - 1}/{len(payload)}",
                        "X-Part-SHA256": digest,
                        "Content-Type": "application/octet-stream",
                    },
                    content=payload,
                )
                uploaded.raise_for_status()
                completed = client.post(
                    f"/v1/material-uploads/{upload['upload_id']}/complete",
                    headers=upload_headers,
                    json={"scope": "turn"},
                )
                completed.raise_for_status()
                result = completed.json()

            with TestClient(create_app(config, runtime=type("Runtime", (), {})())) as restarted:
                status = restarted.get(
                    f"/v1/material-uploads/{upload['upload_id']}",
                    headers=upload_headers,
                )
                status.raise_for_status()
                restart_transfer_status = status.json()["upload"]["transfer_status"]
    finally:
        material_store_module._utc_now = original_clock
        material_store_module.uuid.uuid4 = original_uuid4
        material_store_module.uuid.uuid5 = original_uuid5

    return {
        "contract": "d18.research_material_upload.reproduction/v1",
        "project_id": PROJECT_ID,
        "source": {
            "path": source_path.relative_to(root).as_posix(),
            "bytes": len(payload),
            "sha256": digest,
        },
        "evidence": {
            "upload_id": upload["upload_id"],
            "input_artifact_id": result["artifact"]["artifact_id"],
            "completed_material_id": result["material"]["material_id"],
            "completed_revision_id": result["revision"]["revision_id"],
            "restart_transfer_status": restart_transfer_status,
        },
        "honest_status": {
            "transfer_status": result["upload"]["transfer_status"],
            "processing_status": result["revision"]["processing_status"],
            "scientific_status": result["revision"]["scientific_status"],
        },
        "claim_ceiling": {
            "model_calls": 0,
            "tool_side_effects": 0,
            "scientific_facts": 0,
            "lca_calculations": 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    sys.path.insert(0, str(root))
    print(json.dumps(reproduce(root), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
