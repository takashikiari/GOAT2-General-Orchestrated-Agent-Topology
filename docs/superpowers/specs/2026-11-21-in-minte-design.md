# „În minte": o singură memorie de lucru pentru GOAT — design

Data: 2026-11-21. Autor: sesiunea ee1c5e, la cererea lui Laris. Stare: **propunere, de citit și aprobat de Laris**.
Înlocuiește, ca direcție, abordarea pe bucăți din `2026-11-17-memory-graph-design.md` (acel document rămâne istoric).

## 1. De ce (cuvintele lui Laris, 2026-11-21)

> „Dacă avem toate mecanismele astea, iar ele sunt pe lângă scopul principal și doar căutarea are ultimul cuvânt,
> înseamnă că mecanismele sunt pur și simplu redundante, iar orice măsurătoare e irelevantă. […] Trebuie să
> contribuie, acum doar atârnă pe lângă. Starea trebuie să fie comună și să alimenteze răspunsul."

> „GOAT nu trebuie să se bazeze pe unelte pentru căutarea în memorie. Amintirile trebuie să vină din sistem fără
> ca GOAT să facă un tool call […] altfel sistemul e doar o cârjă în care GOAT nu are încredere."

**Diagnosticul.** Căutarea pe text e centrul sistemului și arbitrul final. Graful, brain-ul și somnul sunt anexe.
Totul e judecat după asemănarea de text cu mesajul, iar o asociere, prin natura ei, nu seamănă la text.
Pe 56 de mesaje reale (18-21.11), filtrul de relevanță a tăiat căutarea după rezultatul 1 sau 2 în 47 de cazuri.
Amintirile din graf au trecut doar în 9. Măsurătorile noastre au judecat asocierea tot cu rigla textului, deci
au ieșit mereu „neutru" sau „negativ". Patru stări separate de „ce e relevant acum" nu se întâlnesc niciodată:
cache-ul de activare din Redis, graful, nucleul brain și rezultatele căutării.

## 2. Ce există azi (verificat pe cod, main 1198903)

| Piesă | Unde | Ce face | Ce lipsește |
|---|---|---|---|
| Căutare L3 | `memory/retrieval.py`, `memory/layers.py` | semantic + BM25 + boost + rerank | e arbitrul final |
| Filtrul de relevanță | `memory/context_assembler.py` (`blended_gap_filter`) | taie la cel mai mare salt de scor | taie tot ce nu e text-asemănător |
| Cache warm | `memory/activation.py` (`merged`, Redis) | refolosește căutarea precedentă | e altă „memorie de acum" |
| Prefetch post-tură | `orchestrator/prefetch.py` | pre-calculează L3 pentru tura următoare | pipeline separat |
| Graful | `brain/graph.py`, `brain/recall.py`, `brain/live.py` | asociere, legături în tură | intră după căutare, e tăiat (84%) |
| Nucleul brain | `brain/store.py`, `brain/inject.py` | promisiuni și întrebări deschise | bloc separat în prompt |
| Somnul | `telegram_interface/_brain_sleep.py` | ingestie în graf, perechi, estompare | nu pregătește nimic pentru tură |
| Arhivarea turei | `orchestrator.py` `_archive_turn` → L3 | tura devine amintire | nu știe ce era în minte atunci |
| L2 → L3 | `memory/auto_promote.py`, `memory/enrichment.py` | mută mesajele vechi, extrage entități | pipeline separat |
| L1 (fapte permanente) | `memory/permanent/` (Letta), `memory/promote.py` | fapte mereu în prompt, plafon de tokeni | se scrie doar dacă GOAT cheamă o unealtă |
| Unelte de memorie | `tools/memory_tools.py` (`search_memory`), `memory_manager.py` (`read_l1`, `forget_fact`), `memory_promote.py`, `memory_writer.py` | GOAT caută și scrie singur | cârja |

## 3. Ideea: o singură stare, „În minte"

Pentru fiecare chat și lane există un singur set de **elemente în minte**, fiecare cu un **nivel de activare**
între 0 și 1. Promptul se construiește **doar din această stare**, iar toate mecanismele lucrează pentru ea.

**Ce poate fi un element:**
- o **amintire** (un episod L3, id = `message_id`);
- un **fapt** din L1 (cele despre viața lui Laris, mereu prezente; §3a);
- un **lucru deschis**: o promisiune a lui GOAT sau o întrebare a lui Laris fără răspuns (azi, nucleul).

Conversația recentă (L2) rămâne fereastra de lucru și e mereu în prompt. Ea e **stimulul** din care pornește totul.
Identitatea (L0) rămâne fixă.

**Mesajele de pe agent-coord** (Laris, 2026-11-21): agent-coord e doar un hub de comunicare, nu o memorie. Ce s-a
discutat acolo e însă parte din ce a trăit GOAT, la fel ca o conversație cu Laris, și intră în aceeași memorie.
Altfel GOAT n-ar ști ce s-a discutat. Azi `brain/live.py` (`_not_lived`) exclude aceste mesaje din reamintire;
excluderea dispare.

