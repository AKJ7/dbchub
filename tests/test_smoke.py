"""Smoke tests for the validator, the MCP helpers and the write client."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from cli.dbchub import compare_url
from scripts.mcp_server import _hex_id, _parse_id
from scripts.validate import validate_file

REPO = Path(__file__).resolve().parent.parent

MINIMAL_DBC = """VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 256 ENGINE: 8 ECU
 SG_ RPM : 0|16@1+ (0.25,0) [0|16383.75] "rpm" Vector__XXX
 SG_ TEMP : 16|8@1+ (1,-40) [-40|215] "degC" Vector__XXX
"""


def test_valid_dbc(tmp_path: Path) -> None:
    f = tmp_path / "engine.dbc"
    f.write_text(MINIMAL_DBC)
    res = validate_file(f)
    assert res.ok, res.errors
    assert res.metadata["counts"]["messages"] == 1
    assert res.metadata["counts"]["signals"] == 2
    assert res.metadata["checksum"].startswith("sha256:")


def test_garbage_dbc_fails(tmp_path: Path) -> None:
    f = tmp_path / "broken.dbc"
    f.write_text("this is not a DBC file\n")
    assert not validate_file(f).ok


def test_unsupported_extension_fails(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    assert not validate_file(f).ok


def test_a_shipped_database_is_valid() -> None:
    res = validate_file(REPO / "databases/comma/body/can/comma_body.dbc")
    assert res.ok, res.errors
    assert res.metadata["counts"]["messages"] > 0


@pytest.mark.parametrize(
    "text, expected",
    [("0x3D8", 0x3D8), ("984", 984), (" 0x1d0 ", 0x1D0), ("nope", None)],
)
def test_parse_id(text: str, expected: int | None) -> None:
    assert _parse_id(text) == expected


def test_hex_id() -> None:
    assert _hex_id(0x3D8, False) == "0x3D8"
    assert _hex_id(0x18FEF100, True) == "0x18FEF100"


def test_compare_url() -> None:
    url = compare_url("AKJ7/dbchub", "master", "alice:add-engine-1", "Add x & y", "body\ntext")
    parts = urlsplit(url)
    assert parts.netloc == "github.com"
    assert parts.path == "/AKJ7/dbchub/compare/master...alice:add-engine-1"
    q = parse_qs(parts.query)
    assert q == {"quick_pull": ["1"], "title": ["Add x & y"], "body": ["body\ntext"]}
