# Reguli de Refactorizare — Service Registry Pattern

## Scop

Acest document definește regulile clare pentru aplicarea **Service Registry Pattern** într-un proiect Python existent. Scopul este centralizarea instanțelor de servicii (clienți HTTP, conexiuni DB, API wrappers etc.) într-un modul unic, fără a modifica codul sau fișierele existente.

---

## Regula 1: Non-Invazivitate — Zero Modificări ale Codului Existent

**1.1** Nu se modifică **niciun fișier** existent din proiect.  
**1.2** Nu se șterge, redenumește sau mută niciun fișier sau director existent.  
**1.3** Nu se schimbă importurile, semnăturile funcțiilor, clasele sau logica de business din codul curent.  
**1.4** Singura acțiune permisă pe codul existent este **citirea** (pentru analiză și documentare).

> **Motivație:** Refactorizarea trebuie să fie sigură și reversibilă. Orice modificare a codului existent introduce riscul de regression.

---

## Regula 2: Adiționalitate — Doar Fișiere Noi

**2.1** Toate schimbările constau exclusiv în **crearea de fișiere noi**.  
**2.2** Fișierele noi se plasează într-un director dedicat, de exemplu:

```
project_root/
  service_registry/       # ← director nou
    __init__.py
    registry.py
    providers.py
    exceptions.py
```

**2.3** Se poate crea și un fișier de configurare (ex. `service_registry_config.py`) la rădăcina proiectului, dacă este necesar.  
**2.4** Se poate crea documentație în `docs/` (ca acest fișier).

> **Motivație:** Fișierele noi pot fi adăugate și testate izolat, fără a afecta sistemul existent.

---

## Regula 3: Registry-ul Trebuie să Fie un Singleton Central

**3.1** Registry-ul este un **singleton** — o singură instanță globală, accesibilă din orice modul.  
**3.2** Implementarea tipică:

```python
# service_registry/registry.py
class ServiceRegistry:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._services = {}
        return cls._instance

    def register(self, name: str, service) -> None:
        self._services[name] = service

    def get(self, name: str):
        if name not in self._services:
            raise ServiceNotFoundError(f"Service '{name}' not registered")
        return self._services[name]

    def list_services(self) -> list[str]:
        return list(self._services.keys())
```

**3.3** Alternativ, se poate folosi un modul cu variabile globale (simplu și pythonic):

```python
# service_registry/registry.py
_registry: dict[str, object] = {}

def register(name: str, service) -> None:
    _registry[name] = service

def get(name: str):
    if name not in _registry:
        raise ServiceNotFoundError(f"Service '{name}' not registered")
    return _registry[name]
```

> **Motivație:** Un singleton asigură că toate modulele văd aceleași instanțe de servicii.

---

## Regula 4: Înregistrarea Serviciilor — Explicită și Controlată

**4.1** Serviciile se înregistrează **explicit** printr-un mecanism centralizat (ex. funcție `register_services()`).  
**4.2** Înregistrarea NU se face automat (fără auto-discovery) — pentru a evita efecte secundare.  
**4.3** Exemplu:

```python
# service_registry/providers.py
from .registry import register
from existing_module import DatabaseClient, ApiClient, CacheClient

def register_all_services():
    register("db", DatabaseClient())
    register("api", ApiClient(base_url="https://api.example.com"))
    register("cache", CacheClient(host="localhost", port=6379))
```

**4.4** Parametrii de configurare (URL-uri, chei, host-uri) se citesc din variabile de mediu sau fișiere de configurare existente — nu se hardcodează.

> **Motivație:** Controlul explicit previne înregistrarea accidentală și face sistemul ușor de înțeles.

---

## Regula 5: Accesul la Servicii — Prin Getter cu Tip Returnat

**5.1** Funcția `get()` returnează serviciul cu tipul corect (type hint).  
**5.2** Se recomandă funcții specializate pe tip:

```python
def get_db() -> DatabaseClient:
    return get("db")

def get_api() -> ApiClient:
    return get("api")
```

**5.3** Dacă serviciul nu este găsit, se aruncă o excepție dedicată (`ServiceNotFoundError`).

> **Motivație:** Tipurile explicite ajută IDE-urile și previn bug-uri.

---

## Regula 6: Gestionarea Erorilor — Excepții Dedicat

**6.1** Se definește o ierarhie de excepții în `exceptions.py`:

```python
class ServiceRegistryError(Exception):
    """Eroare de bază pentru Service Registry."""
    pass

class ServiceNotFoundError(ServiceRegistryError):
    """Serviciul cerut nu este înregistrat."""
    pass

class ServiceAlreadyRegisteredError(ServiceRegistryError):
    """Încercare de înregistrare a unui serviciu deja existent."""
    pass
```

**6.2** Registry-ul nu înghite erori — le propagă pentru a fi tratate la nivelul apelantului.

> **Motivație:** Excepții clare = debugging mai ușor.

---

## Regula 7: Testarea — Izolată și Fără Dependențe Externe

**7.1** Testele pentru registry se scriu în fișiere noi (ex. `tests/test_service_registry.py`).  
**7.2** Testele NU trebuie să depindă de servicii reale (DB, API externe). Se folosesc **mock-uri** sau **obiecte fake**.  
**7.3** Testele acoperă:

- Înregistrare și obținere servicii
- Excepții pentru servicii lipsă
- Comportamentul de singleton (aceeași instanță)
- Resetarea registry-ului între teste (dacă e cazul)

> **Motivație:** Teste rapide, deterministe, care pot rula fără conexiune la rețea.

---

## Regula 8: Documentarea Obligatorie

**8.1** Orice fișier nou creat trebuie să aibă **docstring** la nivel de modul și clasă/funcție.  
**8.2** Se creează un fișier `README.md` în directorul `service_registry/` care explică:

- Ce este Service Registry
- Cum se înregistrează servicii
- Cum se accesează servicii
- Exemplu complet de utilizare

**8.3** Se documentează și **deciziile arhitecturale** (de ce s-a ales această abordare).

> **Motivație:** Documentația asigură că și alți developeri pot înțelege și extinde registry-ul.

---

## Regula 9: Compatibilitate Ascendentă

**9.1** Codul existent care accesează serviciile direct (ex. `DatabaseClient()`) **continuă să funcționeze** exact ca înainte.  
**9.2** Registry-ul este o **opțiune**, nu o înlocuire forțată. Modulele pot migra treptat.  
**9.3** Nu se adaugă wrapper-e sau interceptori care să modifice comportamentul serviciilor existente.

> **Motivație:** Fiecare echipă/modul poate adopta registry-ul în ritmul propriu.

---

## Regula 10: Securitate — Fără Credențiale în Cod

**10.1** Credențialele (parole, token-uri, chei API) nu se hardcodează în registry sau providers.  
**10.2** Se citesc din variabile de mediu sau din fișiere de configurare securizate.  
**10.3** Exemplu:

```python
import os

def register_all_services():
    register("api", ApiClient(api_key=os.environ["API_KEY"]))
```

> **Motivație:** Securitatea este o cerință non-funcțională fundamentală.

---

## Anexă: Exemplu Complet de Utilizare

```python
#旧的 (cod existent) — continuă să funcționeze:
db = DatabaseClient()
db.connect()

# Nou (prin registry):
from service_registry.providers import register_all_services
from service_registry.registry import get

register_all_services()
db = get("db")
db.connect()
```

---

**Versiune:** 1.0  
**Data:** 2025-01-17  
**Autor:** Assistant AI
