"""Console entry points and Postgres timestamp coercion (CI regressions)."""

from __future__ import annotations

import importlib
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_platform.db.backend import _decode_timestamp, _encode_timestamp

ROOT = Path(__file__).parent.parent


def test_console_scripts_resolve():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    scripts = pyproject["project"]["scripts"]
    assert scripts
    for name, target in scripts.items():
        module_name, _, attr = target.partition(":")
        module = importlib.import_module(module_name)
        assert callable(getattr(module, attr)), f"{name} -> {target} is not callable"


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-16T12:11:16.666780+00:00",
        "2026-08-16T12:11:16+00:00",
        datetime(2026, 8, 16, 12, 11, 16, tzinfo=UTC),
    ],
)
def test_encode_timestamp_accepts_iso_strings_and_datetimes(value):
    encoded = _encode_timestamp(value)
    assert isinstance(encoded, str)
    assert encoded.startswith("2026-08-16")


def test_decode_timestamp_parses_postgres_text_output():
    decoded = _decode_timestamp("2026-08-16 12:11:16.666780+00")
    assert isinstance(decoded, datetime)
    assert decoded.utcoffset() is not None
    assert decoded.utcoffset().total_seconds() == 0
    assert decoded.year == 2026 and decoded.microsecond == 666780


def test_decode_timestamp_falls_back_to_text():
    assert _decode_timestamp("infinity") == "infinity"
