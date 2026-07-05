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


def _facts(items: list[str]) -> list[dict]:
    """Alternating user/assistant preload from fact strings (generic ``Got it.`` acks)."""
    out: list[dict] = []
    for item in items:
        out += [_um(item), _am("Got it.")]
    return out


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


def _multi_hop() -> list[dict]:
    """Cases requiring the model to combine two preloaded facts to answer."""
    return [
        _case("mh-01", "country from city", [_um("I live in Oslo."), _am("Oslo, noted."), _um("Oslo is the capital of Norway."), _am("Noted.")], "What country do I live in?", expected="Norway", tags=["multi_hop", "geo"]),
        _case("mh-02", "sister's job", [_um("My sister's name is Elena."), _am("Elena, noted."), _um("Elena works as a doctor."), _am("A doctor, got it.")], "What does my sister do for a living?", expected="doctor", tags=["multi_hop", "family"]),
        _case("mh-03", "fern care", [_um("I bought a fern."), _am("A fern, nice."), _um("Ferns need shade to thrive."), _am("Shade, got it.")], "What light does my fern need?", expected="shade", tags=["multi_hop", "chain"]),
    ]


def _distractor() -> list[dict]:
    """Bury the target fact among ~8 others in L2; tests selective recall."""
    return [
        _case("di-01", "favorite number among facts",
              _facts(["I live in Oslo.", "I drive a Volvo.", "I have two kids.",
                      "My favorite number is 17.", "I was born in 1990.", "I like jazz.",
                      "My shoe size is 42.", "I work in finance."]),
              "What is my favorite number?", expected="17", tags=["distractor", "number"]),
        _case("di-02", "pet name among facts",
              _facts(["I drink tea.", "I run marathons.", "My pet is a parrot named Kiwi.",
                      "I live on the 3rd floor.", "I studied physics.", "I own a kayak.",
                      "I dislike cilantro.", "My birthday is in May."]),
              "What is my pet's name?", expected="Kiwi", tags=["distractor", "pet"]),
        _case("di-03", "meeting day among facts",
              _facts(["I take the bus.", "I play chess.", "My meeting is on Wednesday.",
                      "I have a blue coat.", "I eat oatmeal.", "I read sci-fi.",
                      "I own 3 guitars.", "I was born in March."]),
              "When is my meeting?", expected="Wednesday", tags=["distractor", "schedule"]),
    ]


def _distractor_15() -> list[dict]:
    """Target fact buried among 15 distractors stored in L3 (episodic_only).

    L3 path is forced because 15 distractors × 2 messages = 30 entries — beyond
    the L2 cap (20). Using episodic_only exercises the gap filter and blended
    score under a realistic long-conversation L3 load.
    """
    return [
        _case("d15-01", "secret word among 15 facts",
              _facts([
                  "I like hiking.", "I drive a Toyota.", "I drink oat milk.",
                  "I have three siblings.", "My secret word is MANGO.", "I was born in July.",
                  "I work remotely.", "My shoe size is 41.", "I play the violin.",
                  "I live near the sea.", "I read fantasy novels.", "I own a cat.",
                  "I studied economics.", "I prefer cold weather.", "I dislike mushrooms.",
              ]),
              "What is my secret word?", expected="MANGO", contains=["mango"],
              tags=["distractor", "d15"], episodic_only=True),
        _case("d15-02", "lucky number among 15 facts",
              _facts([
                  "I commute by train.", "I have a garden.", "I enjoy cooking pasta.",
                  "My lucky number is 33.", "I was born in Vienna.", "I own two bikes.",
                  "I speak three languages.", "I work in healthcare.", "I like sushi.",
                  "My morning routine starts at 6am.", "I play football on Sundays.",
                  "I have a standing desk.", "I prefer mountains to beaches.",
                  "I collect vintage maps.", "I dislike loud music.",
              ]),
              "What is my lucky number?", expected="33", contains=["33"],
              tags=["distractor", "d15"], episodic_only=True),
        _case("d15-03", "project codename among 15 facts",
              _facts([
                  "I use a mechanical keyboard.", "I drink two coffees a day.",
                  "I have a sister named Mia.", "I go to the gym on Tuesdays.",
                  "My project codename is FALCON.", "I live in a flat on the 5th floor.",
                  "I studied architecture.", "I own a labrador.", "I prefer evenings to mornings.",
                  "I have visited 12 countries.", "I keep a journal.", "I like board games.",
                  "I am allergic to pollen.", "I use Linux.", "I enjoy documentary films.",
              ]),
              "What is the codename of my project?", expected="FALCON", contains=["falcon"],
              tags=["distractor", "d15"], episodic_only=True),
    ]


def _distractor_20() -> list[dict]:
    """Target fact buried among 20 distractors stored in L3 (episodic_only).

    20 distractors × 2 messages = 40 entries — double the L2 cap. Exclusively
    exercises the L3 search path: gap filter calibration, blended-score ranking,
    and whether a single specific fact surfaces from a dense same-chat corpus.
    """
    return [
        _case("d20-01", "vault code among 20 facts",
              _facts([
                  "I wake up at 7am.", "I prefer tea to coffee.", "I have a blue bicycle.",
                  "My vault code is 8814.", "I read for 30 minutes every night.",
                  "I have lived in four countries.", "I dislike spicy food.",
                  "I have a degree in biology.", "I play chess on weekends.",
                  "My partner's name is Alex.", "I own a succulent plant.",
                  "I take the subway to work.", "I prefer action movies.",
                  "I have never broken a bone.", "I use a standing desk.",
                  "My first language is Spanish.", "I enjoy sailing.",
                  "I have two monitors.", "I prefer autumn to spring.", "I dislike crowds.",
              ]),
              "What is my vault code?", expected="8814", contains=["8814"],
              tags=["distractor", "d20"], episodic_only=True),
        _case("d20-02", "meeting room name among 20 facts",
              _facts([
                  "I commute by car.", "I have a parrot named Coco.", "I enjoy painting.",
                  "I studied physics.", "My meeting room is called Orion.",
                  "I wake up without an alarm.", "I have visited Japan twice.",
                  "I own a 3D printer.", "I prefer morning meetings.",
                  "I have a brother named Tom.", "I drink sparkling water.",
                  "I like minimalist design.", "I cycle on weekends.",
                  "I have a gym subscription.", "My native city is Lisbon.",
                  "I keep a plant on my desk.", "I prefer fiction to non-fiction.",
                  "I have an e-reader.", "I dislike video calls.", "I sleep 7 hours a night.",
              ]),
              "What is the name of my meeting room?", expected="Orion", contains=["orion"],
              tags=["distractor", "d20"], episodic_only=True),
        _case("d20-03", "emergency contact name among 20 facts",
              _facts([
                  "I have two cats.", "I enjoy trail running.", "I work in finance.",
                  "I have a standing desk.", "My emergency contact is Diana.",
                  "I studied abroad in Berlin.", "I prefer Linux over Windows.",
                  "I drink one litre of water before noon.", "I play the guitar.",
                  "I have lived in this city for eight years.", "I enjoy cooking Thai food.",
                  "I own a kayak.", "I read the news every morning.",
                  "I prefer hiking to swimming.", "I have a vintage watch collection.",
                  "My commute takes 25 minutes.", "I like escape rooms.",
                  "I have a podcast I listen to daily.", "I prefer cold climates.",
                  "I take notes by hand.",
              ]),
              "Who is my emergency contact?", expected="Diana", contains=["diana"],
              tags=["distractor", "d20"], episodic_only=True),
    ]


