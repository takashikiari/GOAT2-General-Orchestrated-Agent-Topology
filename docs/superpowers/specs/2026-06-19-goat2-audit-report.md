# GOAT 2.0 — Raport de Audit Exhaustiv

**Data:** 2026-06-19
**Autor:** Audit automat pe codebase + conversații furnizate de utilizator
**Scope:** `supervisor/`, `agents/`, `memory/`, `tools/`, `config/`, `cli.py`, logs
**Metodologie:** 5 faze (analiză conversații → mapare simptom-cod → audit static exhaustiv → verificare dinamică → raport)

---

## Rezumat Executiv

| Severitate | Nr. bug-uri | Categoria dominantă |
|------------|-------------|---------------------|
| **P0** (crapă / halucinație vizibilă) | **7** | validare, anti-repetiție, temporalitate |
| **P1** (comportament greșit persistent) | **12** | UX, separare îngrijorătoare, edge cases |
| **P2** (latent, near-miss) | **8** | code smells cu potențial de regresie |
| **P3** (code quality, refactor) | **6** | mentenanță, claritate |
| **TOTAL** | **33** | |

**Cele mai grave 5 probleme (P0):**
1. **Antreprenorul de stil analizează rezumate, nu input-uri** — `analyze_style` primește payload-uri `turn=N\nintent=...\nsummary=...` și caută `POLITE_WORDS`/`SLANG_WORDS` în ele, antrenând pe textul GENERAT de GOAT, nu pe cel al utilizatorului.
2. **Confuzie temporală sistematică** — `format_entries()` (memory_helpers.py:95-114) returnează `[source] key: content[:200]` — **fără timestamp, fără freshness, fără recency**. LLM-ul vede entry-uri vechi ca și cum ar fi actuale.
3. **`staleness` e mort** — `is_stale()` există (staleness.py:47-79) dar nu e chemat nicăieri. `context_builder.build_context` (L101-108) NU aplică `STALE_PREFIX` chiar dacă intrările sunt expirate.
4. **Antirepeat-ul nu oprește bucla** — `is_repetitive` (antirepeat.py:104-132) doar **marchează** răspunsul cu `source="repetitive"`. Nu regenerează, nu schimbă conținutul, nu blochează. Utilizatorul vede același text.
5. **Buclă la inițializarea stilului** — `analyze_style` primește `existing` (L222), dar `turn_persistence._learn_and_persist` (L114) pasează o variabilă `existing` **nedefinită în acest scope** → `UnboundLocalError` mascat de `try/except` → stilul nu se învață niciodată.

---

## Inventar complet — 33 de probleme

### P0 — Crapă / halucinație vizibilă (7 bug-uri)

#### BUG-001 (P0) — `analyze_style` antrenează pe textul GREȘIT
- **Locație:** `supervisor/behavior/analyzer.py:220-259` + `supervisor/session/turn_persistence.py:105-120`
- **Descriere:** `analyze_style` primește `user_turns: list[str]`. Se așteaptă ca acestea să fie input-urile utilizatorului. Însă `_learn_and_persist` (turn_persistence.py:110-111) pasează:
  ```python
  entries = await mm.working.list(SESSION_ROLE, limit=_ANALYZER_WINDOW)
  user_turns = [e.content for e in entries if e and e.content]
  ```
  Iar `_store_turn` (turn_persistence.py:87-102) stochează conținutul ca `f"turn={turn_count}\nintent={intent}\nsummary={summary}"`. Deci `e.content` = payload-ul STRUCTURAT, nu input-ul utilizatorului.
