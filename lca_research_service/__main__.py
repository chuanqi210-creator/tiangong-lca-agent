"""Local D-18 service entry point."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import uvicorn

from .app import ServiceConfig, create_app
from .attachments import QingxiaodaFileBindingResolver


def _load_project_tokens() -> tuple[tuple[str, str], ...]:
    import json

    token_file = os.environ.get("D18_SERVICE_PROJECT_TOKENS_FILE")
    if not token_file:
        raise SystemExit("D18_SERVICE_PROJECT_TOKENS_FILE is required")
    path = Path(token_file)
    if not path.is_file():
        raise SystemExit("D18_SERVICE_PROJECT_TOKENS_FILE is not readable")
    value = json.loads(path.read_text())
    if (
        not isinstance(value, dict)
        or not value
        or any(
            not isinstance(project_id, str)
            or not project_id
            or not isinstance(token, str)
            or not token
            for project_id, token in value.items()
        )
    ):
        raise SystemExit("D18_SERVICE_PROJECT_TOKENS_FILE is invalid")
    return tuple(sorted(value.items()))


def _load_project_token_grants() -> tuple[tuple[str, str], ...]:
    import json

    grant_file = os.environ.get("D18_SERVICE_PROJECT_TOKEN_GRANTS_FILE")
    if not grant_file:
        raise SystemExit("D18_SERVICE_PROJECT_TOKEN_GRANTS_FILE is required")
    path = Path(grant_file)
    if not path.is_file():
        raise SystemExit("D18_SERVICE_PROJECT_TOKEN_GRANTS_FILE is not readable")
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise SystemExit("D18_SERVICE_PROJECT_TOKEN_GRANTS_FILE permissions are unsafe")
    value = json.loads(path.read_text())
    rows = value.get("grants") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("contract") != "d18_service_project_token_grants/v1"
        or set(value) != {"contract", "grants"}
        or not isinstance(rows, list)
        or not rows
        or any(
            not isinstance(row, dict)
            or set(row) != {"project_prefix", "token"}
            or not isinstance(row["project_prefix"], str)
            or not isinstance(row["token"], str)
            or not row["token"]
            for row in rows
        )
    ):
        raise SystemExit("D18_SERVICE_PROJECT_TOKEN_GRANTS_FILE is invalid")
    return tuple(
        sorted((row["project_prefix"], row["token"]) for row in rows)
    )


def main() -> None:
    project_tokens = (
        _load_project_tokens()
        if os.environ.get("D18_SERVICE_PROJECT_TOKENS_FILE")
        else ()
    )
    project_token_grants = (
        _load_project_token_grants()
        if os.environ.get("D18_SERVICE_PROJECT_TOKEN_GRANTS_FILE")
        else ()
    )
    if not project_tokens and not project_token_grants:
        raise SystemExit(
            "D18_SERVICE_PROJECT_TOKENS_FILE or "
            "D18_SERVICE_PROJECT_TOKEN_GRANTS_FILE is required"
        )
    root = Path(os.environ.get("D18_SERVICE_DATA_ROOT", ".runtime/d18-service"))
    hosts = tuple(
        item.strip()
        for item in os.environ.get("D18_ATTACHMENT_HOSTS", "").split(",")
        if item.strip()
    )
    if not hosts:
        raise SystemExit("D18_ATTACHMENT_HOSTS must contain at least one host")
    release_manifest_file = os.environ.get("D18_RELEASE_MANIFEST_FILE")
    if not release_manifest_file:
        raise SystemExit("D18_RELEASE_MANIFEST_FILE is required")
    try:
        synchronous_material_wait_seconds = float(
            os.environ.get("D18_SYNC_MATERIAL_WAIT_SECONDS", "0")
        )
    except ValueError as error:
        raise SystemExit("D18_SYNC_MATERIAL_WAIT_SECONDS is invalid") from error
    file_binding_path = os.environ.get("D18_QINGXIAODA_FILE_BINDINGS_FILE")
    file_resolver = (
        QingxiaodaFileBindingResolver(file_binding_path)
        if file_binding_path
        else None
    )
    if file_resolver is not None:
        try:
            file_resolver.probe()
        except (OSError, ValueError) as error:
            raise SystemExit("D18_QINGXIAODA_FILE_BINDINGS_FILE is invalid") from error
    app = create_app(
        ServiceConfig(
            bearer_token=None,
            project_bearer_tokens=project_tokens,
            project_token_grants=project_token_grants,
            data_root=root,
            allowed_attachment_hosts=hosts,
            release_manifest_file=Path(release_manifest_file),
            synchronous_material_wait_seconds=synchronous_material_wait_seconds,
        ),
        qingxiaoda_file_resolver=file_resolver,
    )
    host = os.environ.get("D18_SERVICE_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("D18_SERVICE_PORT", "8080"))
    except ValueError as error:
        raise SystemExit("D18_SERVICE_PORT is invalid") from error
    if not 1024 <= port <= 65535:
        raise SystemExit("D18_SERVICE_PORT is invalid")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