def _distractor_25() -> list[dict]:
    """Target fact among 25 distractors in L3 (episodic_only).

    25 distractors × 2 messages = 50 entries — 2.5× the L2 cap.
    Messages are multi-sentence paragraphs; the target fact is buried mid-conversation
    inside a natural paragraph rather than a single isolated statement.
    Three lexical decoys per case use the same semantic domain as the query
    (codes / passwords / project names) but give incorrect or vague values,
    stressing the gap filter under high lexical noise.
    Answers are arbitrary, non-dictionary codes that cannot be guessed.
    """
    # ── case 01: VEXKRELL — backup-server emergency unlock code ──────────────
    c01_pre = _facts([
        "I usually wake up around 6:30 and spend the first twenty minutes having coffee without looking at my phone. It helps me start the day without feeling rushed. I've been doing this for about three months now.",
        "I commute by train five days a week and it takes about forty minutes each way. I use the time to read or listen to podcasts so it doesn't feel wasted. The window seat on the upper deck is my favourite spot.",
        "My partner and I cook together most evenings and take turns picking the recipe. Last week we made a lamb tagine that turned out better than expected. We're slowly working through a cookbook we got as a gift.",
        "I've been learning Spanish for eight months using a language app. I practise for fifteen minutes every day, usually before bed. I can hold a basic conversation now but the subjunctive tense still trips me up.",
        "I renewed my gym membership last month after a long break. I go three times a week and focus on strength training. Morning sessions are quieter than evenings, which I strongly prefer.",
        "I finally got the updated door access code for the office after the building management changed the system last week. I had to tap the new combination on the keypad near reception to activate my card.",
        "I'm about halfway through a book on systems thinking that a colleague recommended. I find it genuinely useful for understanding how interconnected problems behave in large organisations.",
        "My home desk now has two monitors, which makes working on code much easier. I also got a new chair last month after my back started bothering me. Small improvements but they add up.",
        "My gym locker uses a combination lock and I kept forgetting the sequence, so I wrote it on a sticky note I keep in my wallet. I should really just memorize it properly.",
        "We're planning a short trip to the coast next month, just for a long weekend. I've been browsing rental apartments and one has a sea view which would be perfect. Nothing booked yet but I'm optimistic.",
    ])
    c01_target = [
        _um("I had a long meeting with the IT department this week to review the disaster recovery procedures for our server infrastructure. They walked me through the full failover sequence and made it clear I need to have the backup server's emergency unlock code memorised — it's VEXKRELL. It took a few attempts to commit it to memory but I feel better prepared now if something goes wrong."),
        _am("That's good preparation. Emergency codes are exactly the kind of thing you forget under pressure, so having it memorised matters."),
    ]
    c01_post = _facts([
        "I've started keeping a running list of things to fix around the flat. The kitchen tap has been dripping for weeks and I keep delaying calling a plumber. I need to sort it out this weekend.",
        "The access code for the shared storage room at work changed after the refurbishment last month. I had to ask the facilities coordinator for the new one — apparently only a handful of people have it.",
        "I eat lunch at my desk most days but I'm trying to break that habit. Eating away from the screen for twenty minutes is supposed to help afternoon focus.",
        "My houseplant collection has grown to seven plants. The fiddle-leaf fig is the hardest — it drops a leaf whenever I move it even slightly. The succulents practically take care of themselves.",
        "I've been meaning to update my CV for months. I keep adding things mentally but never sit down to write it. I'll try to do it over the weekend if I find a quiet hour.",
        "I played board games with friends on Saturday. We played a long strategy game that got quite competitive. Next time I want to try something cooperative instead.",
        "I've been tracking my sleep with my watch for the past month. I average about six and a half hours, which is less than I'd like. I'm trying to move my bedtime thirty minutes earlier.",
        "The bookshelf in my living room is completely full. I buy books faster than I read them. I should do a clear-out and donate some to the local library.",
        "My neighbour started renovating their flat and the morning noise is quite disruptive. I've been using noise-cancelling earphones when it gets bad. Hopefully it finishes in the next few weeks.",
        "I've been drinking more water by keeping a large bottle on my desk as a visual reminder. I try to finish it before lunch and refill for the afternoon. It's a small habit but I feel better for it.",
        "I'm thinking about switching to a mechanical keyboard. I tried one at a colleague's desk and liked the tactile feedback. The noise might be an issue in open-plan offices though.",
        "We have a team retrospective every two weeks to discuss what's going well and what isn't. Last session we agreed to change how we run sprint planning. The new format should cut meeting time by about a third.",
        "I finally sorted through the pile of old cables I've been accumulating. I threw most of them out because I couldn't identify what half were for. I kept the useful ones in a labelled box.",
        "I'm trying to check the news less in the evenings because I find it disrupts my sleep. I've limited myself to a morning and early-afternoon window. It's harder to stick to than I expected.",
        "I have a dentist appointment next month that I've already rescheduled twice. I really need to go this time. I always build it up as worse than it actually is.",
    ])
    case_01 = _case(
        "d25-01", "backup server unlock code among 25 facts",
        c01_pre + c01_target + c01_post,
        "What is the code I need to enter when the backup server fails to start?",
        expected="VEXKRELL", contains=["vexkrell"],
        tags=["distractor", "d25"], episodic_only=True,
    )

    # ── case 02: torrent-lamp-4471 — router admin panel password ─────────────
    c02_pre = _facts([
        "I've been sorting out the guest bedroom over the past few weekends. I repainted one wall and rearranged the furniture so it feels more spacious. It still needs curtains but it's a big improvement.",
        "I started doing a short meditation session in the mornings before I check my email. Even ten minutes makes a noticeable difference to how I handle the rest of the day.",
        "I reset my email account password after getting a suspicious login alert last Monday. I generated a random string and saved it in my password manager, so it's secure but I couldn't tell you what it is.",
        "I've been cooking more at home rather than ordering takeaway. It's cheaper and I feel better about what I'm eating. I've been working through some new recipes on weekends.",
        "The work VPN password changes every 90 days and the IT helpdesk sends a reminder email a week before expiry. I always update it the same day I get the reminder.",
        "I'm trying to reduce my screen time in the evenings by leaving my phone in another room an hour before bed. It's harder than it sounds but I've been more consistent lately.",
        "I've been cycling to work a couple of days a week when the weather allows. It adds about fifteen minutes compared to taking the train but I arrive feeling more energetic.",
    ])
    c02_target = [
        _um("I spent an afternoon reconfiguring my home network after noticing some connectivity slowdowns. I accessed the router admin panel to update the firmware and check the settings, and while I was in there I changed the admin panel password to something much stronger — it's torrent-lamp-4471, which I built using a three-word passphrase method with numbers. I wrote it in my offline notebook in case I ever need it again."),
        _am("That sounds like a sensible approach. A passphrase-style password is both strong and easier to recover from notes if needed."),
    ]
    c02_post = _facts([
        "I reorganised my bookshelf last weekend — sorted by genre and then alphabetically within each section. It took two hours but I'm much happier with how it looks.",
        "I changed my streaming service password last month because I suspected someone else was using my account without permission. Now only I know it.",
        "I had a long call with my parents on Sunday. They've been talking about visiting later in the year and I'm trying to figure out which dates work best.",
        "I've been going for a walk after dinner most evenings. Even twenty minutes at a gentle pace seems to help with digestion and I sleep better afterwards.",
        "I'm considering getting a smart home hub to control the lights and heating. I've been reading reviews and trying to decide if the setup complexity is worth the convenience.",
        "I submitted my expense report for the quarter — the process at work is a bit bureaucratic but straightforward once you know the right forms. It took about an hour to pull together the receipts.",
        "I've started using a paper planner alongside my digital calendar. The act of writing things down helps me retain them better and plan the week more deliberately.",
        "A friend recommended a documentary series on infrastructure engineering that I've started watching. It's surprisingly gripping — the episode on bridge construction was fascinating.",
        "I've been buying fewer things online lately and trying to shop locally when I can. It's not always cheaper but I prefer knowing where things come from.",
        "I attended a talk on urban design last Thursday evening. The speaker's point about how street width affects pedestrian behaviour was something I hadn't considered before.",
        "I've set up automatic bank transfers for my savings — a fixed amount moves on the first of every month before I can spend it. It's the only savings strategy that has ever worked for me.",
        "I've been doing a digital clear-out: unsubscribing from newsletters, deleting old accounts, and archiving emails I no longer need. It's satisfying but more time-consuming than I expected.",
        "I had coffee with an old colleague from my previous job last week. It was great to catch up; she's moved into a completely different industry and seems much happier.",
        "I repotted three of my plants at the weekend because they'd outgrown their containers. I used fresh soil and added some slow-release fertiliser. I'm cautiously optimistic they'll do well.",
        "I started a journal where I write three things I noticed or appreciated during the day before I go to sleep. Some days it's a stretch to fill three things but on balance it's been worth doing.",
        "I bought a new lamp for my reading corner because the old one flickered and hurt my eyes after a while. The new one has adjustable colour temperature which makes a real difference.",
        "I've been listening to a history podcast on long walks at weekends. I'm currently going through a series on the Byzantine Empire, which I knew very little about before.",
        "I took a first aid refresher course last weekend through work. The CPR practice was more physically demanding than I remembered from the last time I did it several years ago.",
    ])
    case_02 = _case(
        "d25-02", "router admin password among 25 facts",
        c02_pre + c02_target + c02_post,
        "What password do I use to log into the router settings panel?",
        expected="torrent-lamp-4471", contains=["torrent-lamp-4471"],
        tags=["distractor", "d25"], episodic_only=True,
    )

    # ── case 03: CAELUM-7 — internal research project codename ───────────────
    c03_pre = _facts([
        "I've been dealing with a slow internet connection at the office all week. The IT team says it's a bandwidth issue that will be resolved after the infrastructure upgrade next month.",
        "I organised a team lunch last Friday to mark the end of a long delivery phase. We went to a Thai restaurant and everyone seemed genuinely pleased with the choice.",
        "I've been trying to read one technical article per day related to my field, just to stay current. Some days I manage it; other days the day slips by before I get to it.",
        "I started using a new project tracker at work that my manager recommended. It has a steeper learning curve than the previous tool but the reporting features are much better.",
        "The client meeting yesterday ran over by about forty minutes because of questions that hadn't been on the agenda. It was productive in the end but we need better preparation for the next one.",
        "The marketing team named their new campaign something catchy that I keep forgetting. I think it includes a colour and a number, but I haven't looked at the campaign brief closely.",
        "I had a code review session with a junior colleague this morning. We went through three pull requests and I tried to explain my reasoning rather than just flagging issues.",
        "I updated my development environment over the weekend — new IDE version and a few new plugins. There were some compatibility issues that took an hour to resolve but it's running smoothly now.",
        "Our ops team has a project internally called something related to infrastructure scaling. I think it has a bird name in the codename but I haven't been involved in that workstream directly.",
        "I attended a cross-team alignment session yesterday about the quarterly roadmap. There were some competing priorities that needed negotiating; I think we landed in a reasonable place.",
        "I've been experimenting with a different approach to code documentation — writing the why rather than the what. It takes more thought upfront but the comments age much better.",
        "I had a long conversation with a recruiter last week about opportunities in my area. I'm not actively looking but it was useful to understand what the market looks like right now.",
        "I spent a morning cleaning up old branches in our main repository. It was overdue — we had over eighty stale branches from the last two years. The repo is much easier to navigate now.",
        "I've been trying to block out two hours of uninterrupted focus time each morning by putting it in my calendar as a meeting. It works about sixty percent of the time.",
        "I submitted a proposal for a talk at a small internal conference. I don't know yet if it's been accepted — the organisers said they'd let applicants know within two weeks.",
    ])
    c03_target = [
        _um("We had the official project kickoff meeting this week and the team landed on a codename for the new research initiative — we're going with CAELUM-7 internally. It won't appear in any external-facing documents or client communications, but all internal tickets, repositories and design documents will use that label from now on. I'm genuinely excited about the direction; it feels like substantial work."),
        _am("That's a clean naming choice. Having a consistent internal label from the start makes cross-team references much easier down the line."),
    ]
    c03_post = _facts([
        "The client's internal project has a different name to what they market externally. I was specifically told to use only the client-facing name in any written communications with them.",
        "I reviewed some technical debt tickets that have been sitting in the backlog for months. We triaged about thirty and closed half as no longer relevant. The rest got reprioritised.",
        "I gave a short knowledge-sharing session to the team last Thursday about a library I've been using. It was about twenty minutes and generated more questions than I expected — in a good way.",
        "I had a performance review conversation with my manager this week. The feedback was constructive and we agreed on a couple of development areas to focus on over the next quarter.",
        "I've been pairing with a new team member on onboarding tasks. It slows me down short-term but I know it's an investment — and it's a good opportunity to revisit things I haven't thought about in a while.",
        "I wrote a post-mortem document for last month's production incident. It took longer than expected because reconstructing the timeline required pulling logs from several different systems.",
        "I set up alerts on our monitoring dashboards for a few new metrics that the team agreed were important. It took about a morning to configure everything and write the alert conditions.",
        "I've been keeping a daily log of what I actually worked on versus what I planned to work on. The gap between the two is always instructive and slightly humbling.",
        "I attended a webinar on database indexing strategies that a colleague shared. About half of it covered things I already knew, but the second half on composite indexes was worth watching.",
        "I did a code archaeology session going through parts of the codebase that nobody on the current team originally wrote. Understanding the original intent behind some decisions took a while.",
    ])
    case_03 = _case(
        "d25-03", "internal project codename among 25 facts",
        c03_pre + c03_target + c03_post,
        "What is the internal codename for the research initiative I mentioned?",
        expected="CAELUM-7", contains=["caelum-7"],
        tags=["distractor", "d25"], episodic_only=True,
    )

    return [case_01, case_02, case_03]