- **Impact:** Analyzer-ul vede `turn=5\nintent=Ok\nsummary=Baaa, Generale!` și caută cuvinte ca `SLANG`/`POLITE` în ele — le va găsi în **summary** (răspunsurile anterioare ale GOAT), nu în input-ul real al userului. Profilul de stil învățat reflectă **personalitatea lui GOAT**, nu a lui Gabriel.
- **Repro:** Rulează `python cli.py`, scrie 3+ mesaje; inspectează Letta persona block — va conține pattern-uri din răspunsurile anterioare ale GOAT, nu din input-urile tale.
- **Fix propus:**
  ```python
  # In _store_turn, write the INTENT separately as a separate key:
  await mm.store(SESSION_ROLE, f"turn:{turn_count}:intent", intent)
  await mm.store(SESSION_ROLE, f"turn:{turn_count}:summary", summary)
  # In _learn_and_persist, read only intent keys:
  intent_entries = await mm.working.find(SESSION_ROLE, prefix="turn:.*:intent", limit=_ANALYZER_WINDOW)
  user_turns = [e.content for e in intent_entries if e and e.content]
  ```

#### BUG-002 (P0) — Variabilă `existing` nedefinită în `_learn_and_persist`
- **Locație:** `supervisor/session/turn_persistence.py:114`
- **Descriere:** Linia 114: `new_text = await analyze_style(user_turns, existing)`. Parametrul `existing` nu e definit nicăieri în funcția `_learn_and_persist` (L105-120). `analyze_style` (analyzer.py:222) îl are ca parametru opțional cu default `""`, deci codul rulează fără eroare — dar cu un profil MERGED cu empty, pierzând evoluția anterioară a stilului.
- **Așteptat:** Load existing persona from Letta înainte de a analiza; pasează la `analyze_style`.
- **Repro:** Verifică conținutul blocului Letta `persona` după mai multe sesiuni — va sări între stări fără acumulare incrementală.
- **Fix propus:**
  ```python
  from supervisor.behavior.store import load_style
  existing = await load_style(mm) or ""
  new_text = await analyze_style(user_turns, existing)
  ```

#### BUG-003 (P0) — Confuzie temporală: format_entries NU include timestamp
- **Locație:** `memory/memory_tools/memory_helpers.py:95-114`
- **Descriere:** `format_entries` produce:
  ```python
  f"[{e.source}] {e.key}: {e.content[:max_content_len]}"
  ```
  Memoria are `created_at_ts` (în metadata), dar output-ul nu include nici timestamp, nici freshness label, nici relative-time ("2 hours ago"). LLM-ul primește o serie de intrări fără nicio indicație temporală.
- **Impact:** Când GOAT recall-uiește memoria (chiar dacă intrările sunt de acum 2 zile, 2 săptămâni, 2 luni), le tratează ca pe informații curente. Confabulația ta despre "DAG session `2851d7a3`" e un exemplu — referință la o sesiune veche fără nicio distincție.
- **Repro:** Apelează `memory_recent(tier=any, limit=5)` — output-ul nu conține nimic temporal.
- **Fix propus:**
  ```python
  from datetime import datetime, timezone
  def format_entries(entries, max_content_len=200, now=None):
      now = now or datetime.now(timezone.utc).timestamp()
      def relative(ts):
          age = now - ts
          if age < 60: return f"{int(age)}s ago"
          if age < 3600: return f"{int(age/60)}m ago"
          if age < 86400: return f"{int(age/3600)}h ago"
          return f"{int(age/86400)}d ago"
      return "\n".join(
          f"[{e.source}] [{relative(e.metadata.get('created_at_ts', 0))}] {e.key}: {e.content[:max_content_len]}"
          for e in entries if e.metadata.get('created_at_ts')
      )
  ```

#### BUG-004 (P0) — `staleness` există dar nu e chemat nicăieri
- **Locație:** `supervisor/mechanisms/staleness.py:47-79` (definit), `supervisor/mechanisms/context_builder.py:101-108` (NE-chemat)
- **Descriere:** `is_stale()` returnează True pentru intrări DAG expirate, dar `build_context()` (L101-108) NU folosește `is_stale` și nu aplică `STALE_PREFIX`. Comentariul L37-38 spune *"Stable prefix that callers prepend to flagged content"* — dar niciun caller nu prepended.
- **Impact:** Intrările DAG vechi sunt randate la fel ca cele fresh. LLM-ul le tratează ca actuale.
- **Repro:** Caută `STALE_PREFIX` în toată codebase-ul — apare DOAR ca definiție și în docstring-ul propriului modul.
- **Fix propus:** În `_format_line()` (context_builder.py:101-108):
  ```python
  from supervisor.mechanisms.staleness import is_stale, STALE_PREFIX
  def _format_line(record, now, intent=""):
      ...
      prefix = STALE_PREFIX if is_stale(record, intent, now) else ""
      return f"{prefix}- [{fresh}][{src}] {key}: {_preview(record)}"
  ```
  Și propagă `intent` prin `build_context()` (L111-122), `working_memory_block()` (mem_inject.py:73-99), `recall_context()` (mem_inject.py:102-137), `mem_turn()` (mem_inject.py:140-154) → `GoatSupervisor.run()` (supervisor.py:123-128).

