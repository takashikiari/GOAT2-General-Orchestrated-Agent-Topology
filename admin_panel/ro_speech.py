"""admin_panel.ro_speech — Romanian text made ready for Deepgram's Flux text-to-speech.

Flux's Romanian voices (Early Access) do not normalise text: digits, times, dates and percentages are not read
properly. ``normalize`` writes them out in words before the text is sent. It covers whole numbers up to 999 999 999,
decimals, percentages, ``HH:MM`` times, ``DD.MM.YYYY`` and ``YYYY-MM-DD`` dates; anything else is left as it is.

A 1 or a 2 right before a word takes that word's gender, guessed from its ending ("1 fișier" → "un fișier", "1 eroare"
→ "o eroare", "2 erori" → "două erori"). Limit: "2" before a masculine person ("2 băieți") is still read "două".
"""
from __future__ import annotations

import re

__all__ = ["normalize", "number_to_words"]

_UNITS = [
    "zero", "unu", "doi", "trei", "patru", "cinci", "șase", "șapte", "opt", "nouă", "zece", "unsprezece",
    "doisprezece", "treisprezece", "paisprezece", "cincisprezece", "șaisprezece", "șaptesprezece", "optsprezece",
    "nouăsprezece",
]
_TENS = {2: "douăzeci", 3: "treizeci", 4: "patruzeci", 5: "cincizeci", 6: "șaizeci", 7: "șaptezeci", 8: "optzeci", 9: "nouăzeci"}
_MONTHS = [
    "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie", "august", "septembrie", "octombrie",
    "noiembrie", "decembrie",
]


def _under_100(n: int, gender: str = "m") -> str:
    """Gender of 1 and 2: "m" unu/doi (plain counting), "f" una/două (mii), "n" unu/două (milioane)."""
    if n < 20:
        if n == 1 and gender == "f":
            return "una"
        if n == 2 and gender in ("f", "n"):
            return "două"
        return _UNITS[n]
    tens, unit = divmod(n, 10)
    return _TENS[tens] if unit == 0 else f"{_TENS[tens]} și {_under_100(unit, gender)}"


def _under_1000(n: int, gender: str = "m") -> str:
    hundreds, rest = divmod(n, 100)
    parts: list[str] = []
    if hundreds:
        parts.append("o sută" if hundreds == 1 else f"{_under_100(hundreds, 'f')} sute")
    if rest:
        parts.append(_under_100(rest, gender))
    return " ".join(parts)


def _counted(n: int, singular: str, plural: str, gender: str) -> str:
    """``n`` thousands / millions: "o mie", "două mii", "douăzeci de mii" (the "de" comes from 20 up, and at round hundreds)."""
    if n == 1:
        return singular
    link = " de " if n % 100 == 0 or n % 100 >= 20 else " "
    return f"{_under_1000(n, gender)}{link}{plural}"


def number_to_words(n: int) -> str:
    """0..999 999 999 in Romanian words; larger numbers come back as digits."""
    if n < 0 or n > 999_999_999:
        return str(n)
    if n == 0:
        return "zero"
    millions, rest = divmod(n, 1_000_000)
    thousands, units = divmod(rest, 1000)
    parts: list[str] = []
    if millions:
        parts.append(_counted(millions, "un milion", "milioane", "n"))
    if thousands:
        parts.append(_counted(thousands, "o mie", "mii", "f"))
    if units:
        parts.append(_under_1000(units))
    return " ".join(parts)


def _digits(s: str) -> str:
    """The fractional part of a decimal: up to two digits as a number ("05" → "zero cinci"), longer ones digit by digit."""
    if len(s) > 2:
        return " ".join(_UNITS[int(c)] for c in s)
    stripped = s.lstrip("0")
    zeros = " ".join(["zero"] * (len(s) - len(stripped)))
    return " ".join(p for p in (zeros, number_to_words(int(stripped)) if stripped else "") if p)


def _plain_int(s: str) -> str:
    """An integer written with dots as thousand separators ("1.234") or none."""
    if len(s) > 1 and s.startswith("0"):
        return " ".join(_UNITS[int(c)] for c in s)  # 007, phone-like codes: digit by digit
    return number_to_words(int(s))


