"""Constant-time Bearer admission without credential logging."""

from __future__ import annotations

import secrets
import re
from collections.abc import Mapping

from fastapi import HTTPException, Request, status


_PROJECT_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}-$")
_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class BearerAuthenticator:
    def __init__(
        self,
        token: str | None,
        *,
        project_tokens: Mapping[str, str] | None = None,
        project_token_grants: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self._project_tokens = dict(project_tokens or {})
        self._project_token_grants = tuple(project_token_grants)
        if not token and not self._project_tokens and not self._project_token_grants:
            raise ValueError("at least one service bearer token is required")
        if any(not project_id or not value for project_id, value in self._project_tokens.items()):
            raise ValueError("project bearer token entries must be non-empty")
        prefixes = [prefix for prefix, _ in self._project_token_grants]
        grant_tokens = [value for _, value in self._project_token_grants]
        if (
            any(
                not isinstance(prefix, str)
                or _PROJECT_PREFIX.fullmatch(prefix) is None
                or not isinstance(value, str)
                or not value
                for prefix, value in self._project_token_grants
            )
            or len(set(prefixes)) != len(prefixes)
            or len(set(grant_tokens)) != len(grant_tokens)
            or any(
                left.startswith(right) or right.startswith(left)
                for index, left in enumerate(prefixes)
                for right in prefixes[index + 1 :]
            )
        ):
            raise ValueError("project namespace grants are invalid or ambiguous")
        self._token = token

    def require(self, request: Request, *, project_id: str | None = None) -> None:
        if project_id is not None and _PROJECT_ID.fullmatch(project_id) is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="project access denied",
            )
        header = request.headers.get("authorization", "")
        scheme, separator, candidate = header.partition(" ")
        syntactically_valid = (
            separator == " " and scheme.casefold() == "bearer" and bool(candidate)
        )
        if self._project_tokens or self._project_token_grants:
            if project_id is None:
                valid = syntactically_valid and any(
                    secrets.compare_digest(candidate, expected)
                    for expected in (
                        *self._project_tokens.values(),
                        *(value for _, value in self._project_token_grants),
                    )
                )
            else:
                expected = self._project_tokens.get(project_id)
                exact_valid = (
                    syntactically_valid
                    and expected is not None
                    and secrets.compare_digest(candidate, expected)
                )
                prefix_valid = syntactically_valid and any(
                    project_id.startswith(prefix)
                    and secrets.compare_digest(candidate, grant_token)
                    for prefix, grant_token in self._project_token_grants
                )
                valid = exact_valid or prefix_valid
        else:
            valid = (
                syntactically_valid
                and self._token is not None
                and secrets.compare_digest(candidate, self._token)
            )
        if not valid:
            status_code = (
                status.HTTP_403_FORBIDDEN
                if syntactically_valid
                and (self._project_tokens or self._project_token_grants)
                else status.HTTP_401_UNAUTHORIZED
            )
            raise HTTPException(
                status_code=status_code,
                detail=(
                    "project access denied"
                    if status_code == status.HTTP_403_FORBIDDEN
                    else "authentication required"
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )
