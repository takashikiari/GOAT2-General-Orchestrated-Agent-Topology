# Memoria lui GOAT ca un creier — design

Data: 2026-11-21. Autor: sesiunea ee1c5e, la cererea lui Laris. Stare: **aprobat de Laris, 2026-11-21**.
Pornește de la cum funcționează memoria unui creier uman, nu de la piesele pe care le avem. Ce avem e material:
rămâne, se schimbă sau se scoate după cum servește funcțiile de mai jos. Documentul din 2026-11-17 rămâne istoric.

## 1. De ce (Laris, 2026-11-21)

> „Brain, sleep, graph sunt inutile, deși le-am făcut cu scopul de a îmbunătăți sistemul, nu de a merge în jurul
> lui. Trebuie să contribuie, acum doar atârnă pe lângă."
> „Nu contează ce intră în memorie, contează cum le clasifică, cum creează conexiunile, cum le expune."
> „Toate mecanismele astea, și nu doar ele — dacă trebuie să adăugăm sau să eliminăm ceva, o facem — dar să
> funcționeze ca un creier uman."
> „GOAT nu trebuie să se bazeze pe unelte pentru căutarea în memorie […] altfel sistemul e doar o cârjă."

**Ce e greșit azi:** căutarea pe text e centrul și arbitrul final, iar restul atârnă pe lângă ea. Ce intră în
memorie nu e înțeles: o tură devine un text în L3 cu etichete de entități. Legăturile sunt mecanice: același
cuvânt, aduse împreună în prompt. Expunerea se face după asemănarea de text, iar filtrul ei taie orice asociere.
GOAT compensează cu unelte de căutare.

## 2. Cele 7 funcții, pe aceeași memorie

Un singur ciclu, în care fiecare funcție lucrează pe aceeași memorie:

```
  mesaj ─► (4) indicii aprind ─► (1) În minte ─► răspuns ─► (2) întipărire: clasificare + conexiuni
                ▲                    │ (7) știu / nu știu        │
                │                    ▼                           ▼
           (6) estompare ◄── (5) reconsolidare ◄──── (3) somn: consolidare în cunoaștere
```

### F1. Atenția și memoria de lucru — „În minte"
- **Creierul:** puține lucruri active deodată: ce se întâmplă acum, plus ce s-a aprins legat de asta.
- **Azi:** patru stări separate. Conversația (L2, Redis), firul de discuție și cache-ul căutării (L2.5,
  `memory/activation.py`), nucleul de lucruri deschise (`brain/store.py`) și rezultatele căutării. Promptul le
  lipește pe rând (`memory/context_assembler.py`).
- **Design:** o singură memorie de lucru pe fir de discuție: L2.5 refăcut. Ține elementele active (cunoștințe,
  amintiri, lucruri deschise), fiecare cu activare și cu motivul pentru care e acolo. Promptul se construiește doar
  din ea. Conversația recentă (L2) e stimulul și rămâne în prompt. Identitatea (L0) rămâne fixă.

### F2. Întipărirea — clasificare și conexiuni, chiar când intră
- **Creierul:** ce trăiești se înregistrează cu contextul lui (cine, ce, când, ce era important, de ce se leagă).
- **Azi:** `_archive_turn` (`orchestrator/orchestrator.py`) scrie tura în L3 ca text, iar entitățile GLiNER se
  adaugă separat (`memory/enrichment.py`). Graful leagă tura doar de amintirile din prompt (`brain/live.py`).
  Nimic nu spune ce este tura sau ce schimbă față de ce se știa.
- **Design:** după fiecare tură, un pas de înțelegere (model LLM) o **clasifică**:
  - ce este: fapt, eveniment, decizie, promisiune, preferință, întrebare deschisă sau corectură;
  - despre cine și despre ce: legat de nodurile care **există deja** în graf (aceeași persoană, același proiect),
    nu de cuvinte noi;
  - cât de importantă e.

  Tot atunci creează **conexiunile**: continuă discuția X, e cauza sau urmarea lui Y, contrazice sau corectează ce
  se știa despre Z, e legată de ce era în minte acum. Rezultatul intră în graf ca noduri și legături cu tip, lângă
  episodul din L3. Mesajele de pe agent-coord se întipăresc la fel: canalul e doar un hub, dar ce s-a discutat
  acolo e trăit de GOAT.

