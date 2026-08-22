"""Lazy, catalog-bound model credentials and exact model identities."""

from __future__ import annotations

import getpass
import re
import subprocess
from typing import Callable, Mapping, Optional


class CredentialBindingError(ValueError):
    """A provider credential binding is malformed or unsupported."""


class CredentialResolutionError(RuntimeError):
    """A valid credential binding could not yield a credential."""


class ModelBindingError(ValueError):
    """A provider model binding is malformed, absent, or floating."""


_ENVIRONMENT_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")


def _environment_name(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _ENVIRONMENT_NAME.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _exact_model_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelBindingError("model identity is absent")
    model_id = value.strip()
    lowered = model_id.casefold()
    if "latest" in lowered or lowered in {"head", "main", "master"}:
        raise ModelBindingError("model identity is floating")
    return model_id


def validate_credential_binding(binding: Mapping[str, object]) -> dict:
    if not isinstance(binding, Mapping):
        raise CredentialBindingError("credential binding is invalid")
    kind = binding.get("kind")
    if kind == "environment":
        if set(binding) != {"kind", "name"}:
            raise CredentialBindingError("environment credential binding is invalid")
        try:
            name = _environment_name(
                binding.get("name"), label="credential environment name"
            )
        except ValueError as error:
            raise CredentialBindingError(str(error)) from error
        return {"kind": "environment", "name": name}
    if kind == "macos_keychain":
        if set(binding) != {"kind", "service", "account"}:
            raise CredentialBindingError("Keychain credential binding is invalid")
        service = binding.get("service")
        if not isinstance(service, str) or not service.strip():
            raise CredentialBindingError("Keychain service is absent")
        if binding.get("account") != "current_user":
            raise CredentialBindingError(
                "Keychain account must be bound to current_user"
            )
        return {
            "kind": "macos_keychain",
            "service": service.strip(),
            "account": "current_user",
        }
    raise CredentialBindingError("credential binding kind is unsupported")


def validate_model_binding(binding: Mapping[str, object]) -> dict:
    if not isinstance(binding, Mapping):
        raise ModelBindingError("model binding is invalid")
    kind = binding.get("kind")
    if kind == "environment":
        if set(binding) != {"kind", "name"}:
            raise ModelBindingError("environment model binding is invalid")
        try:
            name = _environment_name(
                binding.get("name"), label="model environment name"
            )
        except ValueError as error:
            raise ModelBindingError(str(error)) from error
        return {"kind": "environment", "name": name}
    if kind == "catalog_default_with_environment_override":
        if set(binding) != {"kind", "environment", "default"}:
            raise ModelBindingError("catalog-default model binding is invalid")
        try:
            environment = _environment_name(
                binding.get("environment"), label="model environment name"
            )
        except ValueError as error:
            raise ModelBindingError(str(error)) from error
        return {
            "kind": kind,
            "environment": environment,
            "default": _exact_model_id(binding.get("default")),
        }
    raise ModelBindingError("model binding kind is unsupported")


def build_credential_provider(
    binding: Mapping[str, object],
    environ: Mapping[str, str],
    *,
    command_runner: Callable[..., object] = subprocess.run,
    username: Optional[str] = None,
) -> Callable[[], str]:
    """Return a lazy provider; constructing it never reads a secret."""

    normalized = validate_credential_binding(binding)
    if normalized["kind"] == "environment":
        name = normalized["name"]
        return lambda: environ.get(name, "")

    account = getpass.getuser() if username is None else username
    if not isinstance(account, str) or not account:
        raise CredentialBindingError("current Keychain account is unavailable")
    argv = [
        "/usr/bin/security",
        "find-generic-password",
        "-a",
        account,
        "-s",
        normalized["service"],
        "-w",
    ]

    def resolve() -> str:
        try:
            completed = command_runner(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CredentialResolutionError(
                "model credential is unavailable"
            ) from error
        returncode = getattr(completed, "returncode", None)
        stdout = getattr(completed, "stdout", "")
        value = stdout.strip() if returncode == 0 and isinstance(stdout, str) else ""
        if not value:
            raise CredentialResolutionError("model credential is unavailable")
        return value

    return resolve


def credential_present(
    binding: Mapping[str, object],
    environ: Mapping[str, str],
    *,
    command_runner: Callable[..., object] = subprocess.run,
    username: Optional[str] = None,
) -> bool:
    try:
        return bool(
            build_credential_provider(
                binding,
                environ,
                command_runner=command_runner,
                username=username,
            )()
        )
    except CredentialResolutionError:
        return False


def resolve_model_id(
    binding: Mapping[str, object], environ: Mapping[str, str]
) -> str:
    normalized = validate_model_binding(binding)
    if normalized["kind"] == "environment":
        return _exact_model_id(environ.get(normalized["name"]))
    value = environ.get(normalized["environment"], normalized["default"])
    return _exact_model_id(value)


def credential_environment_name(
    binding: Mapping[str, object],
) -> Optional[str]:
    normalized = validate_credential_binding(binding)
    return (
        normalized["name"]
        if normalized["kind"] == "environment"
        else None
    )


def model_environment_name(binding: Mapping[str, object]) -> Optional[str]:
    normalized = validate_model_binding(binding)
    if normalized["kind"] == "environment":
        return normalized["name"]
    return normalized["environment"]


__all__ = [
    "CredentialBindingError",
    "CredentialResolutionError",
    "ModelBindingError",
    "build_credential_provider",
    "credential_environment_name",
    "credential_present",
    "model_environment_name",
    "resolve_model_id",
    "validate_credential_binding",
    "validate_model_binding",
]
