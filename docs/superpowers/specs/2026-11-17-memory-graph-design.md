# Memoria ca un creier: graful de amintiri al lui GOAT — design

**Data:** 2026-11-17 · **Status:** propunere, așteaptă aprobarea lui Laris (fără cod înainte) · **Bază:** main `118156d`

## 1. Ce vrem (în cuvintele lui Laris)

Un LLM știe foarte mult, dar suferă de „Alzheimer": fiecare conversație pornește de la zero, iar fereastra de context
e singurul lui prezent. Memoria lui GOAT trebuie să transforme experiența dintr-o fereastră într-una **continuă**:
amintirile și întâmplările să fie **legate între ele**, ca într-un creier, iar reamintirea să meargă **pe legături și
relevanță**, nu pe căutare de text într-un dulap cu dosare. Prefetch-ul nu e o cârjă pusă înainte de răspuns, ci
**primul pas al gândirii**, parte din orchestrator.

Nu vrem: mai multe reguli și limite care să forțeze modelul să „gândească" altfel.

## 2. Ce avem azi și ce lipsește

| Avem | Cum se comportă azi |
|---|---|
| L2 (Redis): ultimele 20 de mesaje | de azi, încap aproape toate în prompt (`budget_base` 8000) |
| L3 (Chroma): 3.835 de amintiri = ture arhivate, cu `timestamp`, `entities` (GLiNER) la 3.834, `topic_id` la 1.990 | căutare după text (dens + BM25), rerank, vecini în timp (wave2) |
| Creierul (SQLite): jurnalul `events` al fiecărui mesaj, nucleul, planificatorul | nu leagă nimic între amintiri |
| `usage_probe`: ce intrări injectate apar în răspuns (aproximare prin cuvinte comune) | doar logat |

**Ce lipsește:** legăturile. Azi o amintire e găsită doar dacă **textul ei** seamănă cu mesajul. Nimic nu spune „asta
s-a întâmplat din cauza aceleia", „acestea două au fost gândite împreună", „vorbim de același proiect". De aici
„nu face nicio legătură". (Căile din v0 legau doar mesaje consecutive — informație pe care L2 o avea deja — și nu le
citea nimic; au fost scoase azi.)

## 3. Modelul: noduri și legături

**Noduri** (în `brain.sqlite`, alături de ce există):

- **episod** = o amintire L3 (o tură arhivată); id = `message_id` din Chroma. Nu copiem textul, doar referința.
- **lucru** = o entitate stabilă: persoană, proiect, tehnologie, serviciu, loc (din `entities` GLiNER deja stocate,
  **curățate**: fără ore/date, normalizate — „Prefetch-ul" = „prefetch").