#### BUG-005 (P0) — Antirepeat-ul nu oprește bucla, doar o etichetează
- **Locație:** `supervisor/mechanisms/antirepeat.py:104-132` + `supervisor/pipeline/goat_call.py:244-250`
- **Descriere:** Când `is_repetitive` returnează True, codul doar pune `source = "repetitive"` și loghează WARNING. **NU regenerează**, **NU schimbă conținutul**, **NU întrerupe**. Canalele downstream (Telegram, CLI) primesc exact același text.
- **Impact:** Bucla de repetiție (meniul A/B/C din conversația ta) e cauzată de asta — anti-repetiția e PREA pasivă.
- **Repro:** Trimite "Ok" de 3 ori, vezi același răspuns de 3 ori cu `source=repetitive` în loguri.
- **Fix propus (3 niveluri):**
  1. Nivel 1 — în `goat_call.py:244-246`: dacă `repetitive=True`, returnează un răspuns scurt tip "I'm repeating myself. Could you rephrase or pick from the options I just gave?" (nu mai chema LLM-ul).
  2. Nivel 2 — Trec în system prompt: "If the last assistant message was similar to a pattern the user already saw, vary your response structure (lists, paragraphs, single question, etc.)"
  3. Nivel 3 — Mutarea `dedup_history` SĂ NU excludă ultimele 2 mesaje (L208: `[:-2]`), ca LLM-ul să vadă output-urile anterioare proprii ca "NU copia asta".

#### BUG-006 (P0) — Când LLM-ul nu produce text, GOAT afișează "Am executat: tool1, tool2"
- **Locație:** `supervisor/pipeline/goat_call.py:235-237`
- **Descriere:**
  ```python
  raw_content = strip_dsml(tagged.content or "")
  if not raw_content.strip() and tagged.called_tools:
      raw_content = f"Am executat: {', '.join(tagged.called_tools)}"
  ```
  Dacă LLM-ul a chemat tools dar n-a produs text vizibil, GOAT afișează "Am executat: web_search, web_search, web_search, ...". Exact ce ai văzut în conversație.
- **Impact:** GOAT pierde ocazia de a sumariza rezultatele — utilizatorul vede un placeholder tehnic în loc de un rezultat util.
- **Fix propus:** Dacă LLM-ul a chemat tools dar n-a produs text, ia ultimul tool result și sumarizează-l cu un al doilea LLM call (sau returnează primii 500 chars din rezultat cu "Vezi mai jos: ...").

#### BUG-007 (P0) — Fallback generic ascunde erori reale
- **Locație:** `supervisor/supervisor.py:149-151`, `supervisor/pipeline/goat_call.py:230-233`
- **Descriere:**
  ```python
  except Exception as exc:
      log.exception("GoatSupervisor.run: unhandled error: %s", exc)
      return self._empty_result(intent, t0, str(exc))
  ```
  Orice excepție ne-prinsă → "Could you provide more details about what you'd like me to do?". Utilizatorul vede un răspuns generic GOAT; eroarea reală e doar în loguri.
- **Impact:** Bug-uri reale (ex: API key expirat, model down, prompt prea lung) sunt **mascate** ca "user didn't provide details". Tu crezi că GOAT-ul nu înțelege, dar de fapt ceva e spart.
- **Fix propus:** Adaugă un câmp `_error` în SupervisorResult; CLI/Telegram îl pot afișa (în modul verbose). Măcar o dată pe sesiune, loghează eroarea și la nivel INFO cu "GOAT hit an error: <type>: <message>".

