"""The understanding step: one LLM call reads a finished turn (classify it, link it to what was in mind) or an
incoming message (extract the cues that light memory). Every failure returns None; output is validated hard."""
from __future__ import annotations

import json
import re
from typing import Any

from config import settings
from utils.logging.setup import get_logger

log = get_logger(__name__)

TURN_TYPES = ("fapt", "eveniment", "decizie", "promisiune", "preferință", "întrebare", "corectură")
LINK_KINDS = ("continuă", "cauză", "urmare", "corectează", "contrazice")
OPEN_KINDS = ("promisiune", "întrebare")
_GIST_MAX = 160
_NAME_WORDS = 4  # a name is at most this many words; a longer string is a phrase
_PLACEHOLDER = re.compile(r"\{(in_mind|things|user|reply|text|facts|name)\}")

TURN_PROMPT = """Ești partea din memoria lui GOAT care înțelege o conversație. Primești o rundă încheiată \
(mesajul utilizatorului și răspunsul lui GOAT) și lista elementelor care erau „în minte" în acel moment, \
câte unul pe linie, în forma `id — tip — conținut`.

Răspunde cu UN SINGUR obiect JSON, fără text în afara lui, cu câmpurile:
- "type": tipul rundei, exact unul dintre: fapt, eveniment, decizie, promisiune, preferință, întrebare, corectură.
- "importance": număr între 0 și 1 (cât de important e de ținut minte).
- "gist": o singură propoziție în română care rezumă runda.
- "about": lista lucrurilor despre care e runda, fiecare un nume canonic de 1-3 cuvinte: o persoană, un \
proiect, o tehnologie, un loc sau un obiect (de ex. "Render", "PetVoyageUK", "Kent"), niciodată o frază sau un \
substantiv abstract. Dacă te referi la ceva din lista „Lucruri cunoscute", folosește exact eticheta de acolo.
- "links": lista de legături cu elemente din minte, fiecare {"to": id, "kind": tip}. Leagă DOAR de id-urile \
din listă. "kind" e exact unul dintre: continuă, cauză, urmare, corectează, contrazice.
- "opens": dacă runda deschide ceva rămas de făcut, {"kind": "promisiune" sau "întrebare", "text": "..."}, \
altfel null.
- "closes": lista de id-uri ale elementelor de tip deschis din listă pe care runda le încheie (poate fi goală).

În minte:
{in_mind}

Lucruri cunoscute (etichete existente):
{things}

Utilizator: {user}
GOAT: {reply}
"""

MESSAGE_PROMPT = """Ești partea din memoria lui GOAT care extrage indicii dintr-un mesaj primit, ca să \
știe ce amintiri să trezească. Primești mesajul și lista elementelor care sunt deja „în minte", câte unul \
pe linie, în forma `id — tip — conținut`.

Răspunde cu UN SINGUR obiect JSON, fără text în afara lui, cu câmpurile:
- "about": lista de etichete scurte (nume, subiecte, lucruri) la care se referă mesajul.
- "names": lista lucrurilor concrete numite în mesaj: persoane, locuri, proiecte, produse, obiecte (de ex. \
"Kent", "Render", "Bucegi"), fiecare de cel mult 4 cuvinte; niciodată substantive abstracte („omisiune", \
„observație") sau fraze. Listă goală dacă nu e niciunul.
- "asks_past": true dacă mesajul întreabă despre ceva din trecut (ce s-a spus, ce s-a decis, ce s-a întâmplat), \
altfel false.
- "facts": lista cheilor din „Ce știi" de care e nevoie pentru mesaj (mesajul e despre acea persoană, acel \
loc, acea mașină sau acel proiect), exact cum apar acolo. Listă goală dacă nu e nevoie de niciuna.

În minte:
{in_mind}

Ce știi (cheie — conținut):
{facts}

Mesaj: {text}
"""


def _mind_lines(in_mind: list[dict]) -> str:
    lines = []
    for it in in_mind or []:
        body = str(it.get("text") or it.get("gist") or it.get("why") or "")[:_GIST_MAX].replace("\n", " ")
        lines.append(f"{it.get('item')} — {it.get('kind')} — {body}")
    return "\n".join(lines) or "(nimic)"


