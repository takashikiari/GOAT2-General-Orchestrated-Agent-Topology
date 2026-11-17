# Memoria ca un creier: graful de amintiri al lui GOAT — design

**Data:** 2026-11-17 · **Status:** aprobat de Laris cu condiția „nu se șterge nimic" (§1); decizii §11 luate (deepseek, +15, F0+F1 împreună) · **Bază:** main `118156d`

## 1. Ce vrem (în cuvintele lui Laris)

Un LLM știe foarte mult, dar suferă de „Alzheimer": fiecare conversație pornește de la zero, iar fereastra de context
e singurul lui prezent. Memoria lui GOAT trebuie să transforme experiența dintr-o fereastră într-una **continuă**:
amintirile și întâmplările să fie **legate între ele**, ca într-un creier, iar reamintirea să meargă **pe legături și
relevanță**, nu pe căutare de text într-un dulap cu dosare. Prefetch-ul nu e o cârjă pusă înainte de răspuns, ci
**primul pas al gândirii**, parte din orchestrator.

Nu vrem: mai multe reguli și limite care să forțeze modelul să „gândească" altfel.

**Condiție absolută (Laris, 2026-11-17): nu se șterge nimic, niciodată.** Nicio amintire, niciun nod, nicio legătură
nu primește DELETE. O legătură nefolosită se estompează până la o greutate minimă de **0,001** și rămâne fizic în
`brain.sqlite` pentru totdeauna. Singurul fel în care ceva „dispare" din context e să fie depășit de lucruri mai
relevante; un stimul viitor care aprinde un nod din apropiere trebuie să o poată readuce în față. Ca un creier:
nimic nu dispare, totul doar se estompează până când un stimul îl readuce. (Detalii: §4, §5, §6.)

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
   **Legăturile estompate rămân în joc**: orice legătură, chiar la podeaua de 0,001, face din vecinul ei un
   **candidat** când un nod din apropiere se aprinde. Greutatea doar ordonează candidații; nu îi exclude. Dacă o
   amintire estompată e cea mai relevantă pentru ce se vorbește acum, ea intră în prompt — exact „stimulul care o
   readuce în prim-plan". (Fără asta, o legătură de 0,001 ar transmite practic zero activare și ar fi ștearsă de
   facto, deși există fizic.) Numărul de candidați verificați e plafonat după greutate și recență, ca să rămână
   rapid — dar plafonul taie doar din ce se **verifică acum**, nu șterge nimic.
4. **În prompt**: în bugetul L3 existent, sub titlul „Ce îmi amintesc legat de asta", fiecare amintire cu motivul
   scurt („legat de: brain v0", „gândit împreună cu discuția din 15.11"). GOAT vede **de ce** își amintește.

Latență țintă: activarea în graf ≤ 50 ms (SQLite local, 2 pași, câteva sute de noduri atinse).

## 5. Învățarea: legăturile se întăresc prin folosire

După fiecare răspuns (în fundal, ca scrierea de azi a creierului, niciodată pe calea răspunsului):

- amintirile **folosite** în răspuns (semnalul `usage_probe`, care există) → legătura `gândite_împreună` între ele se
  întărește (+0,2, plafonat la 1), la fel `despre` dintre ele și lucrurile din mesaj;
- amintirile aduse dar **nefolosite** → legătura care le-a adus slăbește puțin (−0,05);
- în timp, fără folosire, legăturile se estompează încet (înjumătățire la ~14 zile) până la **podeaua de 0,001**,
  sub care nu coboară niciodată; nu sunt șterse și nu sunt scoase din parcurgere;
- o legătură estompată readusă în față de un stimul și **folosită** se întărește din nou după aceleași reguli
  (+0,2): amintirea „revine" și rămâne aproape cât timp e folosită.

## 6. Somnul: consolidarea (pe ticul existent al creierului, în felii mici)