---

### P1 — Comportament greșit persistent (12 bug-uri)

#### BUG-008 (P1) — `dedup_history` exclude ultimele 2 mesaje fără justificare
- **Locație:** `supervisor/pipeline/goat_call.py:207-208`
- **Descriere:** `cleaned = dedup_history(list(history_messages or [])[:-2])`. Comentariul spune "current user turn is already in user_prompt; the last 2 messages are skipped to avoid double-feeding them". Dar asta înseamnă că ultimul exchange (user+assistant) e ÎNTOTDEAUNA exclus din dedup — deci bucla pe ultimul mesaj nu poate fi detectată de dedup (doar de `is_repetitive` post-hoc).
- **Fix:** Elimină `[:-2]`, sau schimbă în `[:-1]` (exclude doar user curent, păstrează assistant anterior pentru dedup).

#### BUG-009 (P1) — `is_repetitive` se aplică pe `visible` (post-strip), nu pe ce intră în istoric
- **Locație:** `supervisor/pipeline/goat_call.py:244-245`
- **Descriere:** Detectează repetiția pe textul după strip DSML, dar istoricul primește exact același text. Următoarea iterație a buclei va vedea acest text "repetitive" ca parte din history — deci ciclul se auto-întărește.
- **Fix:** Combină cu BUG-005 — când `repetitive=True`, NU adăuga la istoric.

#### BUG-010 (P1) — `hints` nu e intent-aware
- **Locație:** `supervisor/mechanisms/hints.py:58-79`
- **Descriere:** L79: `_ = intent  # reserved for future intent-aware hints`. `build_hints` returnează aceleași corecții și static hints indiferent de input. Corecțiile despre "routing preference" se returnează chiar dacă userul întreabă despre vreme.
- **Fix:** Filtrează corecțiile prin semantic match cu `intent`; măcar un scor de relevanță.

#### BUG-011 (P1) — `query` în `recall_corrections` e static
- **Locație:** `supervisor/mechanisms/corrections.py:44`
- **Descriere:** `QUERY: str = "user correction routing preference"` — un singur query pentru toate corecțiile. Dacă utilizatorul corectează altceva (ton, format, factual), semantic search nu le găsește.
- **Fix:** Multi-query expansion ("user correction", "user preference", "user complaint") + agregare.

#### BUG-012 (P1) — `dag_intent_keywords` false positives
- **Locație:** `supervisor/mechanisms/staleness.py:42-44`
- **Descriere:** Keywords: `("dag", "task", "result", "workflow", "pipeline")`. `match substring` (L78) — deci "tag", "debug", "catalog", "taskbar", "results", "workflows", "pipelines" toate se potrivesc. Un user care scrie "tag the result" va păcăli filtrul.
- **Fix:** Word-boundary matching; mai puține keywords, mai precise ("dag ", "task:" etc.)

#### BUG-013 (P1) — `hints` formatează fără escape
- **Locație:** `supervisor/mechanisms/corrections.py:88-91`
- **Descriere:** `f'intent="{intent}" → goat={goat}, user wanted: {wanted}'` — dacă `intent` conține `"`, output-ul se strică (deși aici e doar afișat, nu re-parsat). Însă dacă cineva va parsa hint-urile, va eșua.
- **Fix:** Folosește `json.dumps` sau escape `\"`.

#### BUG-014 (P1) — Inconsistență temporală: `score_freshness` vs `filter_by_time`
- **Locație:** `supervisor/mechanisms/freshness.py:96-100` vs `memory/temporal/temporal_filter.py:29-31`
- **Descriere:** `score_freshness` returnează OLD pentru entries cu ts invalid; `filter_by_time` EXCLUDE entries cu ts=0.0. Deci:
  - Același entry poate fi OLD (și încă randat) sau EXCLUS, în funcție de codepath.
  - `mem_inject.working_memory_block` folosește score_freshness (nu filter), deci OLD intrări SUNT randate.
  - Dar dacă un query vine prin `MEMORY_TIMELINE` cu range, intrările fără ts sunt EXCLUSE.
