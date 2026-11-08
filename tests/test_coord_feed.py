"""tests.test_coord_feed — telling GOAT's agent-coord digest turns apart from the real conversation."""
from __future__ import annotations

from admin_panel import coord_feed
from memory.temporal_route import CHANNEL_DIGEST_PREFIX
from telegram_interface._coord_poller import format_digest
from tools.agent_coord_cli import one_line


def _msg(frm, to, text, **extra):
    return {"id": "1-0", "from": frm, "to": to, "text": text, **extra}


def _entry(role, content, ts=1.0):
    return {"role": role, "content": content, "timestamp": ts, "l3_id": "x"}


def test_is_digest_only_for_user_entries_that_start_with_the_channel_prefix():
    assert coord_feed.is_digest(_entry("user", format_digest([_msg("a/b/c", "x", "hi")])))
    assert coord_feed.is_digest(_entry("user", CHANNEL_DIGEST_PREFIX + ", ..."))
    assert not coord_feed.is_digest(_entry("assistant", CHANNEL_DIGEST_PREFIX + ", ..."))
    assert not coord_feed.is_digest(_entry("user", "salut"))
    assert not coord_feed.is_digest(_entry("user", "Mesaje noi pe Telegram"))
    assert not coord_feed.is_digest({"role": "user"})


def test_parse_digest_recovers_what_the_poller_formatted():
    text = format_digest([
        _msg("lenovo/agent-coord/ee1c5e", "lenovo/goat", "Salut\n  linie două,   cu spații"),
        _msg("lenovo/goat/watchdog", "lenovo/goat/superv", "💤 inactivă de 30 min", kind="system"),
        _msg("laris/home/96ed8e", "lenovo/goat", "x", origin_host="alt-host"),
    ])
    parsed = coord_feed.parse_digest(text)
    assert [(p["from"], p["to"]) for p in parsed] == [
        ("lenovo/agent-coord/ee1c5e", "lenovo/goat"),
        ("lenovo/goat/watchdog", "lenovo/goat/superv"),
        ("laris/home/96ed8e", "lenovo/goat"),
    ]
    # the poller flattens a message to one line (newlines become a marker); the page shows what GOAT was shown
    assert parsed[0]["text"] == one_line("Salut\n  linie două,   cu spații", 4000) and parsed[0]["alert"] is False
    assert parsed[1]["alert"] is True and parsed[1]["text"] == "💤 inactivă de 30 min"
    assert parsed[2]["origin_warning"] == "alt-host" and parsed[0]["origin_warning"] is None


def test_parse_digest_ignores_the_fixed_header_and_footer_and_garbage():
    assert coord_feed.parse_digest(CHANNEL_DIGEST_PREFIX + ", fără mesaje") == []
    assert coord_feed.parse_digest("") == []


def test_the_conversation_is_split_into_chat_and_agent_turns():
    digest = format_digest([_msg("lenovo/goat/superv", "lenovo/goat", "ping")])
    messages = [
        _entry("user", "salut", 1), _entry("assistant", "bună", 2),
        _entry("user", digest, 3), _entry("assistant", "[Tool calls]\ncalled repo_git({})\n\nAm citit: ping.", 4),
        _entry("user", "ce faci?", 5), _entry("assistant", "bine", 6),
    ]
    chat, agent = coord_feed.split_conversation(messages)
    assert [m["content"] for m in chat] == ["salut", "bună", "ce faci?", "bine"]
    assert len(agent) == 1
    turn = agent[0]
    assert turn["timestamp"] == 3 and turn["reply_timestamp"] == 4
    assert turn["messages"][0]["text"] == "ping" and turn["messages"][0]["from"] == "lenovo/goat/superv"
    assert turn["goat_reply"] == "Am citit: ping." and turn["tools"] == "called repo_git({})"


def test_a_digest_without_a_reply_and_two_digests_in_a_row():
    d1 = format_digest([_msg("a/b/c", "lenovo/goat", "unu")])
    d2 = format_digest([_msg("d/e/f", "lenovo/goat", "doi")])
    chat, agent = coord_feed.split_conversation([_entry("user", "salut", 1), _entry("user", d1, 2), _entry("user", d2, 3), _entry("assistant", "ok", 4)])
    assert [m["content"] for m in chat] == ["salut"]
    assert [t["messages"][0]["text"] for t in agent] == ["unu", "doi"]
    assert agent[0]["goat_reply"] is None and agent[1]["goat_reply"] == "ok"


def test_a_reply_that_is_not_a_digest_reply_stays_in_the_chat():
    # an assistant entry that does not directly follow a digest belongs to the conversation
    chat, agent = coord_feed.split_conversation([_entry("assistant", "mesaj de la GOAT", 1), _entry("user", "salut", 2)])
    assert [m["content"] for m in chat] == ["mesaj de la GOAT", "salut"] and agent == []


def test_entries_without_content_do_not_break_it():
    chat, agent = coord_feed.split_conversation([{"role": "user"}, {"role": "assistant", "content": None}])
    assert len(chat) == 2 and agent == []


def test_split_tools_old_replies_with_blank_lines_inside_the_evidence():
    # stored before the summary was made one-line: a .gitignore preview had blank lines in it
    stored = ("[Tool calls]\ncalled repo_git({\"op\":\"show\"}) → # Virtual env\nvenv/\n\n# Logs\nlogs/\n"
              "called shell_run({}) → ok\n\nAm verificat repo-ul.\n\nTotul e în regulă.")
    tools, reply = coord_feed.split_tools(stored)
    assert reply == "Am verificat repo-ul.\n\nTotul e în regulă."
    assert tools.startswith("called repo_git") and tools.endswith("called shell_run({}) → ok")


def test_split_tools_one_line_evidence_and_no_evidence():
    assert coord_feed.split_tools("[Tool calls]\ncalled x({}) → a ⏎ b\n\nbună\n\nziua") == ("called x({}) → a ⏎ b", "bună\n\nziua")
    assert coord_feed.split_tools("doar un răspuns") == (None, "doar un răspuns")
    assert coord_feed.split_tools("[Tool calls]\ncalled x({}) → a") == ("called x({}) → a", "")
