"""C4 (corrections-fixes): with [mind] on, GOAT decides on its own what is worth keeping and updates a corrected L1
fact; no explicit „ține minte” is needed. The off path's guidance is the orchestrator's, untouched."""
from __future__ import annotations

import re

from orchestrator import mind_hook
from orchestrator.orchestrator import _PROMOTE_MEMORY_GUIDANCE, _STORE_MEMORY_GUIDANCE

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


def test_off_path_guidance_is_not_the_mind_guidance():
    assert G not in (_STORE_MEMORY_GUIDANCE, _PROMOTE_MEMORY_GUIDANCE)
    assert "store_memory" in _STORE_MEMORY_GUIDANCE or "store" in _STORE_MEMORY_GUIDANCE.lower()