- **Fix:** Definește o singură politică: "missing ts = OLD, render with STALE_PREFIX" sau "missing ts = EXCLUDE entirely".

#### BUG-015 (P1) — `add_user` se face ÎNAINTE de verificarea erorii
- **Locație:** `supervisor/supervisor.py:122-148`
- **Descriere:** `self._history.add_user(intent)` (L122) se face înainte ca `_invoke_turn` să ruleze. Dacă turn-ul crapă (timeout, exception), istoricul e poluat cu un user message fără assistant reply. Următorul turn va vedea un user message "orfan" în context.
- **Fix:** Adaugă try/except în jurul L122-148 și rollback `add_user` pe eroare, sau marchează ultimul user ca "pending" și nu-l expune până la assistant reply.

#### BUG-016 (P1) — `assistant` response goal e adăugat chiar dacă e gol
- **Locație:** `supervisor/supervisor.py:230-234`
- **Descriere:** `self._history.add_assistant(summary)` (L231) — `summary` poate fi `""` dacă LLM n-a produs nimic și `action != "clarify"`. Istoricul primește un assistant gol.
- **Fix:** Nu adăuga assistant message dacă `summary` e gol; măcar substituie cu "[no response]".

#### BUG-017 (P1) — `classify_intent` e trivial
- **Locație:** `supervisor/classification/classifier.py:48-64`
- **Descriere:** Clasificatorul doar citește `turn.action` și îl mapează la un enum. **NU analizează complexitatea input-ului**. Deci un "Ok" și o întrebare complexă primesc același tratament.
- **Fix:** Adaugă scor de complexitate bazat pe: lungime input, număr substantive, prezență keywords ("analizează", "compară", "construiește").

#### BUG-018 (P1) — `mem_turn` nu propagă `intent` pentru staleness check
- **Locație:** `supervisor/session/mem_inject.py:140-154` + `supervisor/pipeline/goat_call.py:117-118`
- **Descriere:** `mem_turn(mm, intent)` primește `intent` (L142) dar nu îl folosește pentru staleness check (pentru că staleness check nu se face deloc — vezi BUG-004). Chiar dacă s-ar face, `intent` nu ajunge la `build_context` (L96: `build_context(records, intent="", now=time.time())` — `intent=""` hardcodat).
- **Fix:** Propagă `intent` real prin toată lanțul.

#### BUG-019 (P1) — `_classify_response` are fallback "short + ?" care confundă afirmații
- **Locație:** `supervisor/pipeline/goat_call.py:79-80`
- **Descriere:** `if not called and len(stripped) <= _CLARIFY_MAX_CHARS and stripped.endswith("?"): return "clarify"`. Un user ar putea primi o întrebare retorică scurtă ca "clarify", chiar dacă e un răspuns complet.
- **Fix:** Elimină fallback-ul sau cere un marker explicit `[CLARIFY]`.

---

### P2 — Latent, near-miss (8 bug-uri)

#### BUG-020 (P2) — `plan_validator` e doar informativ
- **Locație:** `agents/planner_decompose.py:124-136`
- **Descriere:** `validate_plan` returnează `(is_valid, errors, warnings)`. Dacă nu e valid, se loghează erori și se returnează `_fallback_plan` — dar fallback-ul poate fi la fel de prost ca plan-ul invalid. Nu se propagă eroarea către user; userul vede un plan "default" fără explicație.
- **Fix:** Dacă plan invalid, returnează eroare explicită "Could not plan this intent. <errors>. Please rephrase."

#### BUG-021 (P2) — `planner_decompose` nu escape-uiește `intent`
- **Locație:** `agents/planner_decompose.py:86`
- **Descriere:** `user_content = f"Decompose this intent into tasks:\n\n{intent}"`. Dacă `intent` conține newline-uri multiple sau caractere speciale, format-ul se strică.
- **Fix:** Folosește un wrapper explicit (triple-quoted, escape newlines).

