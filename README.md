# GPU Share

Sdílení Windows počítačů s LM Studiem v malé důvěryhodné skupině. Koordinátor poskytuje jednu adresu pro OpenAI Chat Completions. Agent u každé grafiky měří dostupnost, rozhoduje o přijetí požadavku a ukazuje v oznamovací oblasti jméno uživatele. Python 3.11+.

## První spuštění na Windows

1. Propoj koordinátor, oba hostitele a klienty do stejné soukromé sítě Tailscale. Koordinátor může běžet na stálém serveru bez GPU nebo na jednom z hostitelů. V konfiguraci použij jejich Tailscale IPv4. Povol v síti klientům TCP 8787 na koordinátor a koordinátoru TCP 8788 na hostitele. Porty jsou určeny pro šifrovaný tunel. LM Studio ponech na loopbacku. Koordinátor vidí obsah požadavků při přeposílání.
2. Na každém GPU počítači stáhni lokální model, spusť server v LM Studiu na portu 1234 a povol Just-in-Time Model Loading, aby seznam modelů zahrnoval stažené modely. Na těchto hostitelích vypni LM Link pro jednoznačný výběr lokální grafiky. Ověř příkazy `lms ls`, `lms load --help` a `Invoke-RestMethod http://127.0.0.1:1234/v1/models`. Při zapnutém ověření nastav pro agenta proměnnou prostředí `LM_API_TOKEN` a ověř také funkčnost lokálního CLI.
3. Na koordinátoru i hostitelích stáhni tento projekt. Ve složce projektu spusť:

~~~powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
~~~

4. Jen na koordinátoru spusť `.\.venv\Scripts\python.exe configure.py`. Průvodce připraví adresář `private`. Zadej samostatný název pro každého ze tří lidí. Pro každou grafiku vyber přesný identifikátor modelu dostupný zároveň v CLI i API. Rozpočet modelu v MiB odvoď na daném PC z `lms load --estimate-only MODEL --context-length 4096` a uprav podle měření. Kapacita pro RX 6700 XT je 12288 MiB, pro RX 7800 XT 16384 MiB. Výchozí rezerva pro ostatní programy je 1024 MiB.
5. Každému hostiteli soukromě zkopíruj jen jeho složku `agent-N`. Každému uživateli dej jeho `client-N.json`. Soubor `hub.json` obsahuje všechny klíče a patří pouze správci. Vzorové příkazy předpokládají, že jsi zachoval názvy složek:

~~~powershell
# Koordinátor
.\.venv\Scripts\python.exe main.py private/hub.json
# První hostitel, v jeho přihlášené Windows relaci
.\.venv\Scripts\python.exe main.py private/agent-1/agent.json
~~~

6. V klientovi s podporou vlastní OpenAI Base URL nastav hodnoty `base_url` a `api_key` ze svého souboru. Chat zadaný přímo v běžném okně LM Studia nadstavbu obchází. Nastavené uživatelské jméno patří držiteli klíče.

## Použití API

~~~powershell
$c = Get-Content private/client-1.json -Raw | ConvertFrom-Json
$h = @{Authorization = "Bearer $($c.api_key)"}
Invoke-RestMethod "$($c.base_url)/hosts" -Headers $h
$models = Invoke-RestMethod "$($c.base_url)/models" -Headers $h
$body = @{model=$models.data[0].id; messages=@(@{role="user"; content="Ahoj!"})} | ConvertTo-Json -Depth 6
Invoke-RestMethod "$($c.base_url)/chat/completions" -Method Post -Headers $h -ContentType 'application/json; charset=utf-8' -Body $body
~~~

| Endpoint | Výsledek |
|---|---|
| GET /v1/hosts | Stav počítačů, důvod nedostupnosti, volná VRAM, vytížení, čas od vstupu, aktivní uživatel a modely, které se vejdou |
| GET /v1/models | Sjednocený seznam povolených modelů nalezených na připojených strojích |
| POST /v1/chat/completions | Jedna generace, JSON nebo SSE podle `stream`. Další pole chatového požadavku se předají LM Studiu. |

Každý nový požadavek ověří stavy všech nakonfigurovaných hostitelů souběžně. Přednost má počítač bez vstupu posledních 5 minut, potom grafika s největší volnou pamětí. Každý agent obslouží jednu generaci. Při kolizi dvou klientů koordinátor zkusí další vhodnou GPU. Nedostupná kapacita vrací 503. Po ztrátě spojení během generování klient dostane chybu nebo neúplný stream. Nový požadavek může použít jiný stroj. Historii chatu posílá klient v poli messages.

## Když spustíš hru

Agent periodicky měří dedikovanou paměť a vytížení enginů přes Windows GPU Performance Counters. Vlastník v agent.json nastavuje kapacitu vybraného adaptéru, seznam EXE her, rezervu a mez vytížení pro novou práci. Při více adaptérech spusť v PowerShellu `Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory | Select-Object Name,DedicatedUsage` a nastav přesný název instance do pole `adapter`. Zkontroluj změnu DedicatedUsage při zátěži cílové karty. Nedostupná nebo zastaralá telemetrie blokuje nové požadavky.

Pravým tlačítkem na ikonu zvol **Pozastavit sdílení**. Pauza přetrvá restart díky souboru paused vedle konfigurace agenta. Spuštění hry ze seznamu, ruční pauza nebo pokles volné VRAM pod rezervu během generování ukončí požadavek při následující kontrole. Probíhající načítání modelu může nejprve doběhnout. Každý požadavek má vlastní pojmenovanou instanci a agent po skončení požádá o její uvolnění. TTL 60 sekund kryje případ pádu agenta. Nové načtení pro každou zprávu znamená delší čekání na první token.

## Provoz a ověření

* Windows oznámení vyžadují přihlášenou relaci a povolené notifikace. Přepínač `--console` vypisuje události do terminálu. Ukončení procesu pomocí Ctrl+C. Pro automatický start lze stejný příkaz přidat do Plánovače úloh při přihlášení.
* Stav offline znamená, že koordinátor neobdržel platný stav do tří sekund. Prověř Tailscale adresu, port, klíč a běžící proces. Po návratu hostitele ho další požadavek znovu zkusí. Výpadek samotného koordinátoru vyřadí společný endpoint do jeho opětovného spuštění.
* Změny souborů JSON se načtou při restartu příslušné role. Pro odebrání uživatele odstraň jeho klíč z hub.json a restartuj koordinátor.
* Lokální testy směrování a streamu používají simulovaný modelový server: `python -m unittest -v`. Na skutečném hostiteli ověř GPU čítače v porovnání se Správcem úloh, oznámení, uvolnění VRAM po generaci a přerušení po zapnutí pauzy.