def _distractor_30() -> list[dict]:
    """Target fact among 30 distractors in L3 (episodic_only).

    30 distractors × 2 messages = 60 entries — 3× the L2 cap.
    Four lexical decoys per case use the same semantic domain as the query.
    Answers are arbitrary, non-dictionary, non-guessable codes.
    """
    # ── case 01: 4417-ZORN — equipment registration number ───────────────────
    c01_pre = _facts([
        "The annual equipment audit is coming up at the end of the month. I've been going through the asset register to make sure everything is up to date before the external auditor visits.",
        "I spent a morning cleaning the workshop. Dust had built up around the ventilation units and a few cable runs needed retying. It looks considerably better now.",
        "The leasing company sent over the vehicle registration renewal documents this morning. I filed them in the orange folder in the bottom drawer, which is the standard place for vehicle paperwork.",
        "I had to order replacement consumables for the production line last week. The supplier had a six-day lead time, which is longer than usual due to port delays.",
        "The inventory management system flagged three items as discrepancies after last week's count. I need to trace each one — two are probably just scan errors but the third is more puzzling.",
        "I need to renew the equipment certification document before end of quarter. The deadline is a Friday and I've already set a calendar reminder so I don't let it slip again.",
        "The facility manager came in for a walkthrough last Tuesday. She flagged the outdated fire-extinguisher signage in the east corridor and the broken strip light near the goods entrance.",
        "A supplier representative dropped in unannounced yesterday wanting to discuss the next contract renewal. I managed to schedule a proper meeting for next week rather than improvising.",
        "I filed the maintenance log for the conveyor system after the scheduled servicing earlier this month. The service engineer noted minor wear on two of the drive belts but no immediate action needed.",
        "The replacement parts for the compressor arrived two days late. I had rescheduled the maintenance window to accommodate the delay, so it didn't affect production, but the timing was tight.",
        "The serial number label on the old laser printer in the back office has worn off completely. I had to call the manufacturer to get a replacement label sent out — it's needed for the warranty claim.",
        "I updated the preventive maintenance schedule to account for two pieces of equipment that were added to the floor last quarter. The schedule now covers thirty-seven distinct assets.",
    ])
    c01_target = [
        _um("The maintenance technician came out yesterday to service the industrial compressor unit in bay three. He checked everything thoroughly including the registration plate on the rear panel — the unit's official registration number is 4417-ZORN. He was quite insistent that I note it down correctly because there are several variants of this model and the support team needs the exact registration number to pull the right service history and parts list."),
        _am("That's the kind of detail that's easy to overlook. Worth keeping it somewhere you can find it quickly when you need to log a service call."),
    ]
    c01_post = _facts([
        "The company vehicle registration plate expires next month. I set a calendar reminder two weeks out to get the renewal processed in time — it's straightforward but easy to let slip.",
        "I attended a half-day health and safety refresher last Thursday. The most useful part was the section on manual handling, which the team genuinely needed — some bad habits had crept in.",
        "I've been reviewing our supplier contracts this month. Two are up for renewal and one of the suppliers has changed their payment terms, which we need to negotiate before signing.",
        "The building's main electrical panel was inspected by a contractor this week. They gave it a clean bill of health with a recommendation to test the earth bonding connections again in two years.",
        "I put together a short briefing document on the equipment downtime we experienced last quarter. It went to the operations manager and the facilities director as background for a budget discussion.",
        "I've been trying to standardise how we label storage areas across the facility. Inconsistent labelling has caused confusion during stock counts and the new system should make it more reliable.",
        "The waste collection schedule changed this month. Our old Tuesday slot moved to Thursday, which meant the first week I missed the collection because I forgot to move the bins out.",
        "I ordered new PPE for the maintenance team — the old sets were overdue for replacement. I went with the same manufacturer as before since the previous supply lasted well.",
        "A fire drill was scheduled for Wednesday morning. It ran efficiently and everyone was out of the building within the required time. The only gap was one team on the second floor who didn't hear the alarm.",
        "I had a procurement meeting with the finance controller to review the capital expenditure budget for next year. We're likely to get approval for two of the three items on the priority list.",
        "I completed the quarterly safety inspection checklist for all the work areas I'm responsible for. I found two minor issues — both fixable without external contractors.",
        "The loading bay doors have been stiff lately and I've asked the facilities team to look at the mechanism. It's not critical but it slows down deliveries if operators have to force them.",
        "I attended a cross-site meeting with the other facility managers to align on operating standards. It was useful to compare approaches — a few of their practices are worth adapting here.",
        "The shift supervisor asked me to review the onboarding documentation for new operators. I found sections that were out of date and have flagged them for revision before the next intake.",
        "I received the calibration certificates for the weighing scales used in dispatch. All passed within tolerance; I've filed the certificates and updated the asset register accordingly.",
        "I spent an afternoon observing the production flow to identify bottlenecks. There's a consistent accumulation at one station that suggests either a pacing or a tooling issue — I need to investigate further.",
        "I raised a near-miss report after observing a forklift path that came too close to a pedestrian walkway. The supervisor acknowledged it immediately and the path markings are being reviewed.",
        "I updated the emergency contact list for the facility — two of the listed numbers were no longer valid. I also added the new facilities coordinator who joined last month.",
    ])
    case_01 = _case(
        "d30-01", "equipment registration number among 30 facts",
        c01_pre + c01_target + c01_post,
        "What is the registration number on the compressor unit that was serviced?",
        expected="4417-ZORN", contains=["4417-zorn"],
        tags=["distractor", "d30"], episodic_only=True,
    )

    # ── case 02: PELIKAN-NORD — off-site venue internal codename ─────────────
    c02_pre = _facts([
        "I've been finalising the agenda for the annual planning session. There are eleven confirmed attendees so far and two more who haven't responded. The format will be similar to last year but with a longer open discussion slot in the afternoon.",
        "I booked a room at a hotel near the airport for a colleague visiting from abroad next week. The hotel has a shuttle service which makes the logistics easier.",
        "The conference location for the industry event in autumn hasn't been announced yet. The organising committee usually sends a briefing note about six weeks in advance.",
        "I drafted the attendee briefing document for the strategy session. It covers the purpose, the intended outcomes and what participants should prepare in advance.",
        "I've been comparing venues for the leadership off-site in the spring. I've shortlisted four options within a two-hour radius of the main office and I'll present them to the leadership team next week.",
        "The caterer confirmed availability for the event dates I proposed. I still need to finalise the menu options — I'm waiting on the dietary requirement responses from attendees.",
        "I coordinated with the AV team about the equipment setup for the session. They need access from early morning to test the projectors and microphones before guests arrive.",
        "The backup venue for the event is a conference hotel near the ring road. We keep it as a fallback in case the primary location becomes unavailable — it happened once two years ago and we needed it.",
        "I've been working on the facilitation guide for the off-site. I want the sessions to feel structured without being rigid — enough of a skeleton that the facilitator can adapt in real time.",
        "I sent out the pre-reading materials to all confirmed attendees last week. Two people replied immediately with questions, which suggests they actually read it — a better response than usual.",
        "I'm managing the accommodation bookings for participants who need to stay overnight. Seven people need rooms and I've provisionally held them at the same property as the venue.",
        "I reviewed the catering invoices from last quarter's events to get a realistic budget estimate for the upcoming sessions. Costs have gone up noticeably compared to two years ago.",
        "The event logistics lead wants a detailed run-of-show document at least two weeks before the date. I've started building it but I'm waiting on some timing confirmations before it's complete.",
        "I spoke with the security team about access arrangements for the off-site. External attendees will need to be registered in advance and will be issued visitor passes on arrival.",
        "The transport coordinator is arranging a minibus to collect participants from the city centre. The pickup time needs to align with the train arrivals, which has a forty-minute spread.",
        "I finalised the budget estimate for the event and submitted it for sign-off. It came in slightly over the initial allocation but within what I'd described as the realistic range.",
        "I drafted a post-event survey to collect feedback from participants. I'm keeping it short — five questions — to increase the response rate compared to the long survey we sent last time.",
        "I've been coordinating with the venue's facilities manager to confirm room capacities and layout options. We'll need round tables for the workshop sessions rather than theatre-style seating.",
    ])
    c02_target = [
        _um("The logistics for the off-site meeting are nearly complete. The venue has been confirmed and we've registered it under the internal codename PELIKAN-NORD so it doesn't show up in public-facing calendars or any documents shared with external parties. Participants will receive the actual address and directions via a secure, encrypted message no more than 24 hours before the event starts."),
        _am("That's a sensible operational approach for a sensitive planning session. PELIKAN-NORD noted."),
    ]
    c02_post = _facts([
        "I arranged the post-event dinner at a restaurant within walking distance of the venue. I've reserved a private dining room so the team can continue discussions in a relaxed setting.",
        "I confirmed the final attendee list with the venue's catering coordinator this morning. The list changed twice in the last week due to last-minute schedule conflicts.",
        "I've been working on the slides for the session introduction. I want them to be minimal — mostly prompts and frameworks — rather than dense content that participants have to read.",
        "The event will be partially recorded so that people who couldn't attend can watch key segments later. I'm coordinating with the AV team on which sessions should be included.",
        "The backup venue is now on standby with a 72-hour cancellation window. I've reviewed the contract terms and they're reasonable given the scale of the event.",
        "I sent confirmation emails to all registered participants with the agenda and logistics overview. I kept the logistics section deliberately vague pending the final venue details.",
        "I prepared a contingency plan for the event in case of adverse weather affecting travel. It covers remote participation options for anyone who can't get there physically.",
        "I've been tracking RSVPs using a shared spreadsheet. As of this morning, eighteen people have confirmed, three have declined and two haven't responded despite a reminder.",
        "I submitted the event risk assessment to the operations team as required by the internal events policy. The main risks are transport disruption and a lower-than-expected attendance rate.",
        "I checked in with the facilitator about the session plan. She's made a few adjustments to the afternoon flow based on the pre-reading responses that came back — the changes look sensible.",
        "I arranged for printed materials to be produced and shipped to the venue in advance. I allowed an extra day in the schedule in case of delivery delays.",
        "I followed up with the outstanding RSVPs this morning with a firm deadline. I need the final headcount by end of day to confirm catering quantities with the supplier.",
    ])
    case_02 = _case(
        "d30-02", "off-site venue codename among 30 facts",
        c02_pre + c02_target + c02_post,
        "What is the internal codename under which the off-site venue was registered?",
        expected="PELIKAN-NORD", contains=["pelikan-nord"],
        tags=["distractor", "d30"], episodic_only=True,
    )

    # ── case 03: bravo-zebra-9 — parcel collection verification code ─────────
    c03_pre = _facts([
        "I've been ordering more things online than usual lately. The convenience is hard to argue with but I'm trying to be more deliberate about what I actually need before clicking purchase.",
        "The courier left a missed delivery card while I was out last Tuesday. I rebooked online for Saturday morning and the driver actually arrived in the first half of the window for once.",
        "I'm waiting on a package from a supplier abroad. It's been in customs for five days now and the tracking hasn't updated since it cleared the origin airport. I've raised a query with the courier.",
        "I ordered a replacement part for a piece of kitchen equipment and it arrived three days early. The packaging was excessive — four layers of bubble wrap for something the size of a fist.",
        "The post office told me I need to bring ID along with the delivery notice when I collect a registered letter. I hadn't realised there was a registered letter waiting — I hadn't checked my PO box in weeks.",
        "I set up a delivery safe in the porch so parcels can be left securely when I'm not home. It's one of those lockable boxes with a rotary code that the courier is supposed to use.",
        "I got an email about a failed delivery and clicked the reschedule link. I chose a Saturday slot since I'm home then. The confirmation came through almost immediately.",
        "I use a parcel forwarding service for orders from international retailers that don't ship directly to my country. The service adds a few days but the savings on some items make it worthwhile.",
    ])
    c03_target = [
        _um("I got a notification this afternoon that my parcel is ready for collection at the local pickup point on Harrow Street. The message says I need to give the staff a verification code when I arrive to confirm my identity — it's bravo-zebra-9. The code is valid until tomorrow evening at 8pm, so I need to get there today or first thing in the morning before it expires."),
        _am("Good to have that noted. bravo-zebra-9, valid until tomorrow evening — you have a comfortable window if you go first thing in the morning."),
    ]
    c03_post = _facts([
        "I collected a parcel that had been held for three days at the depot. I had to show ID and sign a form — slightly more involved than the usual drop-and-go. The item itself was fine.",
        "The courier company asked me to rate my delivery experience via a text message survey. I gave it three out of five — the delivery was fine but the time window estimate was off by two hours.",
        "I raised a claim with the courier for a damaged item. The online form was straightforward and they said to expect a response within five business days. I kept all the original packaging as evidence.",
        "I had a package delivered to my neighbour by mistake — they brought it over the same evening. I've updated my delivery address preferences to reduce the chance of that happening again.",
        "I ordered a book that was listed as in stock but then received a message saying it was backordered. I decided to wait rather than cancel, since I wasn't in a rush.",
        "I registered for click-and-collect at the local supermarket to pick up a heavy order on the way home from work. The slot was available for the same evening, which was convenient.",
        "I got a notification that a parcel I was expecting had been delivered to a neighbour two streets away. It took two days to track it down — the courier had misread the house number.",
        "I ordered a piece of furniture flat-pack and the delivery required me to be home for a specific two-hour window. The driver called thirty minutes before arriving, which was helpful.",
        "I returned a faulty item using the prepaid label included in the package. I dropped it at a parcel locker near the office and got a confirmation scan notification within the hour.",
        "I've been comparing delivery subscription services to see if the annual fee is worth it given how often I order things. Based on the last six months it would save me a meaningful amount.",
        "I track all my incoming orders in a notes app — expected delivery date, tracking number and what's inside. It sounds excessive but I've had too many moments when something arrives and I can't remember what it is.",
        "I received an automated message saying my order had been split into two separate shipments. One arrived the same day; the other is still a few days out.",
        "I updated my delivery address in three separate online accounts this week after moving to a new flat. I'll probably still find forgotten ones for the next few months.",
        "I sent a gift order directly to a recipient's address and added a gift note. The site's gift wrapping option was surprisingly good — much better than what I'd manage at home.",
        "I checked the tracking status on an international order and it's been sitting in a transit hub overseas for a week without movement. I'll give it a few more days before contacting the seller.",
        "I ordered office supplies for the team and tracked the delivery through the supplier's portal. The portal crashed twice during the process, which is frustrating given it's a standard B2B account.",
        "I left a delivery instruction in my account asking couriers to leave parcels with the concierge if I'm not in. It works about half the time — the other half they still leave a card.",
        "I prepaid for expedited shipping on an order I needed quickly, and it still arrived two days late. The carrier cited unspecified delays and offered a partial refund on the shipping cost.",
        "I reorganised my wardrobe last month after a batch of online orders arrived. I donated two bags of items I hadn't worn in over a year to make room for the new things.",
        "I flagged a package as not received even though the courier marked it as delivered. The investigation took four days and concluded it had been delivered to the wrong building entirely.",
        "I got an unexpected delivery today — it turned out to be a gift from a relative I hadn't spoken to in a while. A nice surprise, though the timing was puzzling.",
        "I spent about thirty minutes updating saved addresses across various accounts after my building's street numbering was corrected by the council. Small administrative hassle but necessary.",
    ])
    case_03 = _case(
        "d30-03", "parcel collection verification code among 30 facts",
        c03_pre + c03_target + c03_post,
        "What code do I need to give when collecting my parcel from the pickup point?",
        expected="bravo-zebra-9", contains=["bravo-zebra-9"],
        tags=["distractor", "d30"], episodic_only=True,
    )

    return [case_01, case_02, case_03]