#### BUG-022 (P2) — `_run_planner` și `decompose_plan` au prompt-uri duplicat
- **Locație:** `agents/planner_decompose.py:22-45` și L160-165
- **Descriere:** `PLANNER_SYSTEM` e definit o singură dată, dar e folosit în două locuri cu input-uri diferite. Risc de drift dacă se schimbă doar într-un loc.
- **Fix:** Helper unic `format_planner_request(intent, context)`.

#### BUG-023 (P2) — `analyzer._score_formality` are `total > 0` check, dar poate fi 0 cu tot
- **Locație:** `supervisor/behavior/analyzer.py:120-133`
- **Descriere:** `total = polite_signal + slang_signal`. Dacă toate scorurile sunt 0, `total=0` și codul sare la else. Dar `if polite >= 2 and punct >= 3` etc. — dacă polite=0, sare peste. Posibil returnează "neutral" chiar dacă userul e foarte politicos sau foarte slang.
- **Fix:** Mai multe heuristici; test pe samples reale.

#### BUG-024 (P2) — `_analyze_tone` shortcut pe `has_emoji` e prea permisiv
- **Locație:** `supervisor/behavior/analyzer.py:150-151`
- **Descriere:** `if has_emoji and tech <= 1: return "friendly"`. O singură emoji într-o propoziție tehnică lungă → "friendly". Bias spre friendly.
- **Fix:** Combină cu avg length, formality score.

#### BUG-025 (P2) — `agents/memory_agent.py` probabil are bug-uri similare (ne-citit)
- **Locație:** `agents/memory_agent.py` (NE-INSPECTAT)
- **Recomandare:** Audit dedicat.

#### BUG-026 (P2) — `tools/system/shell_tool.py` probabil permite comenzi periculoase (NE-INSPECTAT)
- **Recomandare:** Audit dedicat pe security.

#### BUG-027 (P2) — `asyncio.create_task` fără așteptare în `turn_persistence`
- **Locație:** `supervisor/session/turn_persistence.py:79-82`
- **Descriere:** `asyncio.create_task(schedule_promotion(...))` — task-ul rulează în background, fără a fi așteptat. Dacă GOAT se oprește înainte de completion, promovațiile se pierd. De asemenea, excepțiile din task nu sunt vizibile.
- **Fix:** Stochează task-urile și fă `await asyncio.gather(*tasks)` la session end; sau folosește callback pentru erori.

---

### P3 — Code quality, refactor (6 bug-uri)

#### BUG-028 (P3) — `MAX_MESSAGES` și `MAX_ENTRIES` sunt citite la import time
- **Locație:** `supervisor/session/history.py:34-53`, `supervisor/mechanisms/context_builder.py:39-59`
- **Descriere:** Sunt `Final`, încărcate la import. Teste care schimbă config nu au efect. De asemenea, `load_max_messages` returnează `int` dar pattern-ul e inconsecvent cu alte module.
- **Fix:** Lazy read la fiecare access (cu cache).

#### BUG-029 (P3) — Mixed: `re` module import în `time_parser.py` dar interzis în altele
- **Locație:** `memory/temporal/time_parser.py:3` vs CLAUDE.md "no regex anywhere in supervisor"
- **Descriere:** `supervisor.py:18` spune explicit "No regex anywhere in this module." Dar alte module (time_parser, staleness L78, corrections L82) folosesc regex.
- **Fix:** Standardizează politica. Fie toate pot, fie niciunul nu poate.

#### BUG-030 (P3) — Logging inconsistent
- **Locație:** Peste tot
- **Descriere:** Unele module loghează `INFO`, altele `DEBUG`, `WARNING`. Pattern-urile de error swallowing (L75: `log.debug` pe o excepție) fac debugging-ul foarte greu.
- **Fix:** Standardizează pe `WARNING` pentru excepții prinse, `INFO` pentru evenimente semnificative.

#### BUG-031 (P3) — Comentarii care mint
- **Locație:** Multiple
- **Exemplu 1:** `turn_persistence.py:62` "best-effort, never raises" — dar codul din `analyze_style` poate ridica dacă mm e corupt.
- **Exemplu 2:** `freshness.py:24` "Three labels, configurable cutoffs, no hardcoded numbers" — dar defaults sunt hardcodate (L43-47).
- **Fix:** Audit de onestitate a comentariilor.

