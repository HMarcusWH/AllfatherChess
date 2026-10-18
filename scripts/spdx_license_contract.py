#!/usr/bin/env python3
"""Bounded offline SPDX 2.x license-expression syntax and identifier validator.

Identifiers are matched against a frozen SPDX License List (including deprecated
identifiers) and exception catalogue from spdx/license-list-data tag v3.29.0.
This proves structural syntax, NOT licenses, redistribution rights or approval.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[1] / (
    "qualification/spdx-license-identifiers-v3.29.0.json")
TOKEN = re.compile(r"\(|\)|[A-Za-z0-9][A-Za-z0-9.+:-]*", re.ASCII)
USER_LICENSE = re.compile(
    r"(?:DocumentRef-[A-Za-z0-9.-]+:)?LicenseRef-[A-Za-z0-9.-]+\Z",
    re.ASCII)
MAX_LENGTH = 4096
MAX_TOKENS = 256
MAX_NESTING = 24


@lru_cache(maxsize=1)
def approved_identifiers() -> tuple[frozenset[str], frozenset[str]]:
    """Read the pinned exact identifier catalogue; malformed data fails closed."""
    policy = json.loads(CATALOG.read_text(encoding="utf-8"))
    if (type(policy) is not dict or policy.get("schema_version") != 1 or
            policy.get("spdx_license_list_version") != "3.29.0" or
            policy.get("source_tag") != "v3.29.0"):
        raise ValueError("SPDX license identifier catalogue malformed")
    licenses = policy.get("license_ids")
    exceptions = policy.get("exception_ids")
    if (type(licenses) is not list or type(exceptions) is not list or
            len(licenses) < 700 or len(exceptions) < 80 or
            not all(type(x) is str and x for x in licenses + exceptions) or
            len(set(licenses)) != len(licenses) or
            len(set(exceptions)) != len(exceptions)):
        raise ValueError("SPDX license identifier catalogue incomplete")
    return frozenset(licenses), frozenset(exceptions)


def valid_license_expression(value: object) -> bool:
    """Accept SPDX 2.x Annex-D syntax, known IDs, LicenseRefs and sentinels.

    The parser is deliberately bounded and does not interpret license terms.
    NONE/NOASSERTION are accepted as whole field values only.
    """
    if (type(value) is not str or not value or len(value) > MAX_LENGTH or
            value.strip(" ") != value or
            any(ord(c) > 127 or (ord(c) < 32) for c in value)):
        return False
    if value in ("NONE", "NOASSERTION"):
        return True
    try:
        licenses, exceptions = approved_identifiers()
    except (OSError, ValueError, TypeError):
        return False

    tokens: list[str] = []
    end = 0
    for match in TOKEN.finditer(value):
        if any(c != " " for c in value[end:match.start()]):
            return False
        tokens.append(match.group())
        end = match.end()
        if len(tokens) > MAX_TOKENS:
            return False
    if end != len(value) or not tokens:
        return False

    def is_license(token: str) -> bool:
        if USER_LICENSE.fullmatch(token):
            return True
        return token in licenses or (
            token.endswith("+") and token[:-1] in licenses)

    pos = 0

    def parse_atom(depth: int) -> bool:
        nonlocal pos
        if pos >= len(tokens) or depth > MAX_NESTING:
            return False
        current = tokens[pos]
        if current == "(":
            pos += 1
            if not parse_or(depth + 1):
                return False
            if pos >= len(tokens) or tokens[pos] != ")":
                return False
            pos += 1
            # WITH applies only to a simple license identifier, not a group.
            return True
        if not is_license(current):
            return False
        pos += 1
        if pos < len(tokens) and tokens[pos] == "WITH":
            pos += 1
            if pos >= len(tokens) or tokens[pos] not in exceptions:
                return False
            pos += 1
        return True

    def parse_and(depth: int) -> bool:
        nonlocal pos
        if not parse_atom(depth):
            return False
        while pos < len(tokens) and tokens[pos] == "AND":
            pos += 1
            if not parse_atom(depth):
                return False
        return True

    def parse_or(depth: int) -> bool:
        nonlocal pos
        if not parse_and(depth):
            return False
        while pos < len(tokens) and tokens[pos] == "OR":
            pos += 1
            if not parse_and(depth):
                return False
        return True

    return parse_or(0) and pos == len(tokens)
