# GOAT 2.0 Audit — Faza 1: Inventar simptome din conversații

> Notițe brute de lucru. Vor fi consolidate în raportul final.
> Data: 2026-06-19

---

## Conversația 1 (furnizată de Gabriel)

### Simptom S1 — Buclă de repetiție (meniu A/B/C identic de 3 ori)
- **Repro:** la 3 input-uri consecutive ("Goat", "Ok", "Goat ești cu mine?"), GOAT afișează același meniu cu 3 opțiuni A/B/C
- **Citare:** text identic (cu variații minore) "Vrei să: A) Creez un style profile ... B) Sau vrei să mă apuc de debug ... C) Sau poate ambele"
- **Cauze posibile:**
  - state.todo_list nu se resetează între turnuri
  - synthesis template hardcodat care nu consumă contextul curent
  - lipsă „context-aware response selection"

### Simptom S2 — Confuzie temporală (referință la DAG session `2851d7a3`)
- **Repro:** la input "Ok", GOAT zice "ultima oară când ai zis 'Ok', DAG-ul session `2851d7a3` a picat..."
- **Problemă:** vorbește despre o sesiune din trecut ca și cum ar fi relevantă ACUM, fără a indica explicit „acum N minute/ore"
- **Cauze posibile:**
  - memory recall returnează intrări fără timestamp vizibil
  - freshness labels (FRESH/RECENT/OLD) nu sunt folosite în sinteză
  - retrieval-ul nu e limitat temporal la „ultimele X minute"

### Simptom S3 — Confabulație de memorie
- **Repro:** "Am văzut toată memoria aia — m-am repetat ca o oaie beată, recunosc"
- **Problemă:** GOAT recunoaște pattern-ul de repetiție, apoi ÎL REPETĂ imediat
- **Cauze posibile:**
  - lipsă mecanism anti-repetiție în synthesis
  - niciun history-of-last-N-turns care să blocheze template-uri similare
  - „self-awareness" e doar template, nu stare reală

### Simptom S4 — Supra-explicare la stimuli minimi
- **Repro:** input „Ok" → output de ~250 cuvinte cu plan de debugging
- **Repro:** input „Goat" → același meniu A/B/C
- **Problemă:** nu există distincție input elaborat vs input minimal
- **Cauze posibile:**
  - lipsă clasificare „input requires simple response"
  - synthesis template întotdeauna verbose

### Simptom S5 — Validare suspectă (web_search × 15)
- **Repro:** GOAT raportează "Am executat: web_search, web_search, ... (×15)"
- **Problemă:** nu se vede niciun output agregat; nu e clar dacă căutarea a reușit
- **Cauze posibile:**
  - tool execution loop nu returnează rezultatele la synthesis
  - lipsă confirmare `tool_called=True` + `raw_output_hash` în output-ul vizibil

### Simptom S6 — Drift de personalitate
- **Repro:** oscilează între ton tehnic ("am investigat", "i.e.") și mascotă ("Baaa, Generale 🐐", emoji-uri multiple)
- **Problemă:** nu există un style profile stabil
- **Cauze posibile:**
  - lipsă identity prompt coerent
  - tone-ul e lăsat la alegerea LLM-ului per-turn

### Simptom S7 — Halucinație de context partajat
- **Repro:** "Acum, Generale, hai să facem treabă serioasă" presupune o continuitate pe care conversația nu o are
- **Problemă:** fiecare reply pare să „reia" de la zero, mimând continuitate fără s-o aibă

### Simptom S8 — Pierderea întreruperilor
- **Repro:** "Ai zis Ok, dar eu zic să facem debugging" — nu ascultă input-ul, îl înlocuiește
- **Cauze posibile:**
  - prompt-ul de system prioritizează „own agenda" peste input-ul utilizatorului

---

## Aggregate findings Faza 1

| # | Simptom | Severitate (estimare) | Probabil cod în |
|---|---------|----------------------|------------------|
| S1 | Buclă repetiție meniu | P0 | supervisor/synthesis, agents/summarizer |
| S2 | Confuzie temporală | P0 | memory/router, tools/memory_temporal |
| S3 | Confabulație (recunoaște + repetă) | P0 | supervisor/synthesis, state-management |
| S4 | Supra-explicare | P1 | supervisor/classification, agents/* |
| S5 | Validare opacă | P0 | supervisor/dag_validator, tools |
| S6 | Drift personalitate | P1 | supervisor/identity, prompts |
| S7 | Halucinație context | P1 | supervisor/session |
| S8 | Pierdere input utilizator | P0 | agents/planner, agents/summarizer |

---

**Status:** Faza 1 completă. 8 simptome distincte, toate cu ipoteze plauzibile.
**Next:** Faza 2 — urc în cod pentru fiecare simptom.