- **amintire consolidată** = un rezumat făcut în somn dintr-un grup de episoade legate („cum a mers brain v0",
  „de ce am mărit bugetul") — cu legături spre episoadele din care provine.

**Legături** (cu greutate 0..1, momentul ultimei folosiri, tipul):

| Tip | Între | Cum apare |
|---|---|---|
| `despre` | episod → lucru | episodul pomenește lucrul (din entitățile lui) |
| `gândite_împreună` | episod ↔ episod | au fost aduse în același răspuns **și** amândouă au fost folosite (Hebb: „ce se aprinde împreună se leagă") |
| `urmează` | episod → episod | același fir de discuție, unul după altul (ce face azi wave2) |
| `din_cauza` | episod → episod | o decizie și ce a provocat-o / ce a urmat (extras în somn) |
| `rezumă` | consolidată → episod | amintirea mai generală și episoadele ei |

## 4. Reamintirea: activare în lanț (înlocuiește prefetch-ul ca etapă separată)

La fiecare mesaj, în `retrieve()`:

1. **Scântei** (noduri aprinse direct): cele mai relevante episoade după căutarea de azi (dens + BM25 + rerank),
   lucrurile pomenite în mesaj, amintirile din firul curent (activarea de topic existentă).
2. **Răspândire**: activarea curge pe legături, maximum 2 pași, slăbind la fiecare pas
   (activare_nouă = activare × greutate_legătură × 0,5). Un nod legat de multe altele („GOAT", „Laris") dă mai puțin
   fiecăruia — împărțit la numărul lui de legături — ca să nu tragă după el totul (greșeala boost-ului: popularitate
   în loc de asociere).
3. **Filtru de relevanță pe momentul de acum**: un nod ajuns prin legături intră în prompt doar dacă e și relevant
   pentru mesaj (scorul cross-encoder pe care îl avem deja ≥ un prag mic). Legătura propune, relevanța decide.
4. **În prompt**: în bugetul L3 existent, sub titlul „Ce îmi amintesc legat de asta", fiecare amintire cu motivul
   scurt („legat de: brain v0", „gândit împreună cu discuția din 15.11"). GOAT vede **de ce** își amintește.

Latență țintă: activarea în graf ≤ 50 ms (SQLite local, 2 pași, câteva sute de noduri atinse).

## 5. Învățarea: legăturile se întăresc prin folosire

După fiecare răspuns (în fundal, ca scrierea de azi a creierului, niciodată pe calea răspunsului):

- amintirile **folosite** în răspuns (semnalul `usage_probe`, care există) → legătura `gândite_împreună` între ele se
  întărește (+0,2, plafonat la 1), la fel `despre` dintre ele și lucrurile din mesaj;
- amintirile aduse dar **nefolosite** → legătura care le-a adus slăbește puțin (−0,05);
- în timp, fără folosire, legăturile se sting încet (înjumătățire la ~14 zile), **niciodată șterse brusc**.

## 6. Somnul: consolidarea (pe ticul existent al creierului, în felii mici)

- **Ingestie**: episoadele noi din L3 → noduri + legături `despre` (entități curățate) și `urmează`.
- **Consolidare** (o dată pe noapte sau după N episoade noi): grupurile dense de episoade legate → un model mic
  scrie o amintire consolidată (3–5 propoziții) + legăturile `rezumă` și, unde reiese clar, `din_cauza`.
- **Unificare**: lucruri care sunt același lucru („prefetch" / „Prefetch-ul" / „prefetch-ul") → un singur nod.
- **Curățenie**: legăturile sub un prag minim, nefolosite de mult, sunt marcate stinse (păstrate, dar necitite).

## 7. Cum știm că merge (testul de continuitate) — poarta înainte de pornire

Din istoria reală a lui Laris (L2 + L3, pe **memoria de atunci**, fără nimic de după întrebare — lecția din T34/T35):

- **Setul**: ~30–40 de momente în care Laris s-a întors la ceva vorbit cu ore/zile înainte („ce am vorbit aseară",
  „ți-am zis", „cum stăm cu X") + amintirea-țintă la care trebuia să ajungă GOAT. Le extrag eu din log și L3;
  Laris verifică un eșantion.
- **Măsura**: ținta ajunge în prompt? (hit@prompt) — reamintirea de azi vs. graful. Plus: câte amintiri irelevante
  intră (nu vrem zgomot).
- **Poarta** (fixată înainte de date): graful trebuie să aducă ținta în prompt în **cel puțin +15 puncte procentuale**
  mai multe cazuri decât azi, **fără** să crească amintirile irelevante. Dacă nu, nu-l pornim.

## 8. Fazele (fiecare cu PR separat, testat, cu OK-ul lui Laris)

| Fază | Ce | Efect asupra răspunsurilor |
|---|---|---|
| F0 | setul de continuitate + măsurarea de azi (baseline) | niciunul |
| F1 | graful construit din cele 3.835 de amintiri (lucruri curățate, `despre`, `urmează`) + ingestia în somn | niciunul (doar construiește) |
| F2 | reamintirea în lanț în **umbră**: calculează și loghează ce ar aduce, fără să schimbe promptul; testul de continuitate pe ea | niciunul |
| F3 | învățarea Hebb (§5) + consolidarea în somn (§6) | niciunul (încă umbră) |
| F4 | pornire, dacă poarta din §7 trece; wave2 devine legătura `urmează` din graf | da |

## 9. Riscuri, spuse dinainte

- **Calitatea entităților**: GLiNER e zgomotos (ore, date, cuvinte comune ca noduri). Curățarea e partea cea mai
  importantă din F1; fără ea graful leagă tot de tot.
- **Hub-uri**: „GOAT", „Laris", „memorie" apar peste tot. Normalizarea pe numărul de legături (§4.2) e obligatorie.
- **Semnalul de folosire e aproximativ** (cuvinte comune). Învățarea Hebb moștenește zgomotul; de aceea pași mici
  (+0,2 / −0,05) și plafon.
- **Costul consolidării**: un apel de model mic pe grup, în somn — zeci pe zi, nu pe mesaj.
- **Locul în prompt** rămâne limitat: graful decide **ce** intră, nu face loc mai mult.

## 10. Ce nu facem

Nu înlocuim L1/L2/L3; nu ștergem amintiri; nu punem reguli noi asupra modelului; nu pornim nimic fără poarta din §7;
nu adăugăm infrastructură nouă (fără bază de date de grafuri — SQLite-ul creierului ajunge).

## 11. De decis de Laris

1. Modelul pentru consolidare în somn: același model ca GOAT (deepseek) sau unul mai ieftin?
2. Pragul porții (+15 puncte) — ok, sau altă cifră?
3. Ordinea: F0 → F4 ca mai sus, sau F0+F1 împreună ca prim pas?
