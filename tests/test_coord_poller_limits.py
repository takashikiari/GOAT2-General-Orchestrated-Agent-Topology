import json

from telegram_interface import _coord_poller as p


def _fake(monkeypatch, msgs):
    out = json.dumps({"messages": msgs, "last_id": msgs[-1]["id"] if msgs else None})
    monkeypatch.setattr(p, "_cli", lambda *a: (0, out, ""))


def test_long_message_kept_up_to_text_max():
    line = p._line({"from": "a/b/c", "to": "lenovo/goat", "text": "x" * 3000})
    assert "x" * 3000 in line


def test_digest_budget_defers_the_rest_without_acking_it(monkeypatch):
    msgs = [{"id": f"{i}-0", "from": "a/b/c", "to": "lenovo/goat", "text": "y" * 3500} for i in range(1, 8)]
    _fake(monkeypatch, msgs)
    got, last = p._fetch("lenovo/goat/superv")
    assert 1 <= len(got) < len(msgs) and last == got[-1]["id"]


def test_small_batch_untouched(monkeypatch):
    msgs = [{"id": f"{i}-0", "from": "a/b/c", "to": "lenovo/goat", "text": "z"} for i in range(1, 4)]
    _fake(monkeypatch, msgs)
    got, last = p._fetch("lenovo/goat/superv")
    assert got == msgs and last == "3-0"


def test_one_huge_message_still_delivered(monkeypatch):
    _fake(monkeypatch, [{"id": "9-0", "from": "a/b/c", "to": "lenovo/goat", "text": "w" * 50000}])
    got, last = p._fetch("lenovo/goat/superv")
    assert len(got) == 1 and last == "9-0"