# ---------------------------------------------------------------------------
# Programmatic distractor generation — used by large-N distractor datasets.
# 63 unique multi-sentence paragraphs covering diverse life/work domains.
# For N > 63, a time-prefix cycle makes each entry a distinct ChromaDB doc
# while keeping semantic content broad enough to compete with any query.
# ---------------------------------------------------------------------------

_DISTRACTOR_POOL: list[str] = [
    # morning routine
    "I usually wake up around 6:30 and spend the first twenty minutes having coffee without looking at my phone. It helps me start the day without feeling rushed. I've been doing this for about three months now.",
    "I've started doing ten minutes of stretching every morning before I eat breakfast. It sounds minimal but my lower back has stopped aching the way it did all last year. I'm trying to make it a permanent habit.",
    "I set two alarms in the morning — one at 6:45 and a backup at 7:00. I almost always wake up during the gap between them, which I find annoying, but I don't trust myself to rely on just one.",
    # commute / transport
    "I commute by train five days a week and it takes about forty minutes each way. I use the time to read or listen to podcasts so it doesn't feel wasted. The window seat on the upper deck is my favourite spot.",
    "I've been cycling to work a couple of days a week when the weather allows. It adds about fifteen minutes compared to taking the train but I arrive feeling more energetic.",
    "I switched from driving to taking the bus last autumn. The journey takes longer but I don't have to deal with parking and I can actually use the time for something useful.",
    # cooking / food
    "My partner and I cook together most evenings and take turns picking the recipe. Last week we made a lamb tagine that turned out better than expected. We're slowly working through a cookbook we got as a gift.",
    "I've been experimenting with batch cooking on Sundays. I prepare three or four portions of a grain base and whatever proteins I have in the fridge and use them through the week. It saves a lot of weeknight decision fatigue.",
    "I've been trying to reduce how much meat I eat without going fully vegetarian. I've settled on cooking plant-based four nights a week. I'm pleasantly surprised by how satisfying it can be with the right spices.",
    # learning / skills
    "I've been learning Spanish for eight months using a language app. I practise for fifteen minutes every day, usually before bed. I can hold a basic conversation now but the subjunctive tense still trips me up.",
    "I started a woodworking evening class at the local community centre last autumn. We've made a small shelf and a cutting board so far. It's satisfying to work with your hands after a desk job.",
    "I've been working through an online course on data analysis in my spare time. I do about one module per week, usually over the weekend. I'm about halfway through and finding it genuinely useful.",
    # fitness / exercise
    "I renewed my gym membership last month after a long break. I go three times a week and focus on strength training. Morning sessions are quieter than evenings, which I strongly prefer.",
    "I signed up for a half marathon in the spring and I'm following a twelve-week training plan. So far I've stuck to it more consistently than any other running programme I've tried. The long Sunday runs are tough but satisfying.",
    "I've been doing yoga at home using a video series a friend recommended. I try to fit in a session three or four times a week, usually in the early evening. My flexibility has improved noticeably over the past two months.",
    # work productivity
    "I've been trying to block out two hours of uninterrupted focus time each morning by putting it in my calendar as a meeting. It works about sixty percent of the time.",
    "I started using a paper planner alongside my digital calendar. The act of writing things down helps me retain them better and plan the week more deliberately.",
    "I've been keeping a daily log of what I actually worked on versus what I planned to work on. The gap between the two is always instructive and slightly humbling.",
    "I switched to a standing desk earlier this year. I alternate between sitting and standing every hour or so. My energy levels in the afternoon are noticeably better than they were before.",
    # home maintenance
    "I've started keeping a running list of things to fix around the flat. The kitchen tap has been dripping for weeks and I keep delaying calling a plumber. I need to sort it out this weekend.",
    "I finally sorted through the pile of old cables I've been accumulating. I threw most of them out because I couldn't identify what half were for. I kept the useful ones in a labelled box.",
    "I repainted the hallway last month — it was long overdue. The new colour is much lighter than the old one and the whole entrance area feels different. I'm thinking about doing the kitchen next.",
    # reading / media
    "I'm about halfway through a book on systems thinking that a colleague recommended. I find it genuinely useful for understanding how interconnected problems behave in large organisations.",
    "I've been listening to a history podcast on long walks at weekends. I'm currently going through a series on the Byzantine Empire, which I knew very little about before.",
    "I started a journal where I write three things I noticed or appreciated during the day before I go to sleep. Some days it's a stretch to fill three things but on balance it's been worth doing.",
    # social / relationships
    "I had coffee with an old colleague from my previous job last week. It was great to catch up; she's moved into a completely different industry and seems much happier.",
    "I've been making more of an effort to call my parents on Sundays rather than just texting. The calls last about twenty minutes and they seem to appreciate the regularity of it.",
    "I attended a talk on urban design last Thursday evening. The speaker's point about how street width affects pedestrian behaviour was something I hadn't considered before.",
    # finance / savings
    "I've set up automatic bank transfers for my savings — a fixed amount moves on the first of every month before I can spend it. It's the only savings strategy that has ever worked for me.",
    "I've been comparing energy suppliers to see if I can reduce my monthly bills. The switching process is more straightforward than I expected. I'm waiting for one more quote before I decide.",
    "I reviewed my subscriptions last weekend and cancelled four that I wasn't using actively. Individually they were cheap but together they added up to more than I'd realised.",
    # health / sleep
    "I've been tracking my sleep with my watch for the past month. I average about six and a half hours, which is less than I'd like. I'm trying to move my bedtime thirty minutes earlier.",
    "I started doing a short meditation session in the mornings before I check my email. Even ten minutes makes a noticeable difference to how I handle the rest of the day.",
    "I've been drinking more water by keeping a large bottle on my desk as a visual reminder. I try to finish it before lunch and refill for the afternoon. It's a small habit but I feel better for it.",
    # technology / gadgets
    "My home desk now has two monitors, which makes working on code much easier. I also got a new chair last month after my back started bothering me. Small improvements but they add up.",
    "I'm thinking about switching to a mechanical keyboard. I tried one at a colleague's desk and liked the tactile feedback. The noise might be an issue in open-plan offices though.",
    "I've been doing a digital clear-out: unsubscribing from newsletters, deleting old accounts, and archiving emails I no longer need. It's satisfying but more time-consuming than I expected.",
    # gardening / plants
    "My houseplant collection has grown to seven plants. The fiddle-leaf fig is the hardest — it drops a leaf whenever I move it even slightly. The succulents practically take care of themselves.",
    "I repotted three of my plants at the weekend because they'd outgrown their containers. I used fresh soil and added some slow-release fertiliser. I'm cautiously optimistic they'll do well.",
    # music
    "I've started attending an open-mic night at a local venue on the first Thursday of each month. I usually perform two or three acoustic pieces. The audience is small but supportive.",
    "I bought a digital piano a few months ago after years of wanting one. I practise for about thirty minutes on weekday evenings. I'm relearning pieces I used to know and slowly adding new ones.",
    # travel / planning
    "We're planning a short trip to the coast next month, just for a long weekend. I've been browsing rental apartments and one has a sea view which would be perfect. Nothing booked yet but I'm optimistic.",
    "I'm trying to plan a longer trip for late in the year. I've narrowed it down to two destinations but can't decide. I'd rather choose somewhere slower-paced than tick off as many sights as possible.",
    "I booked flights for a work conference next quarter. The city is one I've never been to so I'm adding a day on each end to explore. Mixing business and leisure travel is the only way I can afford to go somewhere new.",
    # shopping / purchases
    "I've been buying fewer things online lately and trying to shop locally when I can. It's not always cheaper but I prefer knowing where things come from.",
    "I bought a new lamp for my reading corner because the old one flickered and hurt my eyes after a while. The new one has adjustable colour temperature which makes a real difference.",
    "I reorganised my wardrobe last month after a batch of online orders arrived. I donated two bags of items I hadn't worn in over a year to make room for the new things.",
    # creative hobbies
    "I've been taking a short photography walk on Sunday mornings before the city gets busy. I try to shoot one or two keepers per session and keep them in a folder I look back at monthly.",
    "I started a small ceramics project at home using air-dry clay. I've made three bowls so far and they're functional if not particularly elegant. I find the process genuinely calming.",
    "I've been sketching for about twenty minutes before bed a few nights a week. I'm not trying to produce finished work — just keeping the habit of looking carefully at things.",
    # environment / sustainability
    "I've switched to refillable containers for most of the cleaning products at home. It requires a bit more planning but I've cut the amount of plastic waste I generate noticeably.",
    "I started composting food scraps earlier this year. The bin lives on the balcony and I drop it off at a collection point every couple of weeks. It's a small thing but it feels meaningful.",
    # professional development
    "I attended a webinar on database indexing strategies that a colleague shared. About half of it covered things I already knew, but the second half on composite indexes was worth watching.",
    "I submitted a proposal for a talk at a small internal conference. I don't know yet if it's been accepted — the organisers said they'd let applicants know within two weeks.",
    "I took a first aid refresher course last weekend through work. The CPR practice was more physically demanding than I remembered from the last time I did it several years ago.",
    # personal organisation
    "I've been meaning to update my CV for months. I keep adding things mentally but never sit down to write it. I'll try to do it over the weekend if I find a quiet hour.",
    "I've been trying to check the news less in the evenings because I find it disrupts my sleep. I've limited myself to a morning and early-afternoon window. It's harder to stick to than I expected.",
    "I have a dentist appointment next month that I've already rescheduled twice. I really need to go this time. I always build it up as worse than it actually is.",
    # pets / animals
    "My neighbour has a dog that barks every morning around 7am. I've been wearing earplugs to sleep through it. I should probably mention it to them but I don't want to create tension.",
    "I've been looking into getting a cat for a while. I've done most of the research — the vet costs, feeding schedule, litter maintenance. I just haven't committed to the timing yet.",
    # cultural / events
    "I played board games with friends on Saturday. We played a long strategy game that got quite competitive. Next time I want to try something cooperative instead.",
    "I went to an exhibition at a gallery near work last Wednesday lunchtime. The show was smaller than I expected but two of the pieces were genuinely affecting. I went back to look at one of them twice.",
    "I've started going to a weekly pub quiz with a small group. We usually finish mid-table. The general knowledge rounds are our strongest; the pop music round is reliably our worst.",
]

