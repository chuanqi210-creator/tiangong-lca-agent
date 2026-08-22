"""Compile a content-addressed, same-platform native TianGong runtime closure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Mapping, Optional, Sequence

try:
    from .model_package_replay import execute_model_package_replay
except ImportError:  # pragma: no cover - direct script execution
    from model_package_replay import execute_model_package_replay  # type: ignore


JsonObject = Dict[str, object]
RUNTIME_CLOSURE_CONTRACT = (
    "lca_research_kernel.portable_native_tool_runtime_closure/v1"
)
TIANGONG_PACKAGE = "@tiangong-lca/cli"
TIANGONG_VERSION = "0.0.25"
TIANGONG_PACKAGE_PATH = "node_modules/@tiangong-lca/cli/package.json"
TIANGONG_ENTRY_PATH = "node_modules/@tiangong-lca/cli/bin/tiangong-lca.js"
TIANGONG_LOCK_PATH = "node_modules/@tiangong-lca/cli"
TIANGONG_RESOLVED = (
    "https://registry.npmjs.org/@tiangong-lca/cli/-/cli-0.0.25.tgz"
)
TIANGONG_INTEGRITY = (
    "sha512-27mgEaPpZIPqVCPiCLbxTMOBHaKUlh4FGqACaefObRJfytLyp5bqm9h5Sn482dTZFHkhi0a1eQKnwd5nJcrj5Q=="
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _safe_path(value: object, label: str) -> str:
    path = _text(value, label)
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != path
        or not parsed.parts
        or any(part in ("", ".", "..") for part in parsed.parts)
        or "\\" in path
    ):
        raise ValueError(f"{label} must be a safe relative path")
    return path


def _parse_json(content: bytes, label: str) -> JsonObject:
    try:
        return _object(json.loads(content), label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error


def _resolve_symlink(path: str, target: str) -> str:
    parsed_target = PurePosixPath(target)
    if parsed_target.is_absolute() or "\\" in target:
        raise ValueError(f"runtime symlink {path} has an absolute or unsafe target")
    stack = list(PurePosixPath(path).parent.parts)
    for part in parsed_target.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not stack:
                raise ValueError(f"runtime symlink {path} target escapes the closure")
            stack.pop()
        else:
            stack.append(part)
    if not stack:
        raise ValueError(f"runtime symlink {path} target is empty")
    return PurePosixPath(*stack).as_posix()


def _file_entry(path: str, content: bytes, mode: int) -> JsonObject:
    if not isinstance(content, bytes):
        raise ValueError(f"runtime file {path} content must be bytes")
    if isinstance(mode, bool) or not isinstance(mode, int) or mode < 0 or mode > 0o777:
        raise ValueError(f"runtime file {path} mode is invalid")
    return {
        "path": path,
        "kind": "file",
        "sha256": _sha256(content),
        "bytes": len(content),
        "mode": mode,
        "executable": bool(mode & 0o111),
    }


def compile_portable_runtime_closure(
    node_bytes: bytes,
    node_license_bytes: bytes,
    node_identity: JsonObject,
    runtime_entries: Mapping[str, JsonObject],
) -> JsonObject:
    """Compile exact runtime content and boundary claims without materializing it."""

    identity = _object(node_identity, "Node identity")
    dynamic_dependencies = identity.get("dynamic_dependencies")
    if (
        identity.get("version") != "v24.18.0"
        or identity.get("platform") != "darwin"
        or identity.get("arch") != "arm64"
        or str(identity.get("modules_abi")) != "137"
        or "Mach-O 64-bit executable arm64"
        not in str(identity.get("binary_format", ""))
        or not isinstance(dynamic_dependencies, list)
        or not dynamic_dependencies
        or any(
            not isinstance(path, str)
            or not path.startswith(("/System/Library/", "/usr/lib/"))
            for path in dynamic_dependencies
        )
    ):
        raise ValueError(
            "the accepted Darwin arm64 Node v24.18.0/module-ABI-137 platform is required"
        )
    if not isinstance(node_bytes, bytes) or not node_bytes:
        raise ValueError("Node executable bytes are required")
    if not isinstance(node_license_bytes, bytes) or not node_license_bytes:
        raise ValueError("Node license bytes are required")

    normalized: Dict[str, JsonObject] = {}
    contents: Dict[str, bytes] = {}
    for raw_path, raw_entry in sorted(runtime_entries.items()):
        path = _safe_path(raw_path, "runtime entry path")
        entry = _object(raw_entry, f"runtime entry {path}")
        kind = entry.get("kind")
        if kind == "file":
            content = entry.get("content")
            mode = entry.get("mode")
            binding = _file_entry(path, content, mode)  # type: ignore[arg-type]
            normalized[path] = binding
            contents[path] = content  # type: ignore[assignment]
        elif kind == "symlink":
            target = _text(entry.get("target"), f"runtime symlink {path} target")
            normalized[path] = {
                "path": path,
                "kind": "symlink",
                "target": target,
                "resolved_target": _resolve_symlink(path, target),
            }
        else:
            raise ValueError(
                f"runtime entry {path} has unsupported special kind; only file or symlink is allowed"
            )
    if not normalized:
        raise ValueError("runtime entries are required")
    for path, entry in normalized.items():
        if entry["kind"] == "symlink" and entry["resolved_target"] not in normalized:
            raise ValueError(f"runtime symlink {path} target is missing from the closure")

    for required in (
        "package.json",
        "package-lock.json",
        TIANGONG_PACKAGE_PATH,
        TIANGONG_ENTRY_PATH,
    ):
        if required not in contents:
            raise ValueError(f"required TianGong runtime entry is missing: {required}")
    root_package = _parse_json(contents["package.json"], "runtime package.json")
    lock = _parse_json(contents["package-lock.json"], "runtime package lock")
    cli = _parse_json(contents[TIANGONG_PACKAGE_PATH], "TianGong package.json")
    packages = _object(lock.get("packages"), "runtime lock packages")
    lock_cli = _object(packages.get(TIANGONG_LOCK_PATH), "TianGong lock entry")
    cli_bin = _object(cli.get("bin"), "TianGong executable map")
    cli_engines = _object(cli.get("engines"), "TianGong engine contract")
    lock_engines = _object(lock_cli.get("engines"), "TianGong lock engine contract")
    if (
        cli.get("name") != TIANGONG_PACKAGE
        or cli.get("version") != TIANGONG_VERSION
        or cli_bin.get("tiangong-lca") not in (
            "./bin/tiangong-lca.js",
            "bin/tiangong-lca.js",
        )
        or cli_engines.get("node") != ">=24 <25"
        or lock_cli.get("version") != TIANGONG_VERSION
        or lock_cli.get("resolved") != TIANGONG_RESOLVED
        or lock_cli.get("integrity") != TIANGONG_INTEGRITY
        or lock_engines.get("node") != ">=24 <25"
    ):
        raise ValueError("TianGong version, executable, engine or lock integrity drifted")
    root_dependencies = _object(
        root_package.get("dependencies"), "runtime root dependencies"
    )
    if TIANGONG_PACKAGE not in root_dependencies:
        raise ValueError("runtime root does not declare the TianGong package")
    entry_binding = normalized[TIANGONG_ENTRY_PATH]
    if entry_binding.get("kind") != "file" or entry_binding.get("executable") is not True:
        raise ValueError("TianGong executable entry must be an executable file")

    package_inventory: List[JsonObject] = []
    omitted_optional_lock_entries: List[JsonObject] = []
    for lock_path, raw_lock_entry in sorted(packages.items()):
        if not isinstance(lock_path, str) or not lock_path.startswith("node_modules/"):
            continue
        installed_lock_entry = _object(
            raw_lock_entry, f"installed lock entry {lock_path}"
        )
        package_json_path = f"{lock_path}/package.json"
        content = contents.get(package_json_path)
        if content is None:
            if installed_lock_entry.get("optional") is True:
                os_values = installed_lock_entry.get("os")
                cpu_values = installed_lock_entry.get("cpu")
                if os_values is not None and (
                    not isinstance(os_values, list)
                    or any(not isinstance(value, str) for value in os_values)
                ):
                    raise ValueError(f"optional lock OS constraint is invalid: {lock_path}")
                if cpu_values is not None and (
                    not isinstance(cpu_values, list)
                    or any(not isinstance(value, str) for value in cpu_values)
                ):
                    raise ValueError(f"optional lock CPU constraint is invalid: {lock_path}")
                omitted_optional_lock_entries.append(
                    {
                        "path": lock_path,
                        "version": installed_lock_entry.get("version"),
                        "os": os_values,
                        "cpu": cpu_values,
                    }
                )
                continue
            raise ValueError(f"locked package is missing package.json: {lock_path}")
        package = _parse_json(content, f"installed package {package_json_path}")
        name = _text(
            package.get("name"), f"installed package name at {package_json_path}"
        )
        version = _text(
            package.get("version"),
            f"installed package version at {package_json_path}",
        )
        if installed_lock_entry.get("version") != version:
            raise ValueError(f"installed package version drifted from lock: {lock_path}")
        license_value = package.get("license")
        license_text = (
            license_value.strip()
            if isinstance(license_value, str) and license_value.strip()
            else None
        )
        package_inventory.append(
            {
                "path": lock_path,
                "name": name,
                "version": version,
                "license": license_text,
                "package_json_sha256": _sha256(content),
            }
        )
    if not package_inventory:
        raise ValueError("installed package inventory is empty")
    cli_inventory = [
        row
        for row in package_inventory
        if row["name"] == TIANGONG_PACKAGE and row["version"] == TIANGONG_VERSION
    ]
    if len(cli_inventory) != 1:
        raise ValueError("installed TianGong package inventory is ambiguous")
    missing_licenses = [row for row in package_inventory if row["license"] is None]

    file_bindings: List[JsonObject] = [
        _file_entry("bin/node", node_bytes, 0o755),
        _file_entry("licenses/node-LICENSE", node_license_bytes, 0o644),
    ]
    for path, entry in normalized.items():
        value = dict(entry)
        value["path"] = f"npm-runtime/{path}"
        if value.get("kind") == "symlink":
            value["resolved_target"] = f"npm-runtime/{value['resolved_target']}"
        file_bindings.append(value)
    file_bindings.sort(key=lambda row: str(row["path"]))
    tree_rows = [
        {
            key: row[key]
            for key in (
                "path",
                "kind",
                "sha256",
                "bytes",
                "mode",
                "target",
                "resolved_target",
            )
            if key in row
        }
        for row in file_bindings
    ]
    tree_sha256 = _sha256(_json_bytes(tree_rows))
    closure_version = _sha256(
        _json_bytes(
            {
                "contract": RUNTIME_CLOSURE_CONTRACT,
                "node": {
                    "version": identity["version"],
                    "platform": identity["platform"],
                    "arch": identity["arch"],
                    "modules_abi": str(identity["modules_abi"]),
                    "sha256": _sha256(node_bytes),
                },
                "tiangong_integrity": TIANGONG_INTEGRITY,
                "tree_sha256": tree_sha256,
            }
        )
    )[:16]
    file_count = sum(row["kind"] == "file" for row in file_bindings)
    symlink_count = sum(row["kind"] == "symlink" for row in file_bindings)
    verified_bytes = sum(
        int(row.get("bytes", 0)) for row in file_bindings if row["kind"] == "file"
    )
    return {
        "schema_version": 1,
        "contract": RUNTIME_CLOSURE_CONTRACT,
        "status": "portable_native_tool_runtime_closure_compiled",
        "closure_version": closure_version,
        "tree_sha256": tree_sha256,
        "runtime_file_count": file_count,
        "runtime_symlink_count": symlink_count,
        "runtime_entry_count": len(file_bindings),
        "verified_byte_count": verified_bytes,
        "installed_package_count": len(package_inventory),
        "omitted_optional_lock_entry_count": len(omitted_optional_lock_entries),
        "omitted_optional_lock_entries": omitted_optional_lock_entries,
        "missing_license_count": len(missing_licenses),
        "license_review_status": "required_before_distribution",
        "node": {
            "version": identity["version"],
            "platform": identity["platform"],
            "arch": identity["arch"],
            "modules_abi": str(identity["modules_abi"]),
            "binary_format": identity["binary_format"],
            "dynamic_dependencies": list(dynamic_dependencies),
            "sha256": _sha256(node_bytes),
            "bytes": len(node_bytes),
        },
        "tiangong_cli": {
            "package": TIANGONG_PACKAGE,
            "version": TIANGONG_VERSION,
            "resolved": TIANGONG_RESOLVED,
            "integrity": TIANGONG_INTEGRITY,
            "package_json_sha256": cli_inventory[0]["package_json_sha256"],
            "entry_sha256": entry_binding["sha256"],
        },
        "launcher": {
            "relative_command_prefix": [
                "bin/node",
                "npm-runtime/node_modules/@tiangong-lca/cli/bin/tiangong-lca.js",
            ],
            "npm_required_at_execution": False,
            "npm_cache_required_at_execution": False,
        },
        "file_bindings": file_bindings,
        "package_inventory": package_inventory,
        "missing_license_packages": [
            {"name": row["name"], "version": row["version"], "path": row["path"]}
            for row in missing_licenses
        ],
        "runtime_content_closed": True,
        "same_platform_relocation_proven": False,
        "system_runtime_closed": False,
        "cross_platform_portability_proven": False,
        "publication_allowed": False,
        "distribution_allowed": False,
        "comparison_allowed": False,
        "lcia_allowed": False,
    }


def scan_runtime_entries(runtime_root: Path) -> Dict[str, JsonObject]:
    """Read regular files and safe symlinks without following linked directories."""

    root = Path(runtime_root).resolve()
    if not root.is_dir():
        raise ValueError("npm runtime root must be a directory")
    entries: Dict[str, JsonObject] = {}
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        retained_directories = []
        for name in sorted(directories):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                entries[relative] = {"kind": "symlink", "target": os.readlink(path)}
            elif stat.S_ISDIR(mode):
                retained_directories.append(name)
            else:
                raise ValueError(f"runtime tree contains a special entry: {relative}")
        directories[:] = retained_directories
        for name in sorted(files):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            metadata = os.lstat(path)
            if stat.S_ISLNK(metadata.st_mode):
                entries[relative] = {"kind": "symlink", "target": os.readlink(path)}
            elif stat.S_ISREG(metadata.st_mode):
                entries[relative] = {
                    "kind": "file",
                    "content": path.read_bytes(),
                    "mode": stat.S_IMODE(metadata.st_mode),
                }
            else:
                raise ValueError(f"runtime tree contains a special entry: {relative}")
    return dict(sorted(entries.items()))


def discover_node_identity(node_path: Path) -> JsonObject:
    """Read the exact Node platform identity and dynamic system dependencies."""

    node = Path(node_path).resolve()
    if not node.is_file():
        raise ValueError("Node executable is missing")
    identity_run = subprocess.run(
        [
            str(node),
            "-p",
            "JSON.stringify({version:process.version,platform:process.platform,arch:process.arch,modules_abi:process.versions.modules})",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    identity = _parse_json(identity_run.stdout.encode("utf-8"), "Node identity")
    file_run = subprocess.run(
        ["file", str(node)], check=True, capture_output=True, text=True
    )
    otool_run = subprocess.run(
        ["otool", "-L", str(node)], check=True, capture_output=True, text=True
    )
    dependencies = []
    for line in otool_run.stdout.splitlines()[1:]:
        value = line.strip().split(" (", 1)[0].strip()
        if value:
            dependencies.append(value)
    return {
        **identity,
        "binary_format": file_run.stdout.strip(),
        "dynamic_dependencies": dependencies,
        "source_path": str(node),
    }


def discover_cached_tiangong_runtime() -> JsonObject:
    """Resolve the exact offline npm execution tree once for closure bootstrap."""

    command = [
        "npm",
        "exec",
        "--yes",
        "--offline",
        "--package=@tiangong-lca/cli@0.0.25",
        "--",
        "sh",
        "-c",
        "command -v tiangong-lca",
    ]
    completed = subprocess.run(
        command, check=True, capture_output=True, text=True, env={**os.environ, "npm_config_offline": "true"}
    )
    executable = Path(completed.stdout.strip())
    if not executable.is_absolute() or not executable.is_symlink():
        raise ValueError("offline npm bootstrap did not return a linked TianGong entry")
    runtime_root = executable.parent.parent.parent.resolve()
    expected = (runtime_root / TIANGONG_ENTRY_PATH).resolve()
    if executable.resolve() != expected or not expected.is_file():
        raise ValueError("offline npm bootstrap TianGong entry escaped its runtime root")
    node_executable = shutil.which("node")
    if not node_executable:
        raise ValueError("Node executable is unavailable for runtime closure bootstrap")
    node_path = Path(node_executable).resolve()
    node_license_path = node_path.parent.parent / "LICENSE"
    if not node_path.is_file() or not node_license_path.is_file():
        raise ValueError("Node executable or license is missing")
    return {
        "runtime_root": str(runtime_root),
        "executable_path": str(executable),
        "resolved_entry_path": str(expected),
        "node_path": str(node_path),
        "node_license_path": str(node_license_path.resolve()),
        "bootstrap_command_sha256": _sha256(_json_bytes(command)),
        "network_read_allowed": False,
        "npm_offline_enforced": True,
    }


def _write_runtime_archive(
    archive_path: Path,
    compiled: JsonObject,
    node_bytes: bytes,
    node_license_bytes: bytes,
    runtime_entries: Mapping[str, JsonObject],
) -> None:
    metadata_files = {
        "runtime-manifest.json": _json_bytes(compiled),
        "package-inventory.json": _json_bytes(
            {
                "installed_package_count": compiled["installed_package_count"],
                "omitted_optional_lock_entry_count": compiled[
                    "omitted_optional_lock_entry_count"
                ],
                "omitted_optional_lock_entries": compiled[
                    "omitted_optional_lock_entries"
                ],
                "packages": compiled["package_inventory"],
            }
        ),
        "license-inventory.json": _json_bytes(
            {
                "status": compiled["license_review_status"],
                "missing_license_count": compiled["missing_license_count"],
                "missing_license_packages": compiled["missing_license_packages"],
                "packages": compiled["package_inventory"],
                "node_license_path": "licenses/node-LICENSE",
                "distribution_allowed": False,
            }
        ),
    }
    file_sources: Dict[str, tuple[bytes, int]] = {
        "bin/node": (node_bytes, 0o755),
        "licenses/node-LICENSE": (node_license_bytes, 0o644),
    }
    symlinks: Dict[str, str] = {}
    for relative, entry in runtime_entries.items():
        destination = f"npm-runtime/{relative}"
        if entry.get("kind") == "file":
            content = entry.get("content")
            mode = entry.get("mode")
            if not isinstance(content, bytes) or not isinstance(mode, int):
                raise ValueError(f"runtime source entry is malformed: {relative}")
            file_sources[destination] = (content, mode)
        elif entry.get("kind") == "symlink":
            symlinks[destination] = _text(
                entry.get("target"), f"runtime symlink {relative}"
            )
        else:
            raise ValueError(f"runtime source entry is unsupported: {relative}")
    for relative, content in metadata_files.items():
        file_sources[relative] = (content, 0o644)

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w", format=tarfile.USTAR_FORMAT) as archive:
        for relative in sorted(set(file_sources) | set(symlinks)):
            name = f"runtime/{relative}"
            info = tarfile.TarInfo(name)
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            if relative in symlinks:
                info.type = tarfile.SYMTYPE
                info.mode = 0o777
                info.linkname = symlinks[relative]
                info.size = 0
                archive.addfile(info)
            else:
                content, mode = file_sources[relative]
                info.type = tarfile.REGTYPE
                info.mode = mode
                info.size = len(content)
                import io

                archive.addfile(info, io.BytesIO(content))


def materialize_runtime_closure(
    compiled: JsonObject,
    node_path: Path,
    node_license_path: Path,
    runtime_root: Path,
    out_dir: Path,
) -> JsonObject:
    """Write and deterministically rebuild one runtime transfer archive."""

    node_bytes = Path(node_path).read_bytes()
    license_bytes = Path(node_license_path).read_bytes()
    entries = scan_runtime_entries(runtime_root)
    identity = _object(compiled.get("node"), "compiled Node identity")
    rebuilt = compile_portable_runtime_closure(
        node_bytes,
        license_bytes,
        {
            "version": identity.get("version"),
            "platform": identity.get("platform"),
            "arch": identity.get("arch"),
            "modules_abi": identity.get("modules_abi"),
            "binary_format": identity.get("binary_format"),
            "dynamic_dependencies": identity.get("dynamic_dependencies"),
        },
        entries,
    )
    if rebuilt != compiled:
        raise ValueError("runtime source content drifted from the compiled closure")
    output = Path(out_dir)
    if output.exists() and any(output.iterdir()):
        raise ValueError("runtime closure output directory must be absent or empty")
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "runtime-manifest.json"
    package_inventory_path = output / "package-inventory.json"
    license_inventory_path = output / "license-inventory.json"
    manifest_path.write_bytes(_json_bytes(compiled))
    package_inventory_path.write_bytes(
        _json_bytes(
            {
                "installed_package_count": compiled["installed_package_count"],
                "omitted_optional_lock_entry_count": compiled[
                    "omitted_optional_lock_entry_count"
                ],
                "omitted_optional_lock_entries": compiled[
                    "omitted_optional_lock_entries"
                ],
                "packages": compiled["package_inventory"],
            }
        )
    )
    license_inventory_path.write_bytes(
        _json_bytes(
            {
                "status": compiled["license_review_status"],
                "missing_license_count": compiled["missing_license_count"],
                "missing_license_packages": compiled["missing_license_packages"],
                "packages": compiled["package_inventory"],
                "node_license_path": "licenses/node-LICENSE",
                "distribution_allowed": False,
            }
        )
    )
    archive_path = output / "portable-native-runtime.tar"
    rebuilt_archive_path = output / ".portable-native-runtime.rebuilt.tar"
    _write_runtime_archive(
        archive_path, compiled, node_bytes, license_bytes, entries
    )
    _write_runtime_archive(
        rebuilt_archive_path, compiled, node_bytes, license_bytes, entries
    )
    archive_bytes = archive_path.read_bytes()
    rebuilt_bytes = rebuilt_archive_path.read_bytes()
    rebuilt_archive_path.unlink()
    if archive_bytes != rebuilt_bytes:
        raise RuntimeError("portable runtime archive is not deterministic")
    report = {
        "schema_version": 1,
        "contract": RUNTIME_CLOSURE_CONTRACT,
        "status": "portable_native_tool_runtime_closure_materialized",
        "closure_version": compiled["closure_version"],
        "tree_sha256": compiled["tree_sha256"],
        "archive_sha256": _sha256(archive_bytes),
        "archive_bytes": len(archive_bytes),
        "runtime_file_count": compiled["runtime_file_count"],
        "runtime_symlink_count": compiled["runtime_symlink_count"],
        "installed_package_count": compiled["installed_package_count"],
        "omitted_optional_lock_entry_count": compiled[
            "omitted_optional_lock_entry_count"
        ],
        "missing_license_count": compiled["missing_license_count"],
        "deterministic_archive_verified": True,
        "distribution_allowed": False,
    }
    (output / "runtime-closure-materialization.json").write_bytes(_json_bytes(report))
    return report


def _safe_archive_member(name: str) -> str:
    parsed = PurePosixPath(name)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != name
        or len(parsed.parts) < 2
        or parsed.parts[0] != "runtime"
        or any(part in ("", ".", "..") for part in parsed.parts)
    ):
        raise ValueError(f"archive member path is unsafe: {name}")
    return PurePosixPath(*parsed.parts[1:]).as_posix()


def extract_and_verify_runtime_closure(
    archive_path: Path, out_dir: Path
) -> JsonObject:
    """Safely extract a closure and verify every manifest-bound entry."""

    output = Path(out_dir)
    if output.exists() and any(output.iterdir()):
        raise ValueError("runtime extraction directory must be absent or empty")
    output.mkdir(parents=True, exist_ok=True)
    runtime_root = output / "runtime"
    members: Dict[str, tarfile.TarInfo] = {}
    with tarfile.open(archive_path, "r:") as archive:
        for member in archive.getmembers():
            relative = _safe_archive_member(member.name)
            if relative in members:
                raise ValueError(f"archive contains a duplicate member: {relative}")
            if not member.isfile() and not member.issym():
                raise ValueError(f"archive contains an unsafe member type: {relative}")
            if member.issym():
                resolved = _resolve_symlink(relative, member.linkname)
                if resolved == relative:
                    raise ValueError(f"archive symlink is self-referential: {relative}")
            members[relative] = member
        for relative, member in sorted(members.items()):
            target = runtime_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isfile():
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"archive file cannot be read: {relative}")
                target.write_bytes(source.read())
                target.chmod(member.mode)
        for relative, member in sorted(members.items()):
            if not member.issym():
                continue
            resolved = _resolve_symlink(relative, member.linkname)
            if resolved not in members:
                raise ValueError(f"archive symlink target is missing: {relative}")
            target = runtime_root / relative
            target.symlink_to(member.linkname)

    manifest_path = runtime_root / "runtime-manifest.json"
    if not manifest_path.is_file():
        raise ValueError("runtime archive manifest is missing")
    manifest = _parse_json(manifest_path.read_bytes(), "runtime archive manifest")
    if (
        manifest.get("contract") != RUNTIME_CLOSURE_CONTRACT
        or manifest.get("status")
        != "portable_native_tool_runtime_closure_compiled"
    ):
        raise ValueError("runtime archive manifest contract drifted")
    bindings = manifest.get("file_bindings")
    if not isinstance(bindings, list):
        raise ValueError("runtime archive file bindings are missing")
    expected_members = {
        "runtime-manifest.json",
        "package-inventory.json",
        "license-inventory.json",
    }
    file_count = 0
    symlink_count = 0
    for raw_binding in bindings:
        binding = _object(raw_binding, "runtime archive binding")
        relative = _safe_path(binding.get("path"), "runtime archive binding path")
        expected_members.add(relative)
        candidate = runtime_root / relative
        if binding.get("kind") == "file":
            if not candidate.is_file() or candidate.is_symlink():
                raise ValueError(f"runtime archive file is missing: {relative}")
            content = candidate.read_bytes()
            if (
                _sha256(content) != binding.get("sha256")
                or len(content) != binding.get("bytes")
                or stat.S_IMODE(candidate.stat().st_mode) != binding.get("mode")
            ):
                raise ValueError(f"runtime archive file content or mode drifted: {relative}")
            file_count += 1
        elif binding.get("kind") == "symlink":
            if not candidate.is_symlink() or os.readlink(candidate) != binding.get("target"):
                raise ValueError(f"runtime archive symlink drifted: {relative}")
            symlink_count += 1
        else:
            raise ValueError(f"runtime archive binding kind is unsupported: {relative}")
    if set(members) != expected_members:
        raise ValueError("runtime archive member set drifted from its manifest")
    package_inventory = _parse_json(
        (runtime_root / "package-inventory.json").read_bytes(),
        "runtime package inventory",
    )
    license_inventory = _parse_json(
        (runtime_root / "license-inventory.json").read_bytes(),
        "runtime license inventory",
    )
    if (
        package_inventory.get("packages") != manifest.get("package_inventory")
        or package_inventory.get("omitted_optional_lock_entries")
        != manifest.get("omitted_optional_lock_entries")
        or package_inventory.get("omitted_optional_lock_entry_count")
        != manifest.get("omitted_optional_lock_entry_count")
        or license_inventory.get("packages") != manifest.get("package_inventory")
        or license_inventory.get("distribution_allowed") is not False
    ):
        raise ValueError("runtime package or license inventory drifted")
    return {
        "contract": RUNTIME_CLOSURE_CONTRACT,
        "status": "portable_native_tool_runtime_closure_extracted_verified",
        "closure_version": manifest["closure_version"],
        "tree_sha256": manifest["tree_sha256"],
        "verified_file_count": file_count,
        "verified_symlink_count": symlink_count,
        "omitted_optional_lock_entry_count": manifest[
            "omitted_optional_lock_entry_count"
        ],
        "runtime_root": str(runtime_root.resolve()),
        "direct_command_prefix": [
            str((runtime_root / "bin/node").resolve()),
            str(
                (
                    runtime_root
                    / "npm-runtime/node_modules/@tiangong-lca/cli/bin/tiangong-lca.js"
                ).resolve()
            ),
        ],
        "runtime_content_closed": True,
        "system_runtime_closed": False,
        "cross_platform_portability_proven": False,
    }


def verify_extracted_runtime_root(
    runtime_root: Path, manifest: JsonObject
) -> JsonObject:
    """Re-hash a relocated runtime tree after execution."""

    root = Path(runtime_root).resolve()
    bindings = manifest.get("file_bindings")
    if not isinstance(bindings, list):
        raise ValueError("runtime manifest file bindings are missing")
    expected = {
        "runtime-manifest.json",
        "package-inventory.json",
        "license-inventory.json",
    }
    verified_files = 0
    verified_symlinks = 0
    rows = []
    for raw_binding in bindings:
        binding = _object(raw_binding, "relocated runtime binding")
        relative = _safe_path(binding.get("path"), "relocated runtime path")
        expected.add(relative)
        candidate = (root / relative).resolve(strict=False)
        lexical_candidate = root / relative
        try:
            lexical_candidate.parent.resolve().relative_to(root)
        except ValueError as error:
            raise ValueError("relocated runtime path escaped its root") from error
        if binding.get("kind") == "file":
            if not lexical_candidate.is_file() or lexical_candidate.is_symlink():
                raise ValueError(f"relocated runtime file is missing: {relative}")
            content = lexical_candidate.read_bytes()
            if (
                _sha256(content) != binding.get("sha256")
                or len(content) != binding.get("bytes")
                or stat.S_IMODE(lexical_candidate.stat().st_mode)
                != binding.get("mode")
            ):
                raise ValueError(f"relocated runtime file drifted: {relative}")
            verified_files += 1
            rows.append(
                {
                    "path": relative,
                    "kind": "file",
                    "sha256": binding["sha256"],
                    "bytes": binding["bytes"],
                    "mode": binding["mode"],
                }
            )
        elif binding.get("kind") == "symlink":
            if (
                not lexical_candidate.is_symlink()
                or os.readlink(lexical_candidate) != binding.get("target")
            ):
                raise ValueError(f"relocated runtime symlink drifted: {relative}")
            verified_symlinks += 1
            rows.append(
                {
                    "path": relative,
                    "kind": "symlink",
                    "target": binding["target"],
                    "resolved_target": binding["resolved_target"],
                }
            )
        else:
            raise ValueError(f"relocated runtime binding kind drifted: {relative}")
    actual = set(scan_runtime_entries(root))
    if actual != expected:
        raise ValueError("relocated runtime member set drifted")
    verified_tree_sha256 = _sha256(
        _json_bytes(sorted(rows, key=lambda row: row["path"]))
    )
    if verified_tree_sha256 != manifest.get("tree_sha256"):
        raise ValueError("relocated runtime tree identity drifted")
    return {
        "verified_file_count": verified_files,
        "verified_symlink_count": verified_symlinks,
        "verified_tree_sha256": verified_tree_sha256,
        "runtime_unchanged": True,
    }


def execute_relocated_runtime_replay(
    archive_path: Path,
    archive_sha256: str,
    package_manifest_path: Path,
    package_manifest_binding: JsonObject,
    package_root: Path,
    out_dir: Path,
) -> JsonObject:
    """Extract the closure and replay the unchanged scientific package directly."""

    archive = Path(archive_path)
    archive_before = archive.read_bytes()
    if _sha256(archive_before) != archive_sha256:
        raise ValueError("portable runtime archive hash drifted")
    output = Path(out_dir)
    if output.exists() and any(output.iterdir()):
        raise ValueError("relocated replay output directory must be absent or empty")
    output.mkdir(parents=True, exist_ok=True)
    extracted = extract_and_verify_runtime_closure(
        archive, output / "extracted-runtime"
    )
    runtime_root = Path(str(extracted["runtime_root"]))
    manifest = _parse_json(
        (runtime_root / "runtime-manifest.json").read_bytes(),
        "relocated runtime manifest",
    )
    runtime_before = verify_extracted_runtime_root(runtime_root, manifest)
    replay_root = output / "relocated-package-replay"
    replay = execute_model_package_replay(
        package_manifest_path,
        package_manifest_binding,
        package_root,
        replay_root,
        native_command_prefix=extracted["direct_command_prefix"],  # type: ignore[arg-type]
        runtime_closure=extracted,
    )
    runtime_after = verify_extracted_runtime_root(runtime_root, manifest)
    archive_after = archive.read_bytes()
    if archive_after != archive_before:
        raise ValueError("portable runtime archive changed during relocated replay")
    if runtime_after != runtime_before:
        raise ValueError("extracted runtime closure changed during relocated replay")
    if (
        replay.get("status") != "native_route_model_execution_replay_completed"
        or replay.get("completed_step_count") != 7
        or replay.get("resulting_processes_sha256")
        != "77569d76d73b3ff6487055c6519ff6127b5c857bb557c70e4a9f1a21822d0acc"
        or replay.get("patch_evidence_count") != 28
        or replay.get("semantic_equivalence_verified") is not True
        or replay.get("source_package_unchanged") is not True
        or replay.get("runtime_content_closed") is not True
        or replay.get("same_platform_relocation_proven") is not True
        or replay.get("system_runtime_closed") is not False
        or replay.get("runtime", {}).get("npm_invoked") is not False
        or replay.get("runtime", {}).get("tiangong_cli_version") != "0.0.25"
    ):
        raise RuntimeError("relocated runtime replay violated exact acceptance")
    replay_report_path = (
        replay_root / "outputs/native-route-model-execution-replay.json"
    )
    replay_trace_path = (
        replay_root / "outputs/native-route-model-execution-replay-trace.json"
    )
    report = {
        "schema_version": 1,
        "contract": RUNTIME_CLOSURE_CONTRACT,
        "status": "portable_native_tool_runtime_closure_verified",
        "closure_version": manifest["closure_version"],
        "tree_sha256": manifest["tree_sha256"],
        "archive_sha256": archive_sha256,
        "archive_bytes": len(archive_before),
        "runtime_file_count": manifest["runtime_file_count"],
        "runtime_symlink_count": manifest["runtime_symlink_count"],
        "installed_package_count": manifest["installed_package_count"],
        "omitted_optional_lock_entry_count": manifest[
            "omitted_optional_lock_entry_count"
        ],
        "omitted_optional_lock_entries": manifest[
            "omitted_optional_lock_entries"
        ],
        "missing_license_count": manifest["missing_license_count"],
        "license_review_status": manifest["license_review_status"],
        "node": manifest["node"],
        "tiangong_cli": manifest["tiangong_cli"],
        "replay_version": replay["replay_version"],
        "completed_step_count": 7,
        "raw_resulting_processes_sha256": replay[
            "raw_resulting_processes_sha256"
        ],
        "resulting_processes_sha256": replay["resulting_processes_sha256"],
        "patch_evidence_count": 28,
        "semantic_equivalence_verified": True,
        "source_package_unchanged": True,
        "runtime_archive_unchanged": True,
        "extracted_runtime_unchanged": True,
        "npm_invoked_during_relocated_replay": False,
        "npm_cache_read_during_relocated_replay": False,
        "runtime_content_closed": True,
        "same_platform_relocation_proven": True,
        "system_runtime_closed": False,
        "cross_platform_portability_proven": False,
        "os_network_sandbox": False,
        "distribution_allowed": False,
        "network_read_executed_count": 0,
        "external_action_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "current_model_mutation_executed_count": 0,
        "replay_report": {
            "path": replay_report_path.relative_to(output).as_posix(),
            "sha256": _sha256(replay_report_path.read_bytes()),
            "bytes": replay_report_path.stat().st_size,
        },
        "replay_trace": {
            "path": replay_trace_path.relative_to(output).as_posix(),
            "sha256": _sha256(replay_trace_path.read_bytes()),
            "bytes": replay_trace_path.stat().st_size,
        },
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "next_action": (
            "Compile a native route-provider network gap map from current exchanges "
            "and lifecyclemodel boundaries."
        ),
        "routing": {
            "next_internal_route": "compile-native-route-provider-network-gap-map",
            "pause_internal_work": False,
            "reason": (
                "Same-platform runtime relocation is exact; retain system/cross-platform "
                "and distribution limits, and return to scientific network integration."
            ),
        },
    }
    outputs = output / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "portable-native-tool-runtime-closure.json").write_bytes(
        _json_bytes(report)
    )
    return report


def run_portable_runtime_closure(
    node_path: Path,
    node_license_path: Path,
    runtime_root: Path,
    package_manifest_path: Path,
    package_manifest_binding_path: Path,
    package_root: Path,
    out_dir: Path,
) -> JsonObject:
    """Compile, transfer, extract and verify one exact native runtime closure."""

    try:
        manifest_binding = _object(
            json.loads(Path(package_manifest_binding_path).read_bytes()),
            "package manifest binding",
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("package manifest binding is invalid") from error
    node = Path(node_path)
    node_license = Path(node_license_path)
    runtime = Path(runtime_root)
    output = Path(out_dir)
    entries = scan_runtime_entries(runtime)
    compiled = compile_portable_runtime_closure(
        node.read_bytes(),
        node_license.read_bytes(),
        discover_node_identity(node),
        entries,
    )
    materialized = materialize_runtime_closure(
        compiled,
        node,
        node_license,
        runtime,
        output / "closure",
    )
    return execute_relocated_runtime_replay(
        output / "closure/portable-native-runtime.tar",
        str(materialized["archive_sha256"]),
        Path(package_manifest_path),
        manifest_binding,
        Path(package_root),
        output / "verification",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bind and relocate the exact native TianGong runtime closure."
    )
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--node-license", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-binding", type=Path, required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_portable_runtime_closure(
        args.node,
        args.node_license,
        args.runtime_root,
        args.manifest,
        args.manifest_binding,
        args.package_root,
        args.out_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
