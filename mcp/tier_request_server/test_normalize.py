"""Local smoke/unit tests for the tier-request MCP normalization function.

Covers ``_normalize`` (R53): alias table -> canonical key, and illegal /
empty / ``None`` -> ``None``. These tests do NOT touch the network — the
HTTP path of ``request_tier_change`` is intentionally not exercised here.

Run from the ``mcp/`` directory with the workspace interpreter, e.g.::

    cd mcp && ../.venv/bin/python -m pytest tier_request_server/test_normalize.py -q

Importing ``server`` loads ``mcp.server.fastmcp`` at module top; that package
is installed in the workspace ``.venv`` (per earlier tasks), so collection
works with that interpreter.
"""

import os
import sys

import pytest

# Make the package importable regardless of the current working directory:
# add the ``mcp/`` parent so ``tier_request_server`` resolves as a package.
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from tier_request_server.server import _normalize  # noqa: E402


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Canonical keys pass through (lowercased then mapped back).
        ("Pro", "Pro"),
        ("ProPlus", "ProPlus"),
        ("ProMax", "ProMax"),
        ("Power", "Power"),
    ],
)
def test_canonical_pass_through(raw, expected):
    assert _normalize(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Documented aliases.
        ("pro", "Pro"),
        ("pro+", "ProPlus"),
        ("proplus", "ProPlus"),
        ("pro plus", "ProPlus"),
        ("promax", "ProMax"),
        ("pro max", "ProMax"),
        ("pro-max", "ProMax"),
        ("power", "Power"),
    ],
)
def test_aliases_map_to_canonical(raw, expected):
    assert _normalize(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Case-insensitive variants.
        ("PRO", "Pro"),
        ("Pro+", "ProPlus"),
        ("PROPLUS", "ProPlus"),
        ("Pro Plus", "ProPlus"),
        ("PROMAX", "ProMax"),
        ("Pro Max", "ProMax"),
        ("Pro-Max", "ProMax"),
        ("POWER", "Power"),
        # Underscore variants are normalized to spaces.
        ("pro_plus", "ProPlus"),
        ("PRO_PLUS", "ProPlus"),
        ("pro_max", "ProMax"),
        # Surrounding whitespace is stripped.
        ("  pro plus  ", "ProPlus"),
        ("\tpower\n", "Power"),
    ],
)
def test_case_insensitive_underscore_and_whitespace(raw, expected):
    assert _normalize(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "prox",
        "pro maxx",
        "enterprise",
        "free",
        "pro++",
        "max",
        "plus",
        "unknown-tier",
    ],
)
def test_illegal_empty_and_none_return_none(raw):
    assert _normalize(raw) is None