_TIME_VARIANTS: list[str] = [
    "",
    "Last week, ",
    "Recently, ",
    "A few months back, ",
    "Earlier this year, ",
    "Just the other day, ",
    "For the past few weeks, ",
    "Since last month, ",
    "This past weekend, ",
    "Yesterday, ",
    "A while ago, ",
    "Over the weekend, ",
    "Not long ago, ",
    "Earlier today, ",
]


def _gen_n_distractors(n: int) -> list[str]:
    """Generate n unique distractor strings from _DISTRACTOR_POOL.

    Cycle through the pool; each subsequent cycle prepends the next time-prefix
    variant so that repeated entries are distinct ChromaDB documents while
    remaining semantically diverse relative to any single query.
    """
    result: list[str] = []
    cycle = 0
    while len(result) < n:
        prefix = _TIME_VARIANTS[cycle % len(_TIME_VARIANTS)]
        for base in _DISTRACTOR_POOL:
            if len(result) >= n:
                break
            if cycle == 0:
                result.append(base)
            else:
                result.append(prefix + base[0].lower() + base[1:])
        cycle += 1
    return result


def _splice(items: list[str], replacements: dict[int, str]) -> list[str]:
    """Replace specific indices in items with domain-specific decoy strings."""
    out = list(items)
    for idx, text in replacements.items():
        if 0 <= idx < len(out):
            out[idx] = text
    return out


