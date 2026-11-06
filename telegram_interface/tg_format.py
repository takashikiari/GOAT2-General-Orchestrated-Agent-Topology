"""Render GOAT's Markdown replies as Telegram HTML (parse_mode="HTML").

Telegram shows a plain-text message verbatim, so GOAT's `**bold**`, backticks and Markdown tables
arrived as raw markup. This converts the Markdown GOAT writes into the small HTML subset Telegram
supports (b, i, s, code, pre, a, blockquote), turns tables into phone-friendly bullet rows, splits
long replies at block boundaries instead of cutting them, and falls back to plain text if Telegram
rejects the HTML.
"""
import html
import logging
import re

from markdown_it import MarkdownIt

log = logging.getLogger(__name__)

MAX_LEN = 4096  # Telegram's hard message-length limit
SAFE_LINK = ("http://", "https://", "tg://", "mailto:")
_md = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")
_TAG = re.compile(r"<[^>]+>")
_INLINE = {"strong_open": "<b>", "strong_close": "</b>", "em_open": "<i>", "em_close": "</i>",
           "s_open": "<s>", "s_close": "</s>", "softbreak": "\n", "hardbreak": "\n"}


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _inline(tokens) -> str:
    out, links = [], []
    for t in tokens or []:
        if t.type in _INLINE:
            out.append(_INLINE[t.type])
        elif t.type == "code_inline":
            out.append(f"<code>{_esc(t.content)}</code>")
        elif t.type == "link_open":
            href = str(t.attrGet("href") or "")
            ok = href.startswith(SAFE_LINK)
            links.append(ok)
            if ok:
                out.append(f'<a href="{html.escape(href, quote=True)}">')
        elif t.type == "link_close":
            if links and links.pop():
                out.append("</a>")
        else:  # text, image alt, html_inline (html is disabled, so it is text) ...
            out.append(_esc(t.content))
    return "".join(out)


def _bold(cell: str) -> str:
    return cell if cell.startswith("<b>") and cell.endswith("</b>") else f"<b>{cell}</b>"


def _table(tokens, i):
    """Collect a Markdown table starting at tokens[i]; return (rendered block, index after table_close)."""
    header, rows, row, in_head = [], [], None, False
    while tokens[i].type != "table_close":
        t = tokens[i]
        if t.type == "thead_open":
            in_head = True
        elif t.type == "thead_close":
            in_head = False
        elif t.type == "tr_open":
            row = []
        elif t.type == "tr_close":
            (header if in_head else rows).append(row)
        elif t.type == "inline":
            row.append(_inline(t.children).strip())
        i += 1
    labels = header[0] if header else []
    lines = []
    for r in rows:
        if not r:
            continue
        lines.append(f"• {_bold(r[0])}" if r[0] else "•")
        for label, value in zip(labels[1:], r[1:]):
            if value:
                lines.append(f"   <i>{label}:</i> {value}" if label else f"   {value}")
        for value in r[len(labels):] if len(r) > len(labels) > 0 else []:
            lines.append(f"   {value}")
    return "\n".join(lines), i + 1


def render_blocks(text: str) -> list[str]:
    """Markdown -> list of self-contained Telegram-HTML blocks (no tag spans two blocks)."""
    tokens = _md.parse(text or "")
    blocks, lists, items, prefix, quote = [], [], [], None, 0

    def add(chunk):
        nonlocal prefix
        if lists:
            lead = prefix if prefix is not None else "  " * len(lists)
            items.append(lead + chunk)
            prefix = None
        elif chunk.strip():
            blocks.append(f"<blockquote>{chunk}</blockquote>" if quote else chunk)

    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "heading_open":
            add(_bold(_inline(tokens[i + 1].children)))
            i += 3
            continue
        if t.type == "paragraph_open":
            add(_inline(tokens[i + 1].children))
            i += 3
            continue
        if t.type in ("bullet_list_open", "ordered_list_open"):
            start = int(t.attrGet("start") or 1) if t.type == "ordered_list_open" else 0
            lists.append([t.type == "ordered_list_open", start - 1])
        elif t.type == "list_item_open":
            top = lists[-1]
            top[1] += 1
            prefix = "  " * (len(lists) - 1) + (f"{top[1]}. " if top[0] else "• ")
        elif t.type in ("bullet_list_close", "ordered_list_close"):
            lists.pop()
            if not lists and items:
                blocks.append("\n".join(items))
                items = []
        elif t.type in ("fence", "code_block"):
            add(f"<pre>{_esc(t.content.rstrip())}</pre>")
        elif t.type == "hr":
            add("──────────")
        elif t.type == "blockquote_open":
            quote += 1
        elif t.type == "blockquote_close":
            quote -= 1
        elif t.type == "table_open":
            block, i = _table(tokens, i)
            add(block)
            continue
        i += 1
    if items:
        blocks.append("\n".join(items))
    return blocks


def plain(chunk_html: str) -> str:
    return html.unescape(_TAG.sub("", chunk_html))


def _pieces(text: str, limit: int = MAX_LEN) -> list[str]:
    out = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return out + ([text] if text else [])


def to_chunks(text: str, limit: int = MAX_LEN) -> list[str]:
    """Telegram-HTML messages, each <= limit, split only between blocks; an oversized block goes as plain text."""
    chunks, cur = [], ""
    for block in render_blocks(text):
        if len(block) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.extend(_esc(p) for p in _pieces(plain(block), limit - 200))
            continue
        joined = f"{cur}\n\n{block}" if cur else block
        if len(joined) > limit:
            chunks.append(cur)
            cur = block
        else:
            cur = joined
    if cur:
        chunks.append(cur)
    return chunks or [_esc(text or "")[:limit]]


async def send(send_fn, text: str) -> None:
    """Send `text` via `send_fn(chunk, parse_mode)`; rejected HTML falls back to plain text for what is left."""
    from telegram.error import BadRequest

    try:
        chunks = to_chunks(text)
    except Exception:  # noqa: BLE001 - formatting must never cost the reply
        log.exception("tg_format: render failed, sending plain text")
        chunks = None
    if chunks:
        for n, chunk in enumerate(chunks):
            try:
                await send_fn(chunk, "HTML")
            except BadRequest as exc:
                log.warning("tg_format: Telegram rejected HTML (%s), sending the rest as plain text", exc)
                for rest in chunks[n:]:
                    for piece in _pieces(plain(rest)):
                        await send_fn(piece, None)
                return
        return
    for piece in _pieces(text or ""):
        await send_fn(piece, None)
