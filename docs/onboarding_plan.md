# Plan Onboarding User-Friendly — GOAT 2.0

> Scop: orice utilizator nou să poată vorbi cu GOAT în maxim 60 de secunde,
> fără să citească documentație, fără să știe ce e un DAG, fără să vadă erori.

---

## 1. Arhitectura reală (ce vede utilizatorul)

```
Tu (user) ──► GOAT (supervisor) ──► DAG (agenți)
                    │
                    ├── CONVERSATIONAL → răspund direct cu tool-uri
                    ├── ANALYTICAL    → DAG ușor (≤2 task-uri)
                    └── COMPLEX       → DAG complet (planner → researcher → critic → sumar)
```

**GOAT** e singurul interlocutor. DAG-ul e invizibil. Utilizatorul NU trebuie să știe nimic despre el.

---

## 2. Etapele onboarding-ului

### Etapa 0 — Prima interacțiune (t=0s)

**Ce se întâmplă:**
1. User scrie primul mesaj (orice — "salut", "cine ești?", "fă x")
2. `init_session()` încarcă profilul, istoricul, stilul din Letta
3. `classify_intent()` decide ruta (CONVERSATIONAL de obicei)
4. GOAT răspunde direct cu tool-uri disponibile

**Probleme curente:**
- Dacă Letta e down → `init_session()` returnează `""` — OK, dar nu știm
- Dacă primul mesaj e o comandă complexă → merge direct în DAG fără confirmare
- Userul nu știe ce POATE face GOAT

**Soluții propuse:**

#### a) Mesaj de bun venit LA PRIMA INTERACȚIUNE (nu la fiecare sesiune)

În `identity.py`, `GOAT_SYSTEM` include deja identitatea. Adăugăm un flag în working memory:
- `onboarding_done` = false la prima sesiune
- După primul răspuns cu succes → `onboarding_done` = true

Când `onboarding_done` = false, GOAT adaugă la primul răspuns:
```
╭──────────────────────────────────────╮
│  🐐 GOAT — mereu gata                │
│                                      │
│  Pot citi fișiere, căuta pe net,     │
│  scrie cod, verifica memorie,        │
│  analiza, compara, implementa.       │
│                                      │
│  Scrie ce ai nevoie.                 │
╰──────────────────────────────────────╯
```

**Implementare:** 
- Fișier: `supervisor/identity.py`
- Funcția `conv_result()` verifică `onboarding_done` din working memory
- După primul răspuns, setează `onboarding_done = true`

#### b) Detectare primul mesaj și adaptare prompt

În `classifier.py`, dacă e primul mesaj din sesiune (history gol) și intentul e vag:
- Forțează CONVERSATIONAL (nu DAG)
- Adaugă în context: "Acesta e primul mesaj al utilizatorului. Prezintă-te succint și întreabă ce are nevoie."

---

### Etapa 1 — Onboarding activ (primele 3 interacțiuni)

**Probleme curente:**
- Userul nu știe că poate cere direct: "citește fișierul X", "caută Y pe net"
- `classify_direct_request()` din `supervisor.py` face bypass DAG doar pentru `memory_recent`, `memory_get`, `file_read`
- Userul nu știe că poate comanda: "fă un script Python care..." fără să specifice "analytical" sau "complex"

**Soluții propuse:**

#### a) Extindere direct request bypass

Adaugă în `request_classifier.py` detectare pentru:
- `web_search` — "caută X", "caută pe net X", "google X"
- `file_write` — "scrie în fișierul X", "salvează în X"
- `file_search` — "găsește fișierul X", "unde e X?"

Acestea pot fi răspunsuri directe fără DAG.

#### b) Hint-uri adaptive în primele 3 răspunsuri