def _build_case(
    cid: str, name: str,
    n_before: int, n_after: int,
    target_user: str, target_asst: str,
    query: str, expected: str,
    decoys_before: dict[int, str],
    decoys_after: dict[int, str],
    tags: list[str],
) -> dict:
    """Build one large-distractor test case from the shared pool."""
    pool = _gen_n_distractors(n_before + n_after)
    before = _splice(pool[:n_before], decoys_before)
    after  = _splice(pool[n_before:], decoys_after)
    conv   = _facts(before) + [_um(target_user), _am(target_asst)] + _facts(after)
    return _case(
        cid, name, conv, query,
        expected=expected, contains=[expected.lower()],
        tags=tags, episodic_only=True,
    )


def _distractor_50() -> list[dict]:
    """3 cases × 50 distractors in L3 (episodic_only). Multi-sentence messages,
    3 lexical decoys per case, non-guessable answers."""
    T = ["distractor", "d50"]
    return [
        _build_case(
            "d50-01", "firmware recovery PIN among 50 facts",
            n_before=20, n_after=30,
            target_user=(
                "The IT support technician walked me through a remote session to reconfigure the router. "
                "At one point he told me to note down the firmware recovery PIN in case I ever needed to "
                "perform a full hardware reset independently — it's KRONEX-7. He said it was specific to "
                "my hardware revision and I absolutely should not lose it."
            ),
            target_asst="That's an important detail. Firmware recovery PINs are rarely needed but critical when they are.",
            query="What is the PIN required to perform a firmware reset on the router?",
            expected="KRONEX-7",
            decoys_before={
                3: "I updated the PIN on my home alarm panel last week after the service engineer's visit. The new combination is something I'll need to memorise since I can't write it near the panel.",
                11: "The building's secure entry now requires a separate PIN for after-hours access. I was given it by the facilities team and I'm supposed to keep it confidential.",
            },
            decoys_after={
                5: "My gym locker uses a four-digit PIN that I keep forgetting. I'm considering switching to one that uses a pattern I already know from elsewhere.",
                18: "The storage unit at work has a new keypad code after the lock was changed last month. I got the new PIN from the site manager.",
            },
            tags=T,
        ),
        _build_case(
            "d50-02", "encrypted notes master passphrase among 50 facts",
            n_before=10, n_after=40,
            target_user=(
                "I finally set up a properly encrypted notes application for sensitive information. "
                "When I configured it, I chose a master passphrase that was long enough to be secure "
                "but structured in a way I'd actually remember — it's silver-rabbit-331. "
                "I wrote a reminder clue in my paper notebook but not the phrase itself, as a compromise "
                "between security and recoverability."
            ),
            target_asst="A passphrase-based approach is smart. Three-part passphrases are both strong and human-memorable.",
            query="What passphrase do I use to unlock my encrypted notes application?",
            expected="silver-rabbit-331",
            decoys_before={
                2: "I reset my email account password last week after a suspicious login notification. I generated a long random string and stored it only in my password manager.",
                7: "The password for the work VPN changes every 90 days — the IT helpdesk sends a reminder a week before expiry. I always update it immediately when the reminder arrives.",
            },
            decoys_after={
                8: "I changed my streaming service password because I suspected someone else was using the account. It's now something only I know.",
                25: "The shared admin account for the team's deployment environment has a passphrase that I rotate on a schedule. The current one was set about six weeks ago.",
                38: "I use a password manager for everything now. The master password for the manager itself is the one thing I have to keep in my head.",
            },
            tags=T,
        ),
        _build_case(
            "d50-03", "instrument storage locker combination among 50 facts",
            n_before=35, n_after=15,
            target_user=(
                "The studio manager handed me the combination for the shared instrument storage locker "
                "at the end of the session last Thursday. I'll need it every time I want to use the house "
                "guitars or the effects pedals — the combination is THULE-88. She said to keep it "
                "between regular members and not share it with casual visitors or anyone sitting in on a session."
            ),
            target_asst="Good to have that. THULE-88 — I'll keep that noted for you.",
            query="What is the combination I need to open the instrument storage locker at the studio?",
            expected="THULE-88",
            decoys_before={
                6: "The storage cage in the basement of my building has a padlock combination that I always confuse with the bike lock code. I should label them more clearly.",
                20: "The rehearsal room at the arts centre uses a keypad lock. The combination changes at the start of each new term and members get the new one by email.",
                30: "The secure cabinet where we keep the master recordings has a combination that only three people know. I'm not one of them, which occasionally makes access slightly awkward.",
            },
            decoys_after={
                4: "The filing cabinet in the office was relocked after someone left and no one could find the combination. The facilities team had to bring in a locksmith.",
                11: "The locker room at the sports centre uses a dial combination that I set myself when I first signed up. I haven't changed it since.",
            },
            tags=T,
        ),
    ]


def _distractor_100() -> list[dict]:
    """3 cases × 100 distractors in L3 (episodic_only)."""
    T = ["distractor", "d100"]
    return [
        _build_case(
            "d100-01", "laptop asset tag among 100 facts",
            n_before=40, n_after=60,
            target_user=(
                "The IT asset manager came around this week to audit all company hardware. "
                "He checked the label on the underside of my laptop and recorded the asset tag number "
                "in the inventory system — it's 9920-DELTA. He reminded me to quote that exact identifier "
                "whenever I log a support ticket or request a hardware upgrade, since there are multiple "
                "machines with similar configurations in the fleet."
            ),
            target_asst="Good to have that on record. Asset tags save a lot of confusion when dealing with IT support.",
            query="What is the asset tag number registered to my company laptop?",
            expected="9920-DELTA",
            decoys_before={
                8: "The serial number on my work monitor is on a sticker on the back panel. I photographed it for the equipment register but haven't memorised it.",
                25: "I submitted a hardware request form for a replacement keyboard. I had to include the asset number of the machine it would be paired with, which I had to look up.",
                35: "The registration number for the company car I use for client visits is in the glove box. I've never had to quote it from memory.",
            },
            decoys_after={
                10: "The barcode label on the office printer has worn off and IT can't locate it in the asset register without it. They're trying to identify it by model and purchase date instead.",
                40: "The new equipment we received last quarter hasn't been tagged yet. The asset manager is waiting for the correct label stock to arrive before processing the batch.",
                55: "My previous employer had an asset tagging system that used a completely different format. I had to explain the new format to a colleague who transferred with me.",
            },
            tags=T,
        ),
        _build_case(
            "d100-02", "VPN pre-shared key among 100 facts",
            n_before=15, n_after=85,
            target_user=(
                "The network administrator sent me the updated credentials for the split-tunnel VPN "
                "configuration I use when working remotely. The critical piece was the pre-shared key "
                "used during the IKE handshake phase — it's mauve-dagger-5. She was explicit that this "
                "key must not be stored in plaintext anywhere on the corporate network or in any "
                "cloud-synced file."
            ),
            target_asst="Understood. mauve-dagger-5 — stored securely, not in plaintext.",
            query="What is the pre-shared key for the VPN connection?",
            expected="mauve-dagger-5",
            decoys_before={
                4: "I reset my VPN password last month when the IT team sent the mandatory rotation notice. The new credential went straight into my password manager.",
                11: "The network passphrase for the guest wifi at the office changes on the first of every month. I always have to ask reception when I bring a visitor in.",
            },
            decoys_after={
                15: "The shared credential for the analytics platform was rotated last week. I got the new one from the team lead and updated my local configuration file.",
                50: "The API key for the external data provider expired and I had to request a replacement. The new key arrived by encrypted email, which was a bit unusual.",
                70: "The authentication token for the CI pipeline needs rotating every six months. I have a calendar reminder set so it doesn't expire during a release window.",
                82: "The credentials for the staging environment are shared among four team members. We agreed informally to rotate them whenever someone leaves the project.",
            },
            tags=T,
        ),
        _build_case(
            "d100-03", "development server hostname among 100 facts",
            n_before=60, n_after=40,
            target_user=(
                "The infrastructure team assigned me a dedicated development server this week. "
                "The hostname I need to use in all configuration files, SSH config aliases, "
                "CI environment variables and deployment scripts is ARCTIS-ZWEI. "
                "The team lead was quite specific that I must use exactly that casing — "
                "the environment variable substitution in the build pipeline is case-sensitive."
            ),
            target_asst="Noted. ARCTIS-ZWEI — case-sensitive, to be used across all config and pipeline references.",
            query="What is the hostname of the dedicated development server assigned to me?",
            expected="ARCTIS-ZWEI",
            decoys_before={
                12: "The staging server was renamed last sprint as part of the infrastructure reorganisation. I had to update four configuration files and two pipeline definitions.",
                35: "The database host for the integration test environment uses a hostname that differs from production only by a suffix. Getting them confused caused an incident last quarter.",
                55: "The load balancer in front of the application cluster has a hostname that's aliased through the internal DNS. The canonical name is longer and I rarely need to use it directly.",
            },
            decoys_after={
                8: "The hostname of the old monitoring server was never officially deprecated. Some legacy scripts still point to it and fail silently when they can't connect.",
                28: "The internal domain for the new microservices cluster follows a naming convention I'm still getting used to. Each service has a region code and a function identifier.",
                38: "The server assigned to the QA team has a hostname that reflects its original purpose, which no longer matches what it's used for. Renaming it is on the backlog.",
            },
            tags=T,
        ),
    ]