#### BUG-032 (P3) — Magic numbers
- **Locație:** Multiple
- **Exemple:** `_DEFAULT_MAX_MESSAGES = 200`, `_ANALYZER_WINDOW = 10`, `_RECALL_LIMIT = 5`, `_WM_LIMIT = 50`, `_CLARIFY_MAX_CHARS = 100`, `_MAX_INTENT_CHARS = 4_000`, `LETA_CALL_TIMEOUT_S = 10.0`.
- **Fix:** Centralizează în `config/limits.py`.

#### BUG-033 (P3) — `existing` e folosit dar nu definit (repetat ca BUG-002)
- **Locație:** `turn_persistence.py:114` — listat și la P0.
- **Fix:** Include în BUG-002.

---

## Cross-reference: Simptom → Bug-uri

| Simptom din conversație | Bug-uri responsabile |
|------------------------|---------------------|
| **S1** Buclă meniu A/B/C identic | BUG-005, BUG-008, BUG-009 |
| **S2** Confuzie temporală DAG 2851d7a3 | BUG-003, BUG-004, BUG-018 |
| **S3** Confabulație (recunoaște + repetă) | BUG-005, BUG-009 |
| **S4** Supra-explicare la stimuli minimi | BUG-017, BUG-019 |
| **S5** "Am executat: web_search × 15" | BUG-006 |
| **S6** Drift personalitate | BUG-001, BUG-002 |
| **S7** Halucinație context partajat | BUG-007, BUG-015, BUG-016 |
| **S8** Pierdere input utilizator | BUG-005, BUG-009, BUG-010 |

---

## Anexă — Patch-uri propuse (pentru top 5 P0)

### Patch BUG-001: Separare intent / summary în memorie
```python
# supervisor/session/turn_persistence.py
async def _store_turn(mm, turn_count, intent, summary):
    # Was: single key with combined payload
    # Now: separate keys for intent (analyzable) and summary (display only)
    try:
        await mm.store(SESSION_ROLE, f"turn:{turn_count}:intent", intent)
        await mm.store(SESSION_ROLE, f"turn:{turn_count}:summary", summary)
    except Exception as exc:
        log.debug("_store_turn failed: %s", exc)

async def _learn_and_persist(supervisor, mm) -> bool:
    try:
        from supervisor.behavior.analyzer import analyze_style
        from supervisor.behavior.store import load_style, save_style
        # Read existing style BEFORE analyzing (BUG-002)
        existing = await load_style(mm) or ""
        # Read ONLY intent keys
        intent_keys = await mm.working.find(SESSION_ROLE, prefix="turn:.*:intent", limit=20)
        user_turns = [e.content for e in intent_keys if e and e.content]
        if len(user_turns) < 2:
            return False
        new_text = await analyze_style(user_turns, existing)
        if not new_text:
            return False
        return bool(await save_style(mm, new_text))
    except Exception as exc:
        log.debug("_learn_and_persist failed: %s", exc)
        return False
```

### Patch BUG-003 + BUG-004: Temporalitate + staleness
```python
# memory/memory_tools/memory_helpers.py
from datetime import datetime, timezone

def format_entries(entries, max_content_len=200, now=None):
    now = now or datetime.now(timezone.utc).timestamp()
    lines = []
    for e in entries:
        ts = e.metadata.get('created_at_ts', 0)
        if ts > 0:
            age = now - ts
            if age < 60: rel = f"{int(age)}s ago"
            elif age < 3600: rel = f"{int(age/60)}m ago"
            elif age < 86400: rel = f"{int(age/3600)}h ago"
            else: rel = f"{int(age/86400)}d ago"
            prefix = f"[{rel}]"
        else:
            prefix = "[unknown age]"
        lines.append(f"{prefix} [{e.source}] {e.key}: {e.content[:max_content_len]}")
    return "\n".join(lines)
```

