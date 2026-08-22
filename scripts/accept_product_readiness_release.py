#!/usr/bin/env python3
"""Publish one immutable Product Readiness release after all five journeys pass."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_service.product_readiness_gate import (
    canonical_sha256,
    evaluate_product_readiness_release,
)
from lca_research_service.native_evidence_authentication import (
    open_native_evidence_authentication_ports,
)


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"immutable product evidence drifted: {path}")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ValueError(f"immutable product evidence drifted: {path}")
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_pointer(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def publish_product_readiness_release(
    packages: list[Mapping[str, Any]],
    *,
    output_root: Path,
    browser_capture_verifier: Callable[[bytes, str], bool],
) -> dict[str, object]:
    receipt = evaluate_product_readiness_release(
        packages, browser_capture_verifier=browser_capture_verifier
    )
    if receipt.get("status") != "passed":
        raise ValueError(
            "product readiness release failed: "
            + ", ".join(str(error) for error in receipt.get("errors", []))
        )
    release_id = str(receipt["release_id"])
    receipt_sha256 = str(receipt["receipt_sha256"])
    release_root = (
        Path(output_root).resolve()
        / "releases"
        / release_id
        / receipt_sha256
    )
    package_by_journey = {
        str(package["observation"]["journey"]): package for package in packages
    }
    for journey_row in receipt["journeys"]:
        journey = str(journey_row["journey"])
        package = package_by_journey[journey]
        journey_root = release_root / "journeys" / journey
        _write_once(journey_root / "observation.json", package["observation_bytes"])
        _write_once(journey_root / "receipt.json", package["receipt_bytes"])
        for evidence in package["browser_captures"]:
            capture = evidence["capture"]
            stage = str(capture["stage"])
            _write_once(
                journey_root / "browser" / f"{stage}.json",
                evidence["capture_bytes"],
            )
            _write_once(
                journey_root / "browser" / f"{stage}.png",
                evidence["screenshot_bytes"],
            )
    receipt_path = release_root / "product-readiness-release.json"
    receipt_bytes = _json_bytes(receipt)
    _write_once(receipt_path, receipt_bytes)
    pointer = {
        "contract": "d18.current_product_readiness_release/v1",
        "release_id": release_id,
        "receipt_ref": receipt_path.relative_to(Path(output_root).resolve()).as_posix(),
        "receipt_file_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "receipt_sha256": receipt_sha256,
        "journey_count": len(receipt["journeys"]),
    }
    pointer["pointer_sha256"] = canonical_sha256(pointer)
    _replace_pointer(
        Path(output_root).resolve() / "current-release.json",
        _json_bytes(pointer),
    )
    return {
        "status": "passed",
        "release_id": release_id,
        "receipt_sha256": receipt_sha256,
        "receipt_path": receipt_path.as_posix(),
        "current_pointer": (
            Path(output_root).resolve() / "current-release.json"
        ).as_posix(),
    }


def _confined_path(base: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("product readiness evidence reference is invalid")
    resolved = (base / value).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as error:
        raise ValueError("product readiness evidence escaped its root") from error
    if not resolved.is_file():
        raise ValueError("product readiness evidence file is absent")
    return resolved


def load_release_packages(manifest_path: Path) -> list[dict[str, object]]:
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("contract") != "d18.product_readiness_release_input/v1"
        or not isinstance(manifest.get("journeys"), list)
    ):
        raise ValueError("product readiness input manifest is invalid")
    base = manifest_path.parent.resolve()
    packages: list[dict[str, object]] = []
    for row in manifest["journeys"]:
        if not isinstance(row, Mapping) or not isinstance(
            row.get("browser_captures"), list
        ):
            raise ValueError("product readiness journey manifest is invalid")
        observation_path = _confined_path(base, row.get("observation_ref"))
        receipt_path = _confined_path(base, row.get("receipt_ref"))
        observation_bytes = observation_path.read_bytes()
        receipt_bytes = receipt_path.read_bytes()
        observation = json.loads(observation_bytes.decode("utf-8"))
        receipt = json.loads(receipt_bytes.decode("utf-8"))
        captures: list[dict[str, object]] = []
        for capture_row in row["browser_captures"]:
            if not isinstance(capture_row, Mapping):
                raise ValueError("product readiness browser manifest is invalid")
            capture_path = _confined_path(base, capture_row.get("capture_ref"))
            screenshot_path = _confined_path(
                base, capture_row.get("screenshot_ref")
            )
            capture_bytes = capture_path.read_bytes()
            capture = json.loads(capture_bytes.decode("utf-8"))
            captures.append(
                {
                    "capture": capture,
                    "capture_bytes": capture_bytes,
                    "screenshot_bytes": screenshot_path.read_bytes(),
                }
            )
        packages.append(
            {
                "observation": observation,
                "observation_bytes": observation_bytes,
                "receipt": receipt,
                "receipt_bytes": receipt_bytes,
                "browser_captures": captures,
            }
        )
    return packages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    authentication = open_native_evidence_authentication_ports(
        args.data_root.resolve()
    )
    result = publish_product_readiness_release(
        load_release_packages(args.manifest),
        output_root=args.output_root,
        browser_capture_verifier=authentication.verify,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