def _distractor_200() -> list[dict]:
    """3 cases × 200 distractors in L3 (episodic_only)."""
    T = ["distractor", "d200"]
    return [
        _build_case(
            "d200-01", "archive drive encryption PIN among 200 facts",
            n_before=80, n_after=120,
            target_user=(
                "I set up the encrypted external archive drive this afternoon following the security "
                "protocol document step by step. When I initialised the encryption layer I chose a PIN "
                "that was both memorable and not derivable from any personal information — it's 7731-KOVA. "
                "The drive will not mount without it and there is no vendor recovery mechanism, "
                "so I printed the PIN on a card and stored it in the physical filing cabinet under lock and key."
            ),
            target_asst="That's the right approach for an air-gapped archive. 7731-KOVA, stored physically only.",
            query="What PIN is required to mount the encrypted archive drive?",
            expected="7731-KOVA",
            decoys_before={
                15: "The encrypted backup volume at work requires a passphrase that only two people in the team know. I'm not one of them — I just handle the scheduling.",
                45: "The hardware security token for the signing infrastructure requires a PIN to unlock. I was told the PIN during the setup session and have it written in a secure location.",
                72: "The drive encryption on my work laptop was configured by IT. They set the pre-boot PIN and I was asked to change it on first login. I did, and now only I know the current one.",
            },
            decoys_after={
                20: "The PIN for the encrypted partition on the team's shared drive was changed after a staff departure. The new one was circulated only to the remaining members with access.",
                80: "I had to reset the PIN for the mobile device management profile on my phone after too many failed attempts triggered a lockout. IT had to do a partial wipe before I could reconfigure it.",
                110: "The secure USB drive used for key ceremony backups requires both a PIN and a physical touch to unlock. Setting it up took most of a morning.",
                118: "The encryption PIN for the cold storage wallet was split into two parts and given to different custodians. I hold one half; the other custodian holds the other.",
            },
            tags=T,
        ),
        _build_case(
            "d200-02", "staging deployment passphrase among 200 facts",
            n_before=30, n_after=170,
            target_user=(
                "The devops team updated the release authentication process for the staging pipeline "
                "this sprint as part of the supply chain security hardening initiative. "
                "The deployment step now requires a passphrase to be confirmed before any release "
                "candidate can be promoted to staging — it's fulcrum-snap-44. "
                "It has to be entered in the CI secrets manager and also provided as an environment "
                "variable in the local staging configuration for manual deployments."
            ),
            target_asst="Understood. fulcrum-snap-44 in the CI secrets manager and local staging config.",
            query="What passphrase is required to promote a build through the staging deployment pipeline?",
            expected="fulcrum-snap-44",
            decoys_before={
                8: "The deployment token for the production pipeline is rotated on every release. The release manager generates a new one as part of the pre-release checklist.",
                22: "The passphrase for the release signing key is held by three people. A deployment requires at least two of them to confirm before the signature can be applied.",
            },
            decoys_after={
                30: "The deploy secret for the canary environment was accidentally committed to the repository last month. It was rotated immediately and the incident was logged.",
                90: "The pipeline authentication was changed from a shared secret to short-lived tokens last quarter. Most engineers haven't had to interact with the underlying mechanism since.",
                140: "The release gate passphrase for the pre-production environment is documented in the runbook. The runbook itself is stored in an access-controlled wiki page.",
                165: "The staging pipeline was locked down after an unauthorised deployment last year. Additional passphrase confirmation was one of several controls added at the time.",
            },
            tags=T,
        ),
        _build_case(
            "d200-03", "remote property door code among 200 facts",
            n_before=150, n_after=50,
            target_user=(
                "My aunt gave me the keypad access code for the front entrance of the remote property "
                "we share use of over the summer. The code is BOREAL-11 and it works on both the "
                "outer gate panel and the main door keypad. She told me to make sure I enter it "
                "within ten seconds of the first activation beep, because the system resets and "
                "requires starting over if you take too long."
            ),
            target_asst="Got it — BOREAL-11, enter within ten seconds of the first beep, works on gate and main door.",
            query="What is the keypad code for the entrance of the remote property?",
            expected="BOREAL-11",
            decoys_before={
                25: "The key safe outside the holiday cottage we rented had a four-digit combination that the owner sent in the booking confirmation. I had to read it off my phone in the dark.",
                80: "The entry code for the apartment block where my friend lives changed when the building was sold. She had to text me the new one ten minutes before I arrived.",
                120: "The gate code at my parents' property is one I've known since I was a teenager. They've never changed it, which I keep telling them is a security risk.",
                145: "The access code for the remote storage facility changes every quarter. I get the new one by post, which feels unnecessarily slow given that everything else is digital.",
            },
            decoys_after={
                10: "The code for the holiday let's key lockbox was straightforward, but the lockbox itself was stiff and took me several attempts to open even with the correct combination.",
                35: "The entry code for the shared workspace I use when I'm working from the coast is pinned up near the door, which rather defeats the purpose of having one.",
                45: "The door code for the mountain refuge we stayed at on the hiking trip was given to us by the warden on arrival. It changed every evening for security.",
            },
            tags=T,
        ),
    ]


def _distractor_400() -> list[dict]:
    """3 cases × 400 distractors in L3 (episodic_only)."""
    T = ["distractor", "d400"]
    return [
        _build_case(
            "d400-01", "server rack cabinet combination among 400 facts",
            n_before=160, n_after=240,
            target_user=(
                "The data centre access coordinator issued me the combination for the server rack "
                "cabinet in row seven during the site induction. The lock is a four-wheel rotary dial "
                "followed by an alpha suffix panel, and the full combination is 3388-LYNX. "
                "The alpha suffix identifies the cabinet series and has to be entered after clearing "
                "the numeric wheels. I had to countersign an access log acknowledging receipt."
            ),
            target_asst="Logged. 3388-LYNX — numeric wheels first, then alpha suffix on the panel.",
            query="What is the combination for the server rack cabinet in row seven of the data centre?",
            expected="3388-LYNX",
            decoys_before={
                20: "The combination for the network patch panel cabinet is known only to senior engineers. I'm not senior yet, so I have to ask someone to open it whenever I need access.",
                80: "The cabinet lock in the secondary data centre uses a different mechanism from the primary. It's a push-button code rather than a rotary dial, which I find less reliable.",
                130: "The rack cabinet in the staging environment room is unlocked most of the time, which the security team keeps flagging in their quarterly audit. Nobody has acted on it yet.",
                155: "The combination for the power distribution cabinet was changed after the last maintenance window. Only the facilities lead and the senior engineer on call know the current one.",
            },
            decoys_after={
                30: "The server cabinet at the remote office has a combination that I was never formally given. I've been borrowing the key from the local office manager whenever I need access.",
                120: "The cabinet housing the backup tape drives uses a keyed lock rather than a combination. The key is checked out from the security desk and signed back in.",
                200: "The combination for the equipment cabinet in the meeting room was set by the AV team and no one thought to document it. It took half an afternoon to reset.",
                235: "The server room access in the old building required a combination plus a swipe card. The new building uses biometric access instead, which is both more secure and more convenient.",
            },
            tags=T,
        ),
        _build_case(
            "d400-02", "restricted share PIN among 400 facts",
            n_before=50, n_after=350,
            target_user=(
                "The compliance officer sent me the PIN to access the restricted document share "
                "on the intranet where the audit trail files are stored. The PIN is cerise-fox-6 "
                "and it expires in 90 days, at which point I'll need to submit a new access request "
                "and go through the verification process again. The documents in that share are "
                "classified and cannot be downloaded or forwarded outside the corporate network."
            ),
            target_asst="Noted. cerise-fox-6, valid for 90 days, restricted to corporate network access only.",
            query="What PIN gives me access to the restricted document share on the intranet?",
            expected="cerise-fox-6",
            decoys_before={
                12: "The PIN for the HR portal was reset company-wide last quarter as part of a security review. Everyone had to go through the self-service reset flow to regain access.",
                38: "The access code for the legal document repository is shared among members of the legal team and a few authorised business partners. It hasn't been rotated in over a year.",
            },
            decoys_after={
                25: "The PIN for the board materials portal changes before every quarterly meeting. Directors get a text message with the new code 48 hours before the meeting opens.",
                100: "The shared access code for the financial reporting system is something I've had memorised for three years. I'm slightly worried that I'll forget it if I ever have to change it.",
                200: "The code for the archiving system was never communicated to the new joiners who came in after the last restructure. Several people have had to request access retroactively.",
                320: "The client portal PIN is issued per engagement and expires when the project closes. I have to request a fresh one for each new client.",
                345: "The restricted section of the knowledge base requires a PIN in addition to the standard login. It's for documents that haven't been fully approved for general circulation yet.",
            },
            tags=T,
        ),
        _build_case(
            "d400-03", "internal report codename among 400 facts",
            n_before=280, n_after=120,
            target_user=(
                "The legal team has assigned an internal classification codename to the consolidated "
                "risk assessment report so it can be referenced in internal communications without "
                "revealing the subject matter to staff not on the distribution list. The codename is "
                "NIMBUS-FOUR. Any email, calendar invite, meeting agenda or file that relates to "
                "that report must use only the codename in the subject line and filename."
            ),
            target_asst="Understood. NIMBUS-FOUR — to be used as the sole reference in all internal communications about the report.",
            query="What is the internal codename the legal team assigned to the consolidated risk report?",
            expected="NIMBUS-FOUR",
            decoys_before={
                40: "The whistleblower report that came in last spring was given an internal reference name by the investigations team. Only a handful of people know what it relates to.",
                150: "The strategic review commissioned by the board has an internal label that's used on all documentation. The label was chosen to be neutral and give nothing away about the scope.",
                240: "The compliance report from the external auditors is circulated under a case reference number rather than a descriptive title. It makes tracking versions easier.",
                275: "One of the confidential workstreams I was briefed on uses a codename in all project management tools. Even the resource bookings use the codename rather than describing the work.",
            },
            decoys_after={
                20: "The internal investigation into the data handling issue was assigned a label that only the HR director and two executives know the full meaning of.",
                60: "The programme board gave the acquisition project a codename at the very start. It's been used consistently since then and most people in the organisation don't know what it refers to.",
                100: "The codename for the restructuring plan was chosen by the CEO and is known only to the executive team and external advisors. Any leakage of the name itself would be considered a serious breach.",
                118: "The new product codename was used throughout the development phase but will be retired when the public name is announced at launch next quarter.",
            },
            tags=T,
        ),
    ]


