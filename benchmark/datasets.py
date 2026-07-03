"""benchmark.datasets — built-in test datasets for benchmarking GOAT 2.0.

Each dataset is a list of test-case dicts exercising one aspect of the memory
pipeline. A test case describes a conversation to preload, a query to ask the
orchestrator, and the expected answer used to score the response. Datasets are
pure data with no imports from ``orchestrator`` or ``memory`` — the suite is
importable with no services running.

Base test-case schema (every field optional except ``id``, ``query``):
    id                str        unique within the dataset
    name              str        human label
    conversation      list[dict] user/assistant messages to preload into L2
    query             str        the question asked of the orchestrator
    expected          str        exact expected answer (for exact match)
    expected_contains list[str]  keywords that must appear in the response
    tags              list[str]  metadata for filtering / reporting

Runner extension fields (optional, consumed by ``BenchmarkRunner``):
    episodic_only     bool       preload into L3 only (forces prefetch path)
    repeat            int        ask the query N times (exercises L2.5 cache)
"""
from __future__ import annotations

from utils.logging.setup import get_logger

log = get_logger(__name__)


def _um(content: str) -> dict:
    """Shorthand for a preloaded user message."""
    return {"role": "user", "content": content}


def _am(content: str) -> dict:
    """Shorthand for a preloaded assistant message."""
    return {"role": "assistant", "content": content}


def _case(
    cid: str, name: str, conv: list[dict], query: str, *,
    expected: str | None = None, contains: list[str] | None = None,
    tags: list[str] | None = None, **extra,
) -> dict:
    """Build a test-case dict, omitting empty optional fields."""
    case = {"id": cid, "name": name, "conversation": conv, "query": query, "tags": tags or []}
    if expected is not None:
        case["expected"] = expected
    if contains is not None:
        case["expected_contains"] = contains
    case.update(extra)
    return case


def _memory_recall() -> list[dict]:
    """10 cases: recall a fact stated earlier in the preloaded conversation."""
    return [
        _case("mr-01", "favorite color",
              [_um("My favorite color is teal."), _am("Got it — teal is your favorite color.")],
              "What is my favorite color?", expected="teal", contains=["teal"],
              tags=["recall", "preference"]),
        _case("mr-02", "home city",
              [_um("I live in Oslo."), _am("Noted — you're based in Oslo.")],
              "Which city do I live in?", expected="Oslo", contains=["oslo"],
              tags=["recall", "location"]),
        _case("mr-03", "pet name",
              [_um("My dog's name is Biscuit."), _am("Biscuit — lovely name for a dog.")],
              "What is my dog's name?", expected="Biscuit", contains=["biscuit"],
              tags=["recall", "pet"]),
        _case("mr-04", "occupation",
              [_um("I work as a nurse."), _am("Understood, you're a nurse.")],
              "What is my job?", expected="nurse", contains=["nurse"],
              tags=["recall", "occupation"]),
        _case("mr-05", "favorite food",
              [_um("My favorite food is ramen."), _am("Ramen — great choice.")],
              "What is my favorite food?", expected="ramen", contains=["ramen"],
              tags=["recall", "food"]),
        _case("mr-06", "birthday month",
              [_um("My birthday is in April."), _am("April — I'll remember that.")],
              "In which month is my birthday?", expected="April", contains=["april"],
              tags=["recall", "birthday"]),
        _case("mr-07", "sibling name",
              [_um("My sister's name is Elena."), _am("Got it, your sister is Elena.")],
              "What is my sister's name?", expected="Elena", contains=["elena"],
              tags=["recall", "family"]),
        _case("mr-08", "car model",
              [_um("I drive a Honda Civic."), _am("A Honda Civic — reliable choice.")],
              "What car do I drive?", expected="Civic", contains=["civic"],
              tags=["recall", "car"]),
        _case("mr-09", "weekend hobby",
              [_um("On weekends I do pottery."), _am("Pottery — nice weekend hobby.")],
              "What do I do on weekends?", expected="pottery", contains=["pottery"],
              tags=["recall", "hobby"]),
        _case("mr-10", "home language",
              [_um("At home we speak Tagalog."), _am("Tagalog at home — understood.")],
              "Which language do I speak at home?", expected="Tagalog", contains=["tagalog"],
              tags=["recall", "language"]),
    ]


def _temporal() -> list[dict]:
    """Cases that recall a fact anchored to a time reference in the preloaded turn."""
    return [
        _case("tp-01", "meeting last Tuesday",
              [_um("Last Tuesday's meeting was about the budget review."),
               _am("Noted — budget review was the topic last Tuesday.")],
              "What was last Tuesday's meeting about?", expected="budget review",
              contains=["budget"], tags=["temporal", "meeting"]),
        _case("tp-02", "trip in March",
              [_um("In March I'm traveling to Lisbon."), _am("Lisbon in March — sounds great.")],
              "Where am I traveling in March?", expected="Lisbon", contains=["lisbon"],
              tags=["temporal", "travel"]),
        _case("tp-03", "errand yesterday",
              [_um("Yesterday I went to the post office."), _am("The post office — got it.")],
              "Where did I go yesterday?", expected="post office", contains=["post office"],
              tags=["temporal", "errand"]),
        _case("tp-04", "restaurant two weeks ago",
              [_um("Two weeks ago we ate at Sakura."), _am("Sakura — two weeks back.")],
              "Which restaurant did we visit two weeks ago?", expected="Sakura",
              contains=["sakura"], tags=["temporal", "restaurant"]),
        _case("tp-05", "course last summer",
              [_um("Last summer I took a scuba diving course."), _am("Scuba diving last summer — noted.")],
              "What course did I take last summer?", expected="scuba diving",
              contains=["scuba"], tags=["temporal", "course"]),
    ]