În `behavior_session.py` sau `session.py`, după `store_turn()`, verifică:
- Dacă `turn < 4` (primele 3 răspunsuri)
- Adaugă un hint subtil la sfârșit, rotind:
  1. "🐐 Pot citi orice fișier din workspace — doar spune-mi ce."
  2. "🐐 Caut pe net în timp real — dă-mi un query."
  3. "🐐 Pot scrie cod, analiza, compara — spune-mi ce ai nevoie."

**Implementare:** în `supervisor/identity.py`, funcția `direct_response()` primește un flag `show_hint` care adaugă hint-ul.

---

### Etapa 2 — Feedback invizibil (după fiecare DAG)

**Probleme curente:**
- Când DAG-ul execută task-uri, userul vede doar rezultatul final
- Dacă un agent eșuează, userul vede "Not available. Tools called: ..." — mesaj tehnic, neprietenos
- Dacă criticul respinge output-ul, userul nu știe că s-a re-executat

**Soluții propuse:**

#### a) Mesaje prietenoase în loc de erori tehnice

În `supervisor.py`, `_unverified_summary()` returnează:
```
"Not available. researcher via web_search: net_error."
```
Înlocuim cu:
```
"Am încercat să caut pe net, dar conexiunea a picat. Încearcă din nou?"
```

**Mapare erori → mesaje prietenoase** (în `_REASON_LABELS`):
- `net_error` → "Conexiunea la net a picat. Mai încearcă."
- `empty_file_read` → "Am găsit fișierul, dar e gol."
- `unverified_execution` → "Am încercat, dar ceva n-a mers. Reformulează?"
- `missing_tool_params` → "Am înțeles, dar nu am destule detalii. Poți da mai multe informații?"
- `stale_memory` → "Am găsit informații vechi în memorie. Vrei să actualizez?"

#### b) Status subtil pentru DAG

În loc să afișeze "DAG execution complete" sau detalii tehnice, GOAT poate adăuga un singur cuvânt la final:
- `✓` — totul OK
- `⚠` — avertisment minor (critic a găsit ceva)
- `✗` — eroare, dar am încercat

Acest lucru se face în `supervisor.py`, la construirea `SupervisorResult.summary`.

---

### Etapa 3 — Help on demand

**Probleme curente:**
- Userul nu are un loc unde să vadă ce poate face GOAT
- Comenzi ca `help`, `?`, `ajutor` nu sunt tratate special

**Soluții propuse:**

#### a) Detectare help în classifier

În `classifier.py` sau `request_classifier.py`, adaugă detectare pentru:
- `help`, `?`, `ajutor`, `ce poți face`, `capabilities`, `commands`

Când detectează, returnează un răspuns predefinit cu lista de capabilități.

#### b) Fișier de help dinamic

Citește `docs/CAPABILITIES.md` (dacă există) și îl include în răspuns.

---

## 3. Plan de implementare (pe fișiere)

| Fișier | Ce se modifică | Prioritate |
|--------|---------------|------------|
| `supervisor/identity.py` | Adăugat mesaj bun venit + hint-uri adaptive + flag `onboarding_done` | **High** |
| `supervisor/request_classifier.py` | Extindere direct bypass: web_search, file_write, file_search | **High** |
| `supervisor/supervisor.py` | Mesaje prietenoase în `_unverified_summary()` + `_REASON_LABELS` | **Medium** |
| `supervisor/classifier.py` | Detectare help/ajutor + forțare CONVERSATIONAL la primul mesaj vag | **Medium** |
| `supervisor/session.py` | Hint-uri adaptive după `store_turn()` pe primele 3 turnuri | **Low** |
| `docs/CAPABILITIES.md` | Fișier nou cu lista de capabilități | **Low** |

---

## 4. Reguli finale

1. **Nicio întrebare la final** — conform preferinței tale, GOAT nu termină cu "Ce mai faci?" sau "Ce zici?"
2. **Mesajele de eroare sunt în limba utilizatorului** — dacă userul vorbește română, erorile sunt în română
3. **DAG-ul e invizibil** — utilizatorul NU trebuie să știe de existența agenților
4. **Onboarding-ul se face în maxim 3 mesaje** — după aia, GOAT e un asistent normal
5. **Fără technical jargon** — "DAG", "workflow", "agent", "critic", "semaphore" nu apar niciodată în fața userului