- **Ingestie**: episoadele noi din L3 → noduri + legături `despre` (entități curățate) și `urmează`.
- **Consolidare** (o dată pe noapte sau după N episoade noi): grupurile dense de episoade legate → un model mic
  scrie o amintire consolidată (3–5 propoziții) + legăturile `rezumă` și, unde reiese clar, `din_cauza`.
- **Unificare**: lucruri care sunt același lucru („prefetch" / „Prefetch-ul" / „prefetch-ul") → un nod principal;
  celelalte rămân în tabel, marcate `alias_of` → nodul principal, cu legăturile lor (nu se șterge nimic, doar se
  redirecționează la citire).
- **Fără curățenie prin ștergere**: nu există DELETE nici pe noduri, nici pe legături, nici pe amintiri consolidate.
  Somnul doar estompează (până la podeaua 0,001) și întărește. Un test automat verifică la fiecare rulare că
  numărul de noduri și de legături nu scade niciodată.

## 7. Cum știm că merge (testul de continuitate) — poarta înainte de pornire

Din istoria reală a lui Laris (L2 + L3, pe **memoria de atunci**, fără nimic de după întrebare — lecția din T34/T35):

- **Setul**: ~30–40 de momente în care Laris s-a întors la ceva vorbit cu ore/zile înainte („ce am vorbit aseară",
  „ți-am zis", „cum stăm cu X") + amintirea-țintă la care trebuia să ajungă GOAT. Le extrag eu din log și L3;
  Laris verifică un eșantion.
- **Măsura**: ținta ajunge în prompt? (hit@prompt) — reamintirea de azi vs. graful. Plus: câte amintiri irelevante
  intră (nu vrem zgomot).
- **Poarta** (fixată înainte de date): graful trebuie să aducă ținta în prompt în **cel puțin +15 puncte procentuale**
  mai multe cazuri decât azi, **fără** să crească amintirile irelevante. Dacă nu, nu-l pornim.

**Înlocuit (Laris, 2026-11-17):** o amintire, o trăire nu e ceva standard — e dinamică, spontaneitate; nu există un
„răspuns corect", ci un răspuns bun. Un etalon cu ținte alese dinainte măsoară o linie (întrebare → țintă), adică
exact ce nu e un creier. Rezultatele lui (azi 8/36; F2 și legăturile din tură 7–9/36) rămân doar ca istoric.
Graful se judecă de acum după: (1) **§12.6** — e viu, citit direct din store; (2) **cum simte Laris conversațiile** —
dacă GOAT face spontan legături bune. Graful se pornește/oprește din `[brain] graph_recall`, fără să șteargă nimic.

## 8. Fazele (fiecare cu PR separat, testat, cu OK-ul lui Laris)

| Fază | Ce | Efect asupra răspunsurilor |
|---|---|---|
| F0 | setul de continuitate + măsurarea de azi (baseline) | niciunul |
| F1 | graful construit din cele 3.835 de amintiri (lucruri curățate, `despre`, `urmează`) + ingestia în somn | niciunul (doar construiește) |
| F2 | reamintirea în lanț în **umbră**: calculează și loghează ce ar aduce, fără să schimbe promptul; testul de continuitate pe ea | niciunul |
| F3 | învățarea Hebb (§5) + consolidarea în somn (§6) | niciunul (încă umbră) |
| F4 | pornire, dacă poarta din §7 trece; wave2 devine legătura `urmează` din graf | da |

**Actualizat (2026-11-17):** F2–F4 sunt înlocuite de graful viu (§12): legături formate în aceeași tură, întărite la
fiecare atingere, estompate lent, reamintire prin activare pornită live (`graph_recall = true`), judecată după §7
(varianta înlocuită). Consolidarea prin rezumate (§6) nu e implementată: legăturile se nasc în tură, nu după ore.

## 9. Riscuri, spuse dinainte

- **Calitatea entităților**: GLiNER e zgomotos (ore, date, cuvinte comune ca noduri). Curățarea e partea cea mai
  importantă din F1; fără ea graful leagă tot de tot.
- **Hub-uri**: „GOAT", „Laris", „memorie" apar peste tot. Normalizarea pe numărul de legături (§4.2) e obligatorie.
- **Semnalul de folosire e aproximativ** (cuvinte comune). Învățarea Hebb moștenește zgomotul; de aceea pași mici
  (+0,2 / −0,05) și plafon.
- **Costul consolidării**: un apel de model mic pe grup, în somn — zeci pe zi, nu pe mesaj.
- **Locul în prompt** rămâne limitat: graful decide **ce** intră, nu face loc mai mult.

## 10. Ce nu facem

Nu înlocuim L1/L2/L3; nu ștergem nimic — nici amintiri, nici noduri, nici legături (condiția absolută din §1); nu punem reguli noi asupra modelului; nu pornim nimic fără poarta din §7;
nu adăugăm infrastructură nouă (fără bază de date de grafuri — SQLite-ul creierului ajunge).

## 11. Decizii (Laris, 2026-11-17)

1. Consolidarea în somn o face **deepseek** (același model ca GOAT).
2. Poarta: **+15 puncte procentuale** hit@prompt față de reamintirea de azi, fără mai mult zgomot.
3. **F0 și F1 împreună** ca prim pas (setul de continuitate + baseline, și graful construit în paralel, fără efect
   asupra răspunsurilor).

## 12. Graful viu: legăturile se nasc, se întăresc și se estompează (Laris, 2026-11-17)

Cerința lui Laris: memoria să devină ceva **viu, ca ADN-ul** — o explozie de legături neuronale, nu o fotografie.

### 12.1 Starea de acum (F1, verificat în cod)

- `brain/graph.py` `_edge`: o legătură se creează o singură dată (`INSERT OR IGNORE`); greutatea rămâne **1,0 pe
  veci** — nu se întărește, nu slăbește.
- Coloanele `uses` și `last_used` există în schemă, dar **nu le scrie nimeni**.
- `WEIGHT_FLOOR = 0,001` e declarat, dar **nu există cod care să estompeze** o legătură spre el: regula „nimic nu se
  șterge, totul se estompează" (§1) e azi doar text.
- **Nu există nicio legătură lucru↔lucru**: doar `despre` (episod→lucru) și `urmează` (episod→episod). Graful e o
  poză a co-apariției, nu o rețea.

### 12.2 Legături lucru↔lucru (`împreună`)

Două lucruri întâlnite **în același episod** se leagă direct între ele (legătură nedirecționată, cheia = perechea
ordonată alfabetic). La prima întâlnire: greutate 0,1. La fiecare re-întâlnire într-un episod nou: întărire (§12.3).
Ce se repetă des devine o legătură puternică („LeShuttle"↔„Eurotunnel", „prefetch"↔„rerank"); o pereche văzută o
singură dată rămâne slabă. Hub-urile („laris", „lenovo", „goat") nu formează perechi cu tot (altfel fiecare episod
ar crea zeci de legături inutile): un lucru prezent în peste 15% din episoade nu intră în perechi, dar își păstrează
legăturile `despre` (§4.2 se ocupă de ele la citire).

### 12.3 Întărire la fiecare atingere, nu doar la creare

Orice atingere a unei legături — o re-întâlnire (§12.2), o formare în aceeași tură (§5, `gândite_împreună`) sau o
parcurgere la reamintire care ajunge în răspuns (§12.5) — o face:

- `weight = min(1,0, weight_estompat + Δ)` (Δ: 0,1 la re-întâlnire, 0,2 la folosire în răspuns, 0,05 la simplă
  parcurgere fără folosire);
- `uses = uses + 1`, `last_used = acum`.

`weight_estompat` e greutatea adusă la zi după §12.4, ca o legătură veche și estompată să pornească de unde a ajuns,
nu de la 1,0.

### 12.4 Estompare lentă spre podea, niciodată ștergere

Fără atingere, greutatea scade cu timpul de la `last_used` (sau `created_at`, dacă n-a fost atinsă niciodată):
`weight_acum = max(0,001, weight × 0,5 ^ (zile_trecute / 14))`. Calculul se face:

- **la citire**, din `weight` și `last_used` (nu e nevoie să rescrii toată tabela ca să fie corect);
- **în somn**, în felii mici, ca valoarea scrisă să urmeze timpul (o actualizare `UPDATE`, niciodată `DELETE`).

Podeaua 0,001 e absolută: o legătură nu dispare, iar la citire rămâne **candidată** (§4.3), deci un stimul viitor o
poate readuce și o poate întări din nou (§12.3). Testul automat din §6 („numărul de noduri și legături nu scade
niciodată") rămâne obligatoriu.

### 12.5 Citirea atinge legăturile

Reamintirea care trece prin graf (§4) **scrie**: legăturile parcurse până la o amintire care a intrat în prompt primesc
`uses++`, `last_used = acum`, Δ = 0,05; dacă amintirea a fost și **folosită** în răspuns (semnalul `usage_probe`),
Δ = 0,2. Scrierea se face în fundal, după răspuns, ca scrierea de azi a creierului — niciodată pe calea răspunsului.
Fără asta nu există „folosit", iar graful nu poate învăța.

### 12.6 Ce înseamnă „viu", verificabil fără măsurători sintetice

După o conversație reală cu Laris, în `brain.sqlite`:

1. legăturile parcurse în acea conversație au `last_used` din timpul conversației și `weight` mai mare decât înainte;
2. legăturile neatinse au `last_used` neschimbat, iar greutatea lor citită (§12.4) e mai mică decât ieri;
3. există legături lucru↔lucru noi, din episoadele conversației;
4. numărul total de noduri și legături a crescut sau a rămas egal — niciodată nu a scăzut.

Verificarea se face citind store-ul (o interogare, fără test sintetic) și e criteriul de „merge" pentru această
secțiune. Ea nu înlocuiește poarta din §7: §12 face graful viu; §7 spune dacă reamintirea pe el aduce amintirea
potrivită în prompt.

## 13. Ce a rămas din spec, măsurat (T40, 2026-11-17 seara)

- **§6 Unificare — făcut.** Scrierile aceluiași lucru („wave 2"/„wave2"/„wave-2") primesc `alias_of` spre cea cu cele
  mai multe episoade (106 grupuri, 116 rânduri marcate, nimic șters); activarea citește tot grupul. Cuvintele care nu
  numesc nimic („aici", „neața", „șefule", „super") nu mai conduc activare și nu mai fac perechi (rândurile rămân).
  Pe o copie a grafului, 300 de episoade ca scântei: se schimbă reamintirea la 61/300; dispar 53 de amintiri aduse
  prin cuvinte-gunoi; 120 vin prin lucruri unificate. Pe cele 40 cele mai recente: nicio schimbare (merg pe legături
  episod↔episod). Timp p90 33 ms.
- **§4.1 Scântei din lucrurile pomenite — măsurat, NU construit.** 80 de mesaje reale ale lui Laris: 8 pomenesc un
  lucru utilizabil (nu generic, nu hub); ce aduc în plus față de căutare: 48 de amintiri, relevanță mediană 0,003,
  4 peste 0,5 — toate rezumate agent-coord, pe care reamintirea le exclude oricum. Beneficiu zero → fără cod.
- **§5 slăbire −0,05 pentru aduse-nefolosite — neconstruit:** era legată de §4.1 și nu are date care s-o susțină.
- **§3 `din_cauza` și §8 wave2 ca `urmează` — propuse spre scoatere** (decizia lui Laris): §3 depindea de rezumatele
  din somn, la care s-a renunțat; §8 nu schimbă răspunsurile (wave2 merge bine separat).