def _multi_turn() -> list[dict]:
    """Cases with a longer preloaded conversation where the fact is buried mid-thread."""
    return [
        _case("mt-01", "party date",
              [_um("Let's plan a party."), _am("Sure — when were you thinking?"),
               _um("How about the 14th?"), _am("The 14th works for me."),
               _um("Great, let's finalize the 14th."), _am("Locked in — the 14th it is.")],
              "What date did we pick for the party?", expected="14th", contains=["14"],
              tags=["multi_turn", "planning"]),
        _case("mt-02", "bug root cause",
              [_um("The login flow is flaky."), _am("Flaky how — intermittent failures?"),
               _um("Yes, a race condition between two requests."), _am("A race condition — that fits."),
               _um("Let's patch the race condition."), _am("On it.")],
              "What was the root cause of the login bug?", expected="race condition",
              contains=["race"], tags=["multi_turn", "engineering"]),
        _case("mt-03", "hotel name",
              [_um("I booked the vacation stay."), _am("Nice — where are you staying?"),
               _um("A place called the Blue Marlin."), _am("The Blue Marlin, lovely."),
               _um("It has a sea view."), _am("A sea view at the Blue Marlin — enjoy."),
               _um("Can't wait."), _am("Have a great trip.")],
              "Which hotel did I book?", expected="Blue Marlin", contains=["blue marlin"],
              tags=["multi_turn", "travel"]),
    ]


def _cache() -> list[dict]:
    """Cases asked twice (``repeat=2``) so the second turn hits the L2.5 search cache."""
    return [
        _case("ca-01", "phone model",
              [_um("My phone is a Pixel 8."), _am("Pixel 8 — noted.")],
              "What phone model do I have?", expected="Pixel 8", contains=["pixel"],
              tags=["cache", "device"], repeat=2),
        _case("ca-02", "coffee order",
              [_um("I always order an oat milk latte."), _am("Oat milk latte — got it.")],
              "What is my usual coffee order?", expected="oat milk latte",
              contains=["oat milk", "latte"], tags=["cache", "food"], repeat=2),
        _case("ca-03", "shoe size",
              [_um("My shoe size is 42."), _am("Size 42 — noted.")],
              "What is my shoe size?", expected="42", contains=["42"],
              tags=["cache", "size"], repeat=2),
        _case("ca-04", "favorite band",
              [_um("My favorite band is The National."), _am("The National — great choice.")],
              "What is my favorite band?", expected="The National", contains=["national"],
              tags=["cache", "music"], repeat=2),
    ]


def _prefetch() -> list[dict]:
    """Cases whose fact is stored in L3 only (``episodic_only``) — forces the prefetch path."""
    return [
        _case("pf-01", "project deadline",
              [_um("The project deadline is Friday."), _am("Friday deadline — noted.")],
              "When is the project deadline?", expected="Friday", contains=["friday"],
              tags=["prefetch", "work"], episodic_only=True),
        _case("pf-02", "doctor appointment",
              [_um("My doctor appointment is at 9am."), _am("9am appointment — got it.")],
              "What time is my doctor appointment?", expected="9am", contains=["9"],
              tags=["prefetch", "appointment"], episodic_only=True),
        _case("pf-03", "recommended book",
              [_um("You recommended the book Dune."), _am("Dune — a classic worth reading.")],
              "Which book did you recommend?", expected="Dune", contains=["dune"],
              tags=["prefetch", "book"], episodic_only=True),
        _case("pf-04", "wifi password",
              [_um("The wifi password is coffee-mug-42."), _am("coffee-mug-42 — saved.")],
              "What is the wifi password?", expected="coffee-mug-42", contains=["coffee-mug-42"],
              tags=["prefetch", "credentials"], episodic_only=True),
    ]


def _build() -> dict[str, list[dict]]:
    """Assemble the dataset registry in stable registration order."""
    return {
        "memory_recall": _memory_recall(),
        "temporal": _temporal(),
        "multi_turn": _multi_turn(),
        "cache": _cache(),
        "prefetch": _prefetch(),
    }


_DATASETS: dict[str, list[dict]] = _build()


def list_datasets() -> list[str]:
    """Return the names of all built-in datasets, in registration order."""
    return list(_DATASETS)


def get_dataset(name: str) -> list[dict]:
    """Load a dataset by name; returns fresh copies so callers may mutate them.

    Raises:
        KeyError: when ``name`` is not a built-in dataset.
    """
    if name not in _DATASETS:
        raise KeyError(
            f"unknown dataset: {name!r} (available: {', '.join(list_datasets())})"
        )
    return [dict(case) for case in _DATASETS[name]]