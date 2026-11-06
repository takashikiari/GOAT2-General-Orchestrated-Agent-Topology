import asyncio

import pytest
from telegram.error import BadRequest

from telegram_interface import tg_format as f

SAMPLE = """**Verificat live (08:32).** `laris/petvoyageuk/06774c` e **`working`** chiar acum:

- `08:28:06` git → `08:31:59` **chrome-devtools navigate_page**
- **Niciun mesaj pe canal** de la ea.

**Ce am găsit eu, read-only, pe branch-uri:**

| branch | față de `origin/main` | observație |
|---|---|---|
| `fix/hreflang-only-translated-pages` | **0** commit-uri în plus | **candidat clar de șters** |
| `mobile-app` | ahead 53 / behind 43 | NU e de șters |

**De aici:** cele două branch-uri <sigure> & curate."""


def html():
    return "\n\n".join(f.to_chunks(SAMPLE))


def test_markdown_becomes_telegram_html():
    out = html()
    assert "**" not in out and "|---" not in out and "`" not in out
    assert "<b>Verificat live (08:32).</b>" in out and "<code>laris/petvoyageuk/06774c</code>" in out
    assert "• <code>08:28:06</code> git" in out


def test_table_rows_become_bullets_with_labels():
    out = html()
    assert "• <b><code>fix/hreflang-only-translated-pages</code></b>" in out
    assert "<i>observație:</i> <b>candidat clar de șters</b>" in out
    assert "<i>față de <code>origin/main</code>:</i> ahead 53 / behind 43" in out


def test_html_is_escaped_and_unsafe_links_dropped():
    out = "\n".join(f.to_chunks("a <script>x</script> & [ok](https://e.com) [bad](javascript:alert(1))"))
    assert "&lt;script&gt;" in out and "&amp;" in out
    assert '<a href="https://e.com">ok</a>' in out and 'href="javascript' not in out and "bad" in out
    assert "&lt;sigure&gt; &amp; curate" in html()


def test_code_fence_and_headings():
    out = "\n".join(f.to_chunks("# Titlu\n\n```\nx < 1\n```"))
    assert "<b>Titlu</b>" in out and "<pre>x &lt; 1</pre>" in out


def test_long_reply_split_between_blocks_never_cut():
    text = "\n\n".join(f"**Paragraf {n}** " + "x" * 500 for n in range(30))
    chunks = f.to_chunks(text)
    assert len(chunks) > 1 and all(len(c) <= f.MAX_LEN for c in chunks)
    assert all(c.count("<b>") == c.count("</b>") for c in chunks)
    assert "Paragraf 29" in chunks[-1]


def test_send_uses_html_then_falls_back_to_plain_on_rejection():
    sent = []

    async def ok(text, mode):
        sent.append((mode, text))

    asyncio.run(f.send(ok, SAMPLE))
    assert sent and all(m == "HTML" for m, _ in sent)

    sent.clear()

    async def picky(text, mode):
        if mode == "HTML":
            raise BadRequest("can't parse entities")
        sent.append((mode, text))

    asyncio.run(f.send(picky, "**a** `b`"))
    assert sent == [(None, "a b")]


def test_empty_and_plain_text():
    assert f.to_chunks("") == [""]
    assert f.to_chunks("salut, totul e ok") == ["salut, totul e ok"]


def test_markdown_it_is_quiet_in_the_log_file():
    from utils.logging import setup
    assert "markdown_it" in setup._QUIET_LIBS


def test_channel_digest_never_takes_the_temporal_route():
    from datetime import datetime
    from memory.temporal_route import CHANNEL_DIGEST_PREFIX, parse_interval
    from telegram_interface._coord_poller import format_digest
    now = datetime(2026, 11, 6, 12, 0)
    assert format_digest([]).startswith(CHANNEL_DIGEST_PREFIX)
    msg = {"from": "lenovo/a/b", "to": "lenovo/goat", "text": "PR #6 live, GOAT repornit 09:20, 4000/1500"}
    assert parse_interval(format_digest([msg]), now=now) is None
    assert parse_interval("[2026-11-06 09:14:04] lenovo/a -> lenovo/goat: salut", now=now) is None
    assert parse_interval("ce am făcut ieri?", now=now) is not None