### F3. Consolidarea — somnul transformă întâmplări în cunoaștere
- **Creierul:** în somn, reia ce ai trăit, leagă evenimentele și extrage din ele ce știi.
- **Azi:** somnul (`telegram_interface/_brain_sleep.py`) doar ingerează în graf, face perechi și estompează. L1
  (`memory/permanent/`, Letta) se scrie doar dacă GOAT cheamă `promote_memory`, cu plafon de tokeni.
- **Design:** somnul (model LLM) ia grupurile de episoade legate și scrie **cunoștințe**: „X știu că e așa", cu
  legături spre episoadele din care provin. O cunoștință nouă care o contrazice pe una veche o înlocuiește în
  expunere; cea veche rămâne, marcată ca depășită. **L1 devine nucleul cunoașterii:** cunoștințele cele mai
  stabile și mai legate de restul, alese de consolidare, nu de o unealtă și nu de un plafon.

### F4. Reamintirea prin indicii — expunerea
- **Creierul:** un indiciu (o persoană, un subiect, o situație) aprinde direct ce e legat. Uneori o amintire vine
  singură, pentru că se potrivește cu momentul.
- **Azi:** căutarea pe text (`memory/retrieval.py`) decide. Graful (`brain/recall.py`) vine după și e tăiat de
  `blended_gap_filter` în ~84% din ture. Wave2 aduce vecini în timp.
- **Design:** mesajul trece prin aceeași înțelegere ca la F2: despre cine, despre ce, ce fel de întrebare. Indiciile
  aprind nodurile corespunzătoare, iar activarea se răspândește pe conexiunile cu tip spre cunoștințe, amintiri și
  lucruri deschise. Căutarea pe text rămâne un indiciu printre altele, nu arbitrul. Ce e aprins din mai multe
  părți urcă. În prompt, memoria de lucru se arată pe feluri: **ce știu**, **ce s-a întâmplat**, **ce e deschis**,
  fiecare cu motivul („legat de X", „ai spus pe 17.11").

### F5. Reconsolidarea — ce îți amintești se actualizează
- **Creierul:** o amintire readusă se rescrie cu ce știi acum.
- **Azi:** se întăresc doar legăturile din graf (`brain/live.py` `after_turn`). Amintirile și L1 nu se schimbă
  când apare ceva nou despre ele.
- **Design:** când o cunoștință sau o amintire a fost în minte și tura aduce ceva nou despre ea (o completare, o
  corectură), întipărirea (F2) o actualizează: cunoștința primește noua versiune, iar cea veche rămâne în istoric.
  Ce a fost folosit se întărește; ce a fost doar prezent, mai puțin.

### F6. Uitarea — estompare, niciodată ștergere
- **Creierul:** ce nu folosești se estompează, dar un indiciu puternic îl readuce.
- **Azi:** reguli diferite pe straturi. Graful se estompează cu înjumătățire la 14 zile, până la 0,001. L2 se taie la
  fereastră. L1 refuză fapte noi peste plafon. L3 contează după recență.
- **Design:** o singură regulă pentru tot (cunoștințe, amintiri, legături): puterea scade cu nefolosirea, până la o
  podea, și crește la fiecare folosire. **Nimic nu se șterge** (regula lui Laris pentru graf, extinsă la toată
  memoria). Ieșirea din L2 nu e uitare: e trecerea în L3, prin întipărire.

### F7. Să știi ce știi
- **Creierul:** simți când știi, când nu știi și când doar ți se pare.
- **Azi:** nimic. Prompt-ul spune „ai încredere în prefetch"; dacă nu găsește, GOAT caută cu unelte sau prin shell.
- **Design:** memoria de lucru spune explicit și ce **nu** are. Pentru indiciile din mesaj fără amintiri sau
  cunoștințe, scrie „nu ai nimic despre X". Pentru cunoștințele contradictorii, scrie „ai două versiuni". GOAT
  răspunde din ce are, spune „nu știu" când memoria spune că nu are și nu inventează.

