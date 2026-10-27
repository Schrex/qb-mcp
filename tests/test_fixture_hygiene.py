"""Guards on the Replay fixtures themselves.

A recording (scripts/record_fixtures.py) may overwrite any fixture except the hand-written
`*.sensitive.xml` ones, so sensitive inputs live only there, and tests that check
sensitive values are stripped must read them from there; otherwise a recording would
quietly remove their input and they would pass vacuously."""

import ast
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from conftest import FIXTURES
from record_fixtures import sensitive_findings

TESTS = Path(__file__).parent

# Tests that check a sensitive value is stripped (or refused), by name.
STRIPPING_TEST = re.compile(r"never_returns|never_exposes|strip|drops|sensitive|refuses_a_(fixture|report)")


def findings(fixture: Path) -> list[str]:
    root = ET.parse(fixture).getroot()
    return [f for msgs in root for rs in msgs for f in sensitive_findings(rs)]


def stripping_tests() -> list[tuple[str, list[str]]]:
    """(test id, `*.sensitive.xml` fixtures it names) for every stripping test."""
    found = []
    for module in sorted(TESTS.glob("test_*.py")):
        if module.name == Path(__file__).name:
            continue
        for node in ast.walk(ast.parse(module.read_text())):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and STRIPPING_TEST.search(node.name):
                names = [
                    n.value
                    for n in ast.walk(node)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.endswith(".sensitive.xml")
                ]
                found.append((f"{module.name}::{node.name}", names))
    return found


@pytest.mark.parametrize("fixture", sorted(p.name for p in FIXTURES.glob("*.xml") if not p.name.endswith(".sensitive.xml")))
def test_recordable_fixtures_hold_no_sensitive_values(fixture):
    assert findings(FIXTURES / fixture) == []


@pytest.mark.parametrize("fixture", sorted(p.name for p in FIXTURES.glob("*.sensitive.xml")))
def test_sensitive_fixtures_hold_sensitive_values(fixture):
    assert findings(FIXTURES / fixture)


def test_there_are_stripping_tests():
    assert len(stripping_tests()) >= 10


@pytest.mark.parametrize("test_id,fixtures", stripping_tests(), ids=[t for t, _ in stripping_tests()])
def test_every_stripping_test_reads_a_sensitive_fixture(test_id, fixtures):
    assert fixtures, f"{test_id} must respond_with(..., '<RqType>.sensitive.xml')"
    for name in fixtures:
        assert (FIXTURES / name).exists(), name
        assert findings(FIXTURES / name), f"{name} holds no non-empty sensitive value"