**Unde stă:** într-un singur loc: stratul L2.5 refăcut (§3a). Fiecare element are: tip, activare, surse, ultima
folosire și de ce a apărut. Ca la graf, **nimic nu se șterge**: un element nefolosit se stinge până la podea și
rămâne. Promptul citește doar ce e peste prag.

## 3a. Straturile memoriei: fiecare are un rol în aceeași minte

„În minte" nu înlocuiește straturile, ci le leagă. Fiecare strat rămâne, cu rolul lui, și toate lucrează împreună:

| Strat | Ce ține | Rolul în „În minte" |
|---|---|---|
| **L0 Identitate** (Letta `identity`) | cine e GOAT | fix, mereu în prompt; nu e memorie de evenimente |
| **L1 Memorie permanentă** (Letta `facts`) | ce știe GOAT sigur despre Laris și viața lui, plus faptele de lucru de durată | **mereu în minte**, la activare maximă |
| **L2 Memorie de lucru** (Redis, conversația) | ce se vorbește acum | mereu în prompt; e stimulul care aprinde restul |
| **L2.5 Activare** (Redis, firul de discuție) | azi: subiectul curent și cache-ul căutării | **devine „În minte"**: starea minții pe fir de discuție |
| **L3 Memorie episodică** (Chroma) | tot ce a trăit GOAT, fiecare tură, inclusiv agent-coord | arhiva din care se aduc amintirile |
| **Graful** (`brain.sqlite`) | legăturile dintre amintirile L3 și lucrurile despre care vorbesc | indexul asocierilor peste L3 |

**L1 are două feluri de fapte, tratate diferit:**
- **Viața lui Laris:** oameni (fratele Ionuț, iubita), animale (cățelușa din copilărie), locuri, preferințe,
  lucruri importante. Sunt mereu în minte și **nu se sting niciodată**, oricât de rar ar fi pomenite. GOAT trebuie
  să le știe oricând, fără să le caute. Plafonul de tokeni nu are voie să le scoată.
- **Fapte de lucru:** stări de proiect, configurații, decizii tehnice. Pot ieși din prompt când nu mai sunt
  folosite, dar rămân păstrate și revin când se aprind.

**L2 → L3:** când un mesaj iese din fereastra conversației (`auto_promote`), trece în L3. Asta e întipărire:
amintirea se scrie legată de ce era în minte atunci (§5), nu ca o copie izolată.

**L3 se accesează pe două căi, ale aceleiași arhive:** căutarea (după ce seamănă la text) și graful (după ce e
legat). Niciuna nu are ultimul cuvânt; amândouă aprind elemente în „În minte".

**Exemplu: „fratele meu".** „Fratele lui Laris: Ionuț" stă în L1, deci e mereu în minte. Tura din 17.11 în care
Laris l-a spus stă în L3 și e legată în graf de „Ionuț". Când Laris pomenește familia, GOAT știe numele din L1, iar
graful aduce și discuțiile legate de el, fără niciun tool call.

## 4. Reamintire: fiecare tură

1. **Vine mesajul.** Activarea de la tura trecută scade cu un factor fix: ce era în minte rămâne, dar mai slab.
   Asta e continuitatea și înlocuiește cache-ul warm din Redis.
2. **Fiecare sursă aprinde ce știe și adaugă activare:**
   - **căutarea**: amintirile asemănătoare, cu scorul cross-encoder ca activare;
   - **graful**: activarea se răspândește din tot ce e deja în minte, plus ce a aprins căutarea acum, pe
     legături (`recall.spread` existent);
   - **lucrurile deschise**: intră cu activare mare și rămân aprinse până se închid;
   - **faptele L1**: se aprind când sunt pomenite sau legate de ce e în minte.
3. **Convergența decide.** Un element adus de mai multe surse se adună (până la 1). O singură sursă slabă nu
   ajunge sus: o amintire adusă doar de graf, pe o legătură slabă, nu trece peste cele bune din căutare.
   Cele mai bune rezultate ale căutării intră ca azi, cu activarea lor.
4. **Promptul:** un singur bloc, „În minte", cu elementele peste prag, ordonate după activare, în bugetul AITS
   existent. Fiecare are un motiv scurt („seamănă cu ce spui", „legat de: X", „promis ieri", „fapt").
   **Nu mai există un filtru al căutării cu ultimul cuvânt.**

## 5. Întipărire: după răspuns

- Tura se arhivează în L3 ca azi, dar **legată de tot ce era în minte** (`gândite_împreună`): puternic de ce a
  folosit răspunsul, slab de ce a fost doar prezent.
- Ce a folosit răspunsul **rămâne aprins mai tare** și își întărește drumurile din graf. Ce n-a folosit se stinge
  mai repede.
- O promisiune nouă a lui GOAT sau o întrebare rămasă fără răspuns devine **lucru deschis** în aceeași stare.
  Se închide când apare răspunsul sau fapta.
- Entitățile turei intră în graf ca azi (ingestia din somn), legate de episod.

## 6. Consolidare: somnul

- **Stingerea lentă** a stării „În minte", pe ticul existent.
- **Pregătirea turei următoare:** din ce e în minte, somnul aprinde dinainte vecinii din graf (`urmează`,
  `gândite_împreună`). Asta înlocuiește prefetch-ul post-tură ca pipeline separat.