## 3. Fără cârje

- `search_memory` și `read_l1` ies din uneltele lui GOAT. `tool_gate` devine inutil și se scoate.
- `forget_fact`, `promote_memory` și `store_memory` rămân doar pentru cererile explicite ale lui Laris („uită",
  „ține minte"). Le trece prin aceeași întipărire (F2).
- Prompt-ul îi spune lui GOAT că memoria de lucru e memoria lui. O lipsă e un defect al sistemului, de reparat de
  noi, nu de ocolit prin shell.

## 4. Ce avem: ce rămâne, ce se schimbă, ce iese

| Piesă | Soartă |
|---|---|
| L3 Chroma (episoade) | rămâne: arhiva întâmplărilor |
| Graful (`brain/graph.py`, `recall.py`) | se schimbă: noduri și conexiuni cu tip (F2, F3), activare din indicii (F4) |
| Somnul | se schimbă: devine consolidarea (F3) și estomparea (F6) |
| Nucleul brain | se mută: lucrurile deschise devin elemente ale memoriei de lucru (F1) |
| L2.5 activare | se reface: devine memoria de lucru (F1) |
| L1 Letta | se schimbă: nucleul cunoașterii, ales de consolidare (F3) |
| Căutarea pe text | rămâne ca indiciu (F4), nu ca arbitru |
| `blended_gap_filter` ca arbitru L3, `_after_top`, cache-ul warm `merged`, prefetch post-tură, `search_memory`, `read_l1`, `tool_gate` | ies |
| GLiNER | rămâne cât timp ajută la indicii; clasificarea o face pasul de înțelegere |

Totul stă sub un comutator, iar cu el oprit sistemul e cel de azi. Codul vechi se scoate abia după decizia lui
Laris.

## 5. Cum judecăm

Un singur criteriu: **GOAT își amintește ce trebuie, când trebuie.** Aduce ce s-a vorbit, ce s-a decis, ce s-a
promis și ce e legat de ce spune Laris, chiar în alte cuvinte, fără să caute. Spune „nu știu" exact atunci când
lucrul nu există în ce a trăit. Un „nu știu" despre ceva ce s-a întâmplat e o lipsă a sistemului; un răspuns
inventat e la fel de grav. Judecă Laris, în conversație. Fără pipeline-uri de măsurare și fără scoruri de text.
Viteza și costul nu sunt criterii (Laris).

## 6. Fazele (fiecare cu OK-ul lui Laris; niciuna nu lasă cod pe care nu-l citește nimic)

1. **Acest spec**, aprobat.
2. **Bucla de bază: F1 + F2 + F4 + F7 + fără cârje.** Turele noi se înțeleg și se leagă, iar memoria de lucru le
   expune în aceeași tură. Somnul reia istoricul existent prin aceeași întipărire, ca să nu pornească de la zero.
   Live, sub comutator; Laris vorbește și decide.
3. **F3 + F5 + F6:** consolidarea în cunoaștere, L1 ales de consolidare, reconsolidarea și regula unică de
   estompare.
4. **Curățenia:** scoaterea a ce iese (§4).

## 7. Decizii (Laris, 2026-11-21)

- Costul apelurilor de model nu contează.
- Mesajele de pe agent-coord se întipăresc ca orice discuție trăită.
- Criteriul e §5, nu o numărătoare.
- Conexiunile cu tip (cauză, continuare, corectură) revin, de data asta făcute la întipărire (F2). Scoaterea lui
  `din_cauza` pe 17.11 era legată de rezumatele din somn, pe care designul acesta nu le mai folosește.
  **Confirmat de Laris (2026-11-21): „nu mai contează ce am spus în trecut, mergi mai departe".**
- **Spec aprobat de Laris, 2026-11-21.**