---

## 5. Implementation Log (English)

### Step 3 — Help Detection & First-Message Guard

**Date:** 2025-01-XX
**Files modified:**
- `supervisor/classifier.py` — Added help detection and first-message guard
- `docs/CAPABILITIES.md` — Created new capabilities reference file
- `docs/onboarding_plan.md` — This implementation log

#### Changes to `supervisor/classifier.py`

**1. Help detection (`_is_help_query`)**

Added a pattern-matching function that runs **before** the LLM classification call. It detects help-related queries using regex patterns:

| Pattern | Matches |
|---------|---------|
| `^\?\s*$` | Just "?" |
| `\bhelp\b` | "help", "help me" |
| `\bajut[oai]r\b` | "ajutor", "ajută" |
| `\bce\s+po[iț]i?\s+(face\|faci)\b` | "ce poți face", "ce poți să faci" |
| `\bce\s+știi\s+să\s+(faci\|fac)\b` | "ce știi să faci" |
| `\bcapabilities\b` | "capabilities" |
| `\bcommands?\b` | "command", "commands" |
| `\b(what\s+)?(can\s+you\s+do\|are\s+you\s+capable\s+of)\b` | "what can you do" |
| `\b(arată\|show)\s+(ce\|what)\s+(poți\|can)\b` | "arată ce poți" |
| `\b(how\s+to\|how\s+do\s+i\|cum\s+să)\s+(use\|folosesc\|utilizez)\b` | "how to use" |

When a help query is detected, `classify_intent()` returns `IntentDepth.CONVERSATIONAL` immediately — no LLM call is made. This ensures the user gets a friendly introduction with capabilities listed, rather than being routed to a DAG.

**2. First-message guard (`_is_vague_first_message`)**

Added a new optional parameter `is_first_message: bool = False` to `classify_intent()`. When `True` and the intent matches vague/exploratory patterns, the function forces CONVERSATIONAL mode.

Vague patterns include:
- Greetings: "salut", "bună", "hello", "hi", "hey"
- Apologies: "scuze", "sorry"
- Tests: "test", "testing"
- Simple affirmations: "da", "nu", "yes", "no", "ok"
- Identity questions: "cine ești", "who are you"
- Definition questions: "ce este asta", "what is this"
- Just punctuation: "?", "!", "."
- Empty/whitespace-only messages

The guard checks both pattern matching and message length (≤3 words) to avoid false positives on legitimate short commands.

**3. Existing logic preserved**

- The LLM-based classification path is **unchanged** — it still runs for non-help, non-vague intents
- The fallback safeguard (CONVERSATIONAL on unparseable LLM output) is preserved
- The `IntentDepth` enum and all imports remain the same
- The `registry` parameter and Phase 4 injection are untouched

#### New file: `docs/CAPABILITIES.md`

Created a structured capabilities reference organized by category:
- **File Operations** — read, write, create, list, search, grep, info, read_lines
- **Web Search** — real-time web queries
- **Memory** — recent memory, get facts, search, store
- **Code & Analysis** — write code, analyze, compare, refactor, explain, debug
- **System** — multi-step tasks, research, implementation, architecture

Each capability includes example commands in both English and Romanian. The file is designed to be read dynamically by the conversational response handler when a help query is detected.

#### Integration points (future)

The help detection in `classifier.py` returns CONVERSATIONAL, which means the response goes through `conv_result()` in `identity.py`. To make help responses dynamic:

1. In `identity.py`, `conv_result()` or `direct_response()` should check if the intent was a help query
2. If so, read `docs/CAPABILITIES.md` and include it in the system prompt
3. The LLM will then present capabilities in a friendly, conversational way

This integration is planned for a follow-up step (identity.py modification).
