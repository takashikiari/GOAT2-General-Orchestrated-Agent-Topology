"""tests.test_ro_speech — numbers, dates, times and percentages written in Romanian words for Flux TTS."""
from __future__ import annotations

import pytest

from admin_panel.ro_speech import normalize, number_to_words


@pytest.mark.parametrize(
    "n, words",
    [
        (0, "zero"), (1, "unu"), (12, "doisprezece"), (21, "douăzeci și unu"), (99, "nouăzeci și nouă"),
        (100, "o sută"), (200, "două sute"), (999, "nouă sute nouăzeci și nouă"),
        (1000, "o mie"), (2000, "două mii"), (12000, "doisprezece mii"), (20000, "douăzeci de mii"),
        (21000, "douăzeci și una de mii"), (100000, "o sută de mii"), (2026, "două mii douăzeci și șase"),
        (1_000_000, "un milion"), (2_000_000, "două milioane"), (22_000_000, "douăzeci și două de milioane"),
    ],
)
def test_whole_numbers(n, words):
    assert number_to_words(n) == words


def test_numbers_beyond_the_range_stay_digits():
    assert number_to_words(1_000_000_000) == "1000000000"


@pytest.mark.parametrize(
    "text, spoken",
    [
        ("Am găsit 3 erori.", "Am găsit trei erori."),
        ("20% din cereri", "douăzeci la sută din cereri"),
        ("rata e 3,5%", "rata e trei virgulă cinci la sută"),
        ("12,05 lei", "doisprezece virgulă zero cinci lei"),
        ("pi e 3,14159", "pi e trei virgulă unu patru unu cinci nouă"),
        ("0.5 secunde", "zero virgulă cinci secunde"),
        ("la 14:30", "la ora paisprezece și treizeci"),
        ("la 09:00", "la ora nouă"),
        ("pe 08.11.2026", "pe opt noiembrie două mii douăzeci și șase"),
        ("pe 1.12.2026", "pe întâi decembrie două mii douăzeci și șase"),
        ("1.234 de linii", "o mie două sute treizeci și patru de linii"),
        ("2.500.000 de utilizatori", "două milioane cinci sute de mii de utilizatori"),
        ("cod 007", "cod zero zero șapte"),
    ],
)
def test_normalize_writes_numbers_in_words(text, spoken):
    assert normalize(text) == spoken


@pytest.mark.parametrize("text", ["GPT-4 și v2.5", "IP 10.0.0.1", "fișierul log2.txt", "raport 1/2", "ora 25:61", "nimic de schimbat aici"])
def test_things_that_are_not_plain_numbers_are_left_alone(text):
    assert normalize(text) == text