def _first_json(content: str) -> dict | None:
    """The first balanced ``{...}`` block of the content, parsed; None if absent or invalid."""
    start = content.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(content)):
            ch = content[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(content[start:i + 1])
                    except ValueError:
                        break
                    return obj if isinstance(obj, dict) else None
        start = content.find("{", start + 1)
    return None


def _fill(template: str, **values: str) -> str:
    """``template`` with its placeholders replaced in ONE pass: a "{reply}" inside the user's text stays as typed."""
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


async def _ask(client: Any, prompt: str, max_tokens: int = 4000) -> dict | None:
    try:
        resp = await client.chat.completions.create(
            model=settings.MODEL_NAME, messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens)
        choice = resp.choices[0]
        content = choice.message.content or ""
    except Exception as exc:  # noqa: BLE001 - any client failure means "no understanding"
        log.warning("understand: LLM call failed: %s", exc)
        return None
    obj = _first_json(content)
    if obj is None:
        log.warning("understand: no JSON in reply (finish_reason=%s)", getattr(choice, "finish_reason", None))
    return obj


def _labels(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [s.strip() for s in raw if isinstance(s, str) and s.strip()]


def _names(raw: Any) -> list[str]:
    return [s for s in _labels(raw) if len(s.split()) <= _NAME_WORDS]


def _importance(raw: Any) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.5
    return max(0.0, min(1.0, float(raw)))


def _clean_turn(obj: dict, in_mind: list[dict]) -> dict:
    shown = {str(it.get("item")) for it in in_mind or []}
    open_ids = {str(it.get("item")) for it in in_mind or [] if it.get("kind") == "deschis"}
    links = []
    for ln in obj.get("links") if isinstance(obj.get("links"), list) else []:
        if isinstance(ln, dict) and str(ln.get("to")) in shown and ln.get("kind") in LINK_KINDS:
            links.append({"to": str(ln["to"]), "kind": ln["kind"]})
    op = obj.get("opens")
    opens = None
    if isinstance(op, dict) and op.get("kind") in OPEN_KINDS and isinstance(op.get("text"), str) \
            and op["text"].strip():
        opens = {"kind": op["kind"], "text": op["text"].strip()}
    raw_closes = obj.get("closes") if isinstance(obj.get("closes"), list) else []
    gist = obj.get("gist")
    return {"type": obj.get("type") if obj.get("type") in TURN_TYPES else "eveniment",
            "importance": _importance(obj.get("importance")),
            "gist": gist.strip() if isinstance(gist, str) else "",
            "about": _labels(obj.get("about")), "links": links, "opens": opens,
            "closes": [str(c) for c in raw_closes if str(c) in open_ids]}


async def understand_turn(client: Any, user: str, reply: str, in_mind: list[dict],
                          things: list[str] | None = None) -> dict | None:
    """Classify a finished turn and link it to the items that were in mind; ``things`` are the existing thing
    labels its ``about`` should reuse (R28). None on any failure. A reasoning model needs room: 8000 tokens (R34)."""
    known = "\n".join(f"- {t}" for t in (things or [])[:30]) or "(niciunul)"
    prompt = _fill(TURN_PROMPT, in_mind=_mind_lines(in_mind), things=known, user=user or "", reply=reply or "")
    obj = await _ask(client, prompt, max_tokens=8000)
    return None if obj is None else _clean_turn(obj, in_mind)


async def understand_message(client: Any, text: str, in_mind: list[dict],
                             knowledge: dict | None = None) -> dict | None:
    """Extract the memory cues of an incoming message; ``knowledge`` are the L1 knowledge facts it may need, of
    which ``facts`` names the keys it is about (only keys shown). None on any failure."""
    known = "\n".join(f"{k} — {str(v)[:120]}".replace("\n", " ") for k, v in (knowledge or {}).items())
    obj = await _ask(client, _fill(MESSAGE_PROMPT, in_mind=_mind_lines(in_mind), facts=known or "(nimic)",
                                   text=text or ""))
    if obj is None:
        return None
    return {"about": _labels(obj.get("about")), "names": _names(obj.get("names")),
            "asks_past": obj.get("asks_past") is True,
            "facts": [k for k in dict.fromkeys(_labels(obj.get("facts"))) if k in (knowledge or {})]}