- **Urcarea în L1:** o amintire care revine și e folosită în ture diferite, în zile diferite, devine candidat
  de fapt permanent. Un model o formulează ca fapt („fratele lui Laris: Ionuț"), iar faptul intră în L1 prin
  scrierea existentă (`promote_fact`).
- **L1 nu pierde nimic din viața lui Laris:** faptele despre oameni, animale, locuri și preferințe rămân mereu
  (§3a). Doar faptele de lucru nefolosite multă vreme ies din prompt; rămân păstrate și revin când se aprind.
- **Ce a trăit GOAT și nu e încă în L1** (de exemplu un nume pomenit o singură dată) rămâne în L3 și în graf și se
  aduce când se aprinde. Consolidarea îl urcă în L1 dacă e despre viața lui Laris, chiar după o singură pomenire.
- Perechile și estomparea grafului rămân cum sunt.

## 7. Fără cârje

- **`search_memory` iese din uneltele lui GOAT.** Tot ce ar fi găsit căutând trebuie să fie deja în minte.
- **`read_l1` iese:** faptele sunt deja în prompt. `forget_fact`, `promote_memory` și `store_memory` rămân doar
  pentru cererile explicite ale lui Laris („uită asta", „ține minte asta"). Descrierile lor spun asta.
- **Prompt-ul îi spune lui GOAT** că blocul „În minte" e memoria lui. Dacă ceva nu e acolo, spune că nu-și
  amintește, fără să caute prin shell sau prin fișiere. Lipsa e o problemă a sistemului, de reparat de noi.
- `tool_gate` devine inutil (`search_memory` nu mai există) și se scoate.

## 8. Ce dispare (ca nimic să nu mai atârne)

- `activation.merged` ca sursă warm → continuitatea din „În minte";
- `run_prefetch_and_save` ca pipeline separat → pregătirea din somn;
- `blended_gap_filter` ca arbitru pentru L3 → pragul de activare (scorul căutării rămâne o intrare);
- `_after_top` și plasarea „sub locul 3" a amintirilor din graf;
- blocul separat al nucleului (`brain/inject.py`) → lucrurile deschise sunt elemente în aceeași stare;
- `search_memory`, `read_l1`, `tool_gate`.

Totul stă sub un singur comutator, `[in_minte] enabled`. Cu el oprit, sistemul e exact ca azi, deci revenirea
durează un minut. Codul vechi se scoate abia după ce Laris decide că varianta nouă rămâne.

## 9. Cum judecăm

Un singur criteriu: **GOAT își amintește ce trebuie, când trebuie.**
- Aduce ce s-a vorbit, ce s-a promis și ce e legat de ce spune Laris, chiar în alte cuvinte, fără să caute.
- Spune „nu știu" **exact atunci când lucrul chiar nu există** în ce a trăit. Nu contează de câte ori spune
  „nu știu". Contează dacă îl spune la locul lui. Un „nu știu" despre ceva ce s-a întâmplat e o lipsă a
  sistemului; un răspuns inventat despre ceva ce nu s-a întâmplat e la fel de grav.
- Judecă Laris, în conversație. Fără pipeline-uri de măsurare noi și fără scoruri de text pentru asociere.
  Viteza și costul nu sunt criterii.

## 10. Riscuri, spuse dinainte

- **Zgomot în prompt:** asocieri fără legătură. Protecția e convergența (§4.3) și pragul; Laris va simți.
- **Căutarea pierde ce găsea bine:** cele mai bune rezultate intră cu activarea lor, ca azi.
- **Urcarea în L1 scrie fapte greșite:** un fapt nou apare marcat ca nou, iar Laris îl poate respinge
  („uită asta"). Pornește doar după ce reamintirea s-a dovedit în conversație.
- **„Nu știu" pus greșit:** GOAT spune „nu știu" despre ceva ce s-a întâmplat (sistemul n-a adus amintirea) sau
  inventează ceva ce nu s-a întâmplat. Ambele sunt defecte ale memoriei, de reparat în sistem, nu în GOAT.

## 11. Fazele (fiecare cu OK-ul lui Laris)

1. **Acest spec**, citit și aprobat.
2. **PR 1 — bucla completă:** starea „În minte", reamintirea din toate sursele (§4), întipărirea (§5), stingerea
   și pregătirea din somn (§6, fără L1), fără cârje (§7). Sub comutator. Live, Laris vorbește câteva zile și decide.
3. **PR 2 — consolidarea în L1** (§6, urcarea și curățarea L1). Abia după ce PR 1 e păstrat.
4. **PR 3 — curățenia:** scoaterea codului înlocuit (§8), după decizia lui Laris.

## 12. Decizii (Laris, 2026-11-21)

- Costul apelurilor de model (urcarea în L1 etc.) nu contează.
- Mesajele de pe agent-coord intră în aceeași memorie, ca orice discuție trăită (§3).
- Criteriul e „își amintește ce trebuie, spune «nu știu» doar când lucrul nu există" (§9), nu o numărătoare.