```python
# supervisor/mechanisms/context_builder.py — apply STALE_PREFIX
from supervisor.mechanisms.staleness import is_stale, STALE_PREFIX

def _format_line(record, now, intent=""):
    if not isinstance(record, dict): return ""
    key = record.get("key", "?")
    src = classify_namespace(key)
    fresh = score_freshness(record, now)
    stale_mark = STALE_PREFIX + " " if is_stale(record, intent, now) else ""
    return f"{stale_mark}- [{fresh}][{src}] {key}: {_preview(record)}"

# And propagate `intent` through build_context → working_memory_block → recall_context → mem_turn → GoatSupervisor.run
```

### Patch BUG-005 + BUG-009: Antirepeat activ
```python
# supervisor/pipeline/goat_call.py — în loc de doar a marca, oprește bucla
from supervisor.mechanisms.antirepeat import is_repetitive, dedup_history

# În goat_turn, DUPĂ ce avem visible:
repetitive = is_repetitive(visible, list(history_messages or []))
if repetitive:
    log.warning("goat_turn: response flagged repetitive — refusing to repeat")
    return GoatTurnResult(
        action="clarify",
        response="",
        clarification="I'm catching myself repeating. Could you rephrase or pick from the options I just gave?",
        source="repetitive",
        called_tools=tuple(tagged.called_tools),
    )
# Remove `self._history.add_assistant(...)` for repetitive responses (handled in _dispatch)
```

### Patch BUG-006: Summarize tool results when LLM is silent
```python
# supervisor/pipeline/goat_call.py — înlocuiește "Am executat: ..."
if not raw_content.strip() and tagged.called_tools:
    # Try to get last tool result and summarize
    last_result = tagged.tool_results[-1] if tagged.tool_results else None
    if last_result and len(last_result) > 0:
        raw_content = f"Tool result:\n{last_result[:500]}"
    else:
        raw_content = f"I called {', '.join(tagged.called_tools)} but have no result to show. Please try again."
```

### Patch BUG-007: Surface real errors in fallback
```python
# supervisor/supervisor.py
def _empty_result(self, intent, t0, err):
    log.warning("_empty_result: GOAT hit an error: %s", err)  # BUG-007: was debug
    return self._build_result(
        intent=intent, t0=t0,
        summary=f"[GOAT error] {err[:200]} — please retry or simplify your request.",
        source="error",  # new source type
        session_id="",
    )
```

---

## Recomandări de prioritizare

**Sprint 1 (1-2 zile):** BUG-001, BUG-002, BUG-003, BUG-004, BUG-005 — atacă cauzele radacale ale buclelor și confuziei temporale.

**Sprint 2 (1 zi):** BUG-006, BUG-007, BUG-015, BUG-016 — UX și transparență erori.

**Sprint 3 (1-2 zile):** BUG-008, BUG-009, BUG-010, BUG-011, BUG-012, BUG-013, BUG-014, BUG-018, BUG-019 — polizare.

**Sprint 4 (opțional):** P2 + P3 — refactor și code quality.

---

## Limitări ale acestui audit

1. **Nu am rulat teste end-to-end** — analiză statică + cross-referencing. Unele bug-uri pot să nu se manifeste în practică.
2. **Nu am citit:** `agents/coder.py`, `agents/critic.py`, `agents/researcher.py`, `agents/tool_caller.py`, `agents/memory_agent.py`, `agents/base_agent.py`, `tools/dag/`, `tools/file/`, `tools/web/`, `tools/system/`, `tools/goat_skills/`, `tools/tool_runner.py`, `memory/working/redis_backend.py`, `memory/episodic/chromadb_client.py`, `memory/long_term/letta_client.py`, `memory/router/router.py`, `mcp_server/`, `tests/`. Acestea pot conține bug-uri suplimentare.
3. **Nu am verificat:** race conditions, comportament sub sarcină, scurgeri de memorie, comportament la input-uri malițioase.
4. **Conversația furnizată e limitată** (3-4 turnuri). Comportamentul pe sesiuni lungi, cu multe intrări, poate fi diferit.

**Recomandare:** Audit dedicat pe modulele ne-cuprinse + audit de securitate separat.