def _distractor_800() -> list[dict]:
    """3 cases × 800 distractors in L3 (episodic_only). Extreme stress test —
    preloading takes ~30s per case; search still limited to top-20 by HNSW."""
    T = ["distractor", "d800"]
    return [
        _build_case(
            "d800-01", "calibration reference code among 800 facts",
            n_before=320, n_after=480,
            target_user=(
                "The metrology lab issued the official calibration reference code for the batch of "
                "precision sensors I submitted last quarter for recertification. The code is 5502-WREN "
                "and it needs to be cited in all subsequent measurement records, quality assurance logs "
                "and calibration traceback documents that reference this sensor batch. "
                "The certificate itself is filed under that code in the quality management system "
                "and in the physical archive cabinet in lab three."
            ),
            target_asst="Recorded. 5502-WREN — to be cited in all QA records and traceback documents for this batch.",
            query="What is the calibration reference code issued for the precision sensor batch?",
            expected="5502-WREN",
            decoys_before={
                50: "The calibration certificate for the pressure gauges expired last week. I've submitted a service request to get them recertified before the next production run.",
                160: "The reference standard used for the dimensional checks has its own calibration record that has to be traceable to a national measurement institute. It's up for renewal next month.",
                250: "The calibration reference for the old torque wrenches was lost when the previous quality manager left. We've had to reconstruct the traceability chain from the manufacturer's records.",
                310: "The test equipment register lists calibration due dates for all instruments. Three items are overdue and the quality engineer flagged them in the last internal audit.",
            },
            decoys_after={
                80: "The batch reference number for the last raw materials delivery got transcribed incorrectly on the goods-in form. It took two days to reconcile with the supplier's documentation.",
                240: "The instrument serial number and its calibration status are cross-referenced in the asset management system. When they fall out of sync it causes problems for the traceability audit.",
                400: "The reference material lot number has to be entered on every test record where that material was used. If the lot expires mid-batch the whole batch has to be quarantined.",
                460: "The calibration laboratory assigns its own reference code to each item submitted for certification. That code is different from the equipment's own serial number, which causes occasional confusion.",
                475: "The environmental monitoring sensors were recalibrated after the HVAC upgrade changed the baseline conditions in the lab. The new calibration records supersede the previous ones.",
            },
            tags=T,
        ),
        _build_case(
            "d800-02", "encryption keyholder seed fragment among 800 facts",
            n_before=100, n_after=700,
            target_user=(
                "The security team conducted a key ceremony for the new backup encryption infrastructure "
                "last month. Each of the five designated keyholders received a unique fragment of the "
                "seed phrase; my fragment is jasper-tide-77. "
                "The fragment must not be stored in any digital format — it is written on a laminated "
                "card I keep in the physical safe, and it is completely meaningless without the other "
                "four fragments held by the other keyholders."
            ),
            target_asst="Understood. jasper-tide-77 — your fragment, physical only, kept in the safe.",
            query="What is the encryption seed phrase fragment I hold as a designated keyholder?",
            expected="jasper-tide-77",
            decoys_before={
                20: "The key ceremony for the previous infrastructure cycle was done with only three keyholders. The decision to expand to five for the new system came from the CISO after an external review.",
                65: "The recovery phrase for the hardware security module is split across two sealed envelopes stored in separate physical locations. Opening either one without authorisation triggers an alert.",
            },
            decoys_after={
                50: "The seed phrase backup for the signing infrastructure is reviewed once a year by the security team to ensure all fragments are still accessible and all keyholders are still available.",
                200: "The keyholder rotation policy requires that when a keyholder leaves the organisation, a new ceremony is conducted and all fragments are reissued. This happened twice in the past year.",
                400: "The emergency recovery procedure for the encryption infrastructure requires a quorum of three out of five keyholders. Getting three people together under controlled conditions takes planning.",
                550: "One of the keyholders for the previous cycle stored their fragment digitally against explicit policy. The fragment was considered compromised and the ceremony was redone.",
                680: "The key ceremony was witnessed by two external auditors who certified the process but did not receive any fragments themselves. Their attestation is filed with the security committee.",
                695: "The fragments were generated using a deterministic algorithm applied to a master entropy source. The master source was destroyed immediately after the ceremony was complete.",
            },
            tags=T,
        ),
        _build_case(
            "d800-03", "infrastructure migration work stream codename among 800 facts",
            n_before=560, n_after=240,
            target_user=(
                "The programme office assigned an operational codename to the infrastructure migration "
                "work stream so that progress can be tracked in external-facing status reports and "
                "governance dashboards without revealing the technical scope to stakeholders who are "
                "not on the core delivery team. The codename is HELIOS-DREI. "
                "All project management tickets, sprint boards, release notes and budget line items "
                "for this work stream must use that label from this point forward."
            ),
            target_asst="Noted. HELIOS-DREI — the label for all PM artefacts, sprint boards and budget references for the migration work stream.",
            query="What is the operational codename assigned to the infrastructure migration work stream?",
            expected="HELIOS-DREI",
            decoys_before={
                80: "The cloud migration project at my previous company was also given a codename, though I can't remember what it was now. We used it internally for about eighteen months before the work was complete.",
                250: "The workstream for the platform consolidation was given a label by the portfolio office that nobody on the team particularly liked. It stuck anyway.",
                400: "The programme management office assigned a tracking code to each initiative in the current portfolio. The codes are used in the quarterly reporting pack but rarely in day-to-day conversation.",
                540: "The internal label for the legacy system decommissioning project changed twice before the team settled on a final name. The changes caused tracking issues in the ticketing system.",
            },
            decoys_after={
                30: "The data centre exit programme was referred to by a codename for the first year, then shifted to a public name when it was announced to all staff. The transition caused some confusion in the documentation.",
                100: "The codename for the network refresh initiative was chosen to be geographically neutral since the work spans multiple office locations. The portfolio team vetoed the first two suggestions.",
                180: "The infrastructure security hardening programme runs in parallel with the migration and shares some resources. They have separate codenames to keep the budgets and governance trails distinct.",
                230: "The operational label for the disaster recovery restructure was leaked in a slide deck sent to the wrong distribution list. The programme office had to clarify the scope externally as a result.",
            },
            tags=T,
        ),
    ]


def _build() -> dict[str, list[dict]]:
    """Assemble the dataset registry in stable registration order."""
    return {
        "memory_recall": _memory_recall(),
        "temporal": _temporal(),
        "multi_turn": _multi_turn(),
        "cache": _cache(),
        "prefetch": _prefetch(),
        "multi_hop": _multi_hop(),
        "distractor": _distractor(),
        "distractor_15": _distractor_15(),
        "distractor_20": _distractor_20(),
        "distractor_25": _distractor_25(),
        "distractor_30": _distractor_30(),
        "distractor_50": _distractor_50(),
        "distractor_100": _distractor_100(),
        "distractor_200": _distractor_200(),
        "distractor_400": _distractor_400(),
        "distractor_800": _distractor_800(),
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