_DATE = re.compile(r"(?<![\d.])(\d{1,2})[./](\d{1,2})[./](\d{4})(?![\d.])")
_ISO_DATE = re.compile(r"(?<![\d-])(\d{4})-(\d{2})-(\d{2})(?![\d-])")
_TIME = re.compile(r"(?<![\d:.])(\d{1,2}):(\d{2})(?![\d:])")
_PERCENT = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*%")
_DECIMAL = re.compile(r"(?<![\w.,/:-])(\d+)(?:,(\d+)|\.(\d{1,2}))(?![\w-]|[.,]\d)")
_THOUSANDS = re.compile(r"(?<![\w.,/:-])\d{1,3}(?:\.\d{3})+(?![\w.,/:-])")
_INTEGER = re.compile(r"(?<![\w.,/:-])\d+(?![\w/:-]|[.,]\d)")
_ONE_TWO = re.compile(r"(?<![\w.,/:-])([12])[ ]+([^\W\d_]+)(?![\w/:-]|[.,]\d)")
# words after a 1 or a 2 that do not tell its gender ("între 1 și 2", "1 sau 2", "1 la 1")
_LINKS = {
    "și", "sau", "la", "din", "de", "cu", "în", "pe", "până", "dar", "iar", "ori", "a", "ca", "față", "spre", "către",
    "e", "este", "sunt", "nu", "au", "era", "va", "să", "se", "mai", "ar", "fi", "are", "am",
}
# feminine singular nouns that do not end in -ă / -e, and neuter ones that end in -e
_FEMININE = {"zi", "noapte", "sesiune"}
_NEUTER_IN_E = {"nume", "munte", "părinte", "frate", "câine"}


def _date(m: re.Match[str]) -> str:
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return m.group(0)
    return f"{'întâi' if day == 1 else number_to_words(day)} {_MONTHS[month - 1]} {number_to_words(year)}"


def _time(m: re.Match[str]) -> str:
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return m.group(0)
    return f"ora {number_to_words(hour)}" + (f" și {number_to_words(minute)}" if minute else "")


def _decimal_words(whole: str, frac: str) -> str:
    return f"{number_to_words(int(whole))} virgulă {_digits(frac)}"


def _iso_date(m: re.Match[str]) -> str:
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return m.group(0)
    return f"{'întâi' if day == 1 else number_to_words(day)} {_MONTHS[month - 1]} {number_to_words(year)}"


def _one_or_two(m: re.Match[str]) -> str:
    """"1 fișier" → "un fișier", "1 eroare" → "o eroare", "2 erori" → "două erori"; before a link word, left for the plain pass."""
    number, word = m.group(1), m.group(2)
    if word.lower() in _LINKS:
        return m.group(0)
    if number == "2":
        return f"două {word}"
    feminine = word.lower() in _FEMININE or (word.lower().endswith(("ă", "e")) and word.lower() not in _NEUTER_IN_E)
    return f"{'o' if feminine else 'un'} {word}"


_CONTRACTED = re.compile(r"\b(în|din) (un|o)\b(?= )", re.IGNORECASE)


def _contract(m: re.Match[str]) -> str:
    """"în un fișier" → "într-un fișier", "din o zi" → "dintr-o zi" (what a 1 turned into after "în" or "din")."""
    return f"{'într' if m.group(1).lower() == 'în' else 'dintr'}-{m.group(2)}"


def _percent(m: re.Match[str]) -> str:
    value = m.group(1)
    sep = "," if "," in value else "."
    if sep in value and not re.fullmatch(r"\d{1,3}(?:\.\d{3})+", value):
        whole, frac = value.split(sep)
        return f"{_decimal_words(whole, frac)} la sută"
    return f"{_plain_int(value.replace('.', ''))} la sută"


def normalize(text: str) -> str:
    """``text`` with dates, times, percentages, decimals and numbers written in words."""
    text = _DATE.sub(_date, text)
    text = _ISO_DATE.sub(_iso_date, text)
    text = _TIME.sub(_time, text)
    text = _PERCENT.sub(_percent, text)
    text = _DECIMAL.sub(lambda m: _decimal_words(m.group(1), m.group(2) or m.group(3)), text)
    text = _THOUSANDS.sub(lambda m: number_to_words(int(m.group(0).replace(".", ""))), text)
    text = _CONTRACTED.sub(_contract, _ONE_TWO.sub(_one_or_two, text))
    return _INTEGER.sub(lambda m: _plain_int(m.group(0)), text)
