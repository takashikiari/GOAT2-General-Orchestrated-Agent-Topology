"""repo_git refusals name the forbidden characters and say how to write an OR without '|'."""
import pytest

from tools import repo_git as rg
from tools.goat_skills import repo_git as skill

GOAT_CMD = 'grep -rn "clear_cache\\|invalidate_cache" -- *.py **/*.py'


def _reason(cmd: str) -> str:
    with pytest.raises(ValueError) as exc:
        rg.validate(cmd)
    return str(exc.value)


def test_goat_command_still_refused_and_names_each_char():
    reason = _reason(GOAT_CMD)
    assert reason.startswith('caracter interzis: " \\ |')
    assert "fără operatori de shell, ghilimele sau $" in reason   # the old text is kept
    assert "-e A -e B" in reason


@pytest.mark.parametrize("cmd, chars", [
    ('grep -n "x" -- *.py', '"'), ("log; rm -rf ~", ";"), ("log $(id)", "$ ( )"),
    ("show 'a b'", "'"), ("log > out", ">"), ("status\nrm x", "\\n"),
])
def test_reason_lists_found_chars_in_order(cmd, chars):
    assert _reason(cmd).startswith(f"caracter interzis: {chars} —")


def test_or_hint_only_when_pipe_present():
    assert "-e A -e B" in _reason("log | sh")
    assert "-e A -e B" not in _reason("log; rm x")


def test_suggested_equivalents_pass():
    for cmd in ("grep -n -e clear_cache -e invalidate_cache -- *.py", "grep -rn clear_cache -- *.py **/*.py"):
        assert rg.validate(cmd)[0] == "grep"


def test_run_readonly_surfaces_the_named_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(rg, "discover", lambda force=False: {"lenovo:work/proj": str(tmp_path)})
    assert rg.run_readonly("proj", GOAT_CMD).startswith('REFUZAT: caracter interzis: " \\ |')


def test_skill_description_shows_e_example():
    tool = next(t for t in skill.build(None) if t.name == "repo_git")
    assert "-e clear_cache -e invalidate_cache" in tool.description
