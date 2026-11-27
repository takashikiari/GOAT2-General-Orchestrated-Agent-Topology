"""C4 (corrections-fixes): with [mind] on, GOAT decides on its own what is worth keeping and updates a corrected L1
fact; no explicit „ține minte” is needed. The off path's guidance is the orchestrator's, untouched."""
from __future__ import annotations

import re

from orchestrator import mind_hook

G = mind_hook.MIND_GUIDANCE


def test_no_explicit_request_restriction():
    assert "explicit" not in G and "doar când Laris" not in G and "ține minte" not in G


def test_keeps_the_memory_and_no_search_sentences():
    assert "Blocul [În minte] de mai sus este memoria ta" in G
    assert "spune simplu că nu-ți amintești; nu inventa" in G
    assert "Nu căuta niciodată în memorie cu unelte, cu shell-ul sau prin fișiere" in G


def test_decides_on_its_own_and_updates_corrected_facts():
    assert "singur" in G and "fără să ceri confirmare" in G
    for word in ("regulă", "preferință", "promote_memory", "store_memory", "forget_fact", "corectează"):
        assert word in G
    assert "aceeași cheie" in G


def test_short():
    assert len([s for s in re.split(r"(?<=[.;])\s+(?=[A-ZĂÂÎȘȚ])", G) if s.strip()]) <= 5

# The off path (no MIND_GUIDANCE, the old store/promote/search guidance present) is covered end to end by
# tests/test_orchestrator_mind.py::test_off_prompt_and_tools_unchanged.


def test_forget_fact_takes_the_key_as_shown_in_memory_not_via_read_l1():
    from tools.memory_manager import build_memory_manager_tools
    desc = next(t.description for t in build_memory_manager_tools(None) if t.name == "forget_fact")
    assert "read_l1" not in desc and "[În minte]" in desc
