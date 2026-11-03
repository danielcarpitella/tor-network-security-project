# Preparazione esperimento onion: ambiente e rete

Questa guida descrive gli obiettivi, la preparazione dell'ambiente e la rete
Tor Docker ricreata il 3 ottobre 2026, e registra passo passo le attività
dell'esperimento. La sezione della run corrente distingue i passaggi già
eseguiti dai controlli e dall'esportazione dei file.

## 1. Obiettivi

L'esperimento studia una rete Tor privata di laboratorio con un client e un
onion service. La topologia prepara due punti di osservazione distinti:

- il Guard usato dal client;
- il Guard usato dall'onion service.

In questa fase l'obiettivo è predisporre l'ambiente, creare la rete, verificare
che i nodi Tor si avviino e annotare i nomi, gli IP e le impostazioni effettive
della rete.

## 2. Ambiente di lavoro

Eseguire i comandi dalla VM Linux/WSL con Docker disponibile e dalla root della
repository:

```bash
cd ~/tor-network-security-project
docker info
```

La configurazione Python del progetto si prepara in un ambiente virtuale.
Questo repository include le dipendenze della CLI in `requirements.txt` e
richiede Python 3.12 nel `Pipfile`; la guida generale del progetto usa Python
3.11. Usare la versione Python installata e supportata dalla propria VM:

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Per questa fase servono inoltre le dipendenze del generatore onion, che non
sono tutte elencate in `requirements.txt`:

```bash
python -m pip install 'requests[socks]' pandas
```

Verificare che la CLI possa accedere a Docker:

```bash
python cli/main.py --help
```

## 3. Creazione della rete Docker e dell'immagine Tor

> **Attenzione:** la procedura di reset rimuove i container e i dati runtime
> della rete `testing-tor`. Non eseguirla se si vogliono conservare container,
> chiavi o stato della rete attuale. I file della repository non vengono
> rimossi.

Per ricreare da zero la rete del laboratorio:

```bash
docker ps -a --filter name=testing-tor- -q | xargs -r docker rm -f
docker network rm testing-tor 2>/dev/null || true
docker volume rm testing-tor 2>/dev/null || true
rm -f cli/status.json

docker volume create testing-tor
docker network create testing-tor --subnet 10.5.0.0/16
docker build -t testing-tor docker
```

La rete Docker deve esistere prima di aggiungere i nodi. Creare prima le
Directory Authority (DA), attendere che pubblichino il consensus, poi
aggiungere relay ed exit relay. Attendere il consensus aggiornato prima di
creare client e onion service:

```bash
python cli/main.py container add-da --count 3
python cli/main.py container list
```

Dai log di una DA, verificare la pubblicazione del consensus:

```bash
docker logs -f testing-tor-da-NOME
```

Attendere i messaggi `Published ns consensus` e
`Published microdesc consensus`, quindi interrompere la visualizzazione dei
log con `Ctrl+C`. Aggiungere i relay:

```bash
python cli/main.py container add-relay --count 5
python cli/main.py container add-exit --count 3
```

Attendere che il consensus includa gli exit relay. Infine creare il servizio
onion e il client con la porta SOCKS 9050 pubblicata sull'host:

```bash
python cli/main.py container add-hs
python cli/main.py container add-client --port 9050
python cli/main.py container list
```

Il comando `container list` mostra i nomi e gli IP assegnati in quella
creazione. I nomi e gli indirizzi cambiano quando i container vengono
ricreati: usare sempre i valori restituiti dalla rete corrente.

## 4. Rete ricreata il 3 ottobre 2026

La rete corrente è una rete Docker `bridge` chiamata `testing-tor`, con
subnet `10.5.0.0/16`, gateway `10.5.0.1` e IPv6 non usato dai nodi Tor.
La rete non è configurata con l'opzione Docker `--internal`. La porta SOCKS
del client è pubblicata sull'host sulla porta `9050`; gli altri container non
hanno porte pubblicate sull'host.

Per ricontrollare rete e container:

```bash
docker network inspect testing-tor
docker ps --filter name=testing-tor-
python cli/main.py container list
```

### Directory Authority

| Container | IP |
|---|---|
| `testing-tor-da-iikweosm` | `10.5.143.166` |
| `testing-tor-da-faibqdaz` | `10.5.214.144` |
| `testing-tor-da-qrqiqrzi` | `10.5.77.135` |

### Relay ed exit relay

| Ruolo | Container | Nickname Tor | IP |
|---|---|---|---|
| Relay | `testing-tor-relay-jggiztzk` | `relayjggiztzk` | `10.5.215.124` |
| Relay | `testing-tor-relay-sgufrlnh` | `relaysgufrlnh` | `10.5.237.145` |
| Relay | `testing-tor-relay-ngpufjeq` | `relayngpufjeq` | `10.5.184.229` |
| Relay | `testing-tor-relay-cttnxyqo` | `relaycttnxyqo` | `10.5.182.47` |
| Relay | `testing-tor-relay-wrovgfcs` | `relaywrovgfcs` | `10.5.30.22` |
| Exit | `testing-tor-exit-kyocjfzm` | `exitkyocjfzm` | `10.5.2.120` |
| Exit | `testing-tor-exit-dawswzhh` | `exitdawswzhh` | `10.5.117.101` |
| Exit | `testing-tor-exit-niinedtm` | `exitniinedtm` | `10.5.201.132` |

### Client e onion service

| Ruolo | Container | IP | Impostazioni principali |
|---|---|---|---|
| Client | `testing-tor-client-mnfhxjpk` | `10.5.178.226` | SOCKS `0.0.0.0:9050`, pubblicato sull'host come `9050`; ControlPort `9051`; cookie authentication |
| Onion service | `testing-tor-hs-zjlakoee` | `10.5.174.148` | Hidden service sulla porta onion `80`, inoltrata a `127.0.0.1:80`; SOCKS disabilitato |

Indirizzo onion generato per questa istanza:

```text
hy5r6cm6om6oap2obkfx4aaculnjgg72oh6zrvjjy5psdx3ucnmgxsqd.onion
```

Il contenuto e le chiavi dell'onion service appartengono allo stato runtime
di questa istanza; l'indirizzo può cambiare se vengono ricreati container o
volume.

## 5. Impostazioni Tor attive

Le opzioni comuni verificate nei `torrc` dei nodi includono:

```text
TestingTorNetwork 1
AssumeReachable 1
AddressDisableIPv6 1
ControlPort 9051
CookieAuthentication 1
```

Ogni container contiene le direttive `DirAuthority` delle tre DA, necessarie
per usare il consensus della rete di test. I relay hanno `ORPort 9001`; i
client SOCKS non sono abilitati su relay ed exit. Il client ha inoltre:

```text
SocksPort 0.0.0.0:9050
ClientRejectInternalAddresses 0
```

`ClientRejectInternalAddresses 0` è una configurazione del client per il
laboratorio privato; non va trasferita a un client Tor per la rete pubblica.
L'onion service usa:

```text
HiddenServiceDir /var/lib/tor/.tor/hs/
HiddenServicePort 80 127.0.0.1:80
SocksPort 0
```

## 6. Stato dei Guard nella rete corrente

I circuiti Tor attivi sono stati controllati tramite la ControlPort dei due
container:

- Guard osservato nei circuiti del client:
  `relayngpufjeq` — container `testing-tor-relay-ngpufjeq`,
  IP `10.5.184.229`;
- Guard osservato nei circuiti dell'onion service:
  `relaysgufrlnh` — container `testing-tor-relay-sgufrlnh`,
  IP `10.5.237.145`.

I due Guard sono distinti; non è quindi stata aggiunta un'esclusione al
client. Per ispezionarli interattivamente con Nyx:

```bash
docker exec -it testing-tor-client-mnfhxjpk nyx
docker exec -it testing-tor-hs-zjlakoee nyx
```

Questi nomi e Guard descrivono la rete verificata il 3 ottobre 2026. Dopo
aver ricreato i container, ricontrollarli: non sono identificatori permanenti.

## 7. Run `run_01`: server, catture e richieste

La run è stata avviata il 3 ottobre 2026 dalla root della repository, con
l'ambiente virtuale `.venv311` attivo. L'ID assegnato è `run_01`; il generatore
è stato configurato per inviare 100 richieste all'indirizzo onion della rete
corrente.

### 7.1 Avvio del server HTTP sull'onion service

Nel container dell'onion service è stato avviato un server HTTP in ascolto
solo su `127.0.0.1:80`. Il contenuto restituito è `<h1>Hello Tor</h1>` e
l'output, incluso il log delle richieste, viene scritto in
`/tmp/webroot/server_access.log`:

```bash
docker exec -d testing-tor-hs-zjlakoee sh -c \
  'mkdir -p /tmp/webroot && echo "<h1>Hello Tor</h1>" > /tmp/webroot/index.html && python3 -m http.server 80 --bind 127.0.0.1 --directory /tmp/webroot > /tmp/webroot/server_access.log 2>&1'
```
Azzeriamo i log del server prima della cattura successiva:
```bash
docker exec testing-tor-hs-zjlakoee sh -c '> /tmp/webroot/server_access.log'
```

### 7.2 Avvio delle catture sui due Guard

Prima di generare le richieste, sono state avviate due catture in due
terminali distinti. Il Guard client è `relayngpufjeq`; il Guard del servizio
è `relaysgufrlnh`.

**Terminale del Guard client:**

```bash
docker exec -it testing-tor-relay-ngpufjeq \
  tcpdump -i any -nn -s 0 -U \
  -w /tmp/guard_client_run_01.pcap 'tcp port 9001'
```

**Terminale del Guard del servizio:**

```bash
docker exec -it testing-tor-relay-sgufrlnh \
  tcpdump -i any -nn -s 0 -U \
  -w /tmp/guard_service_run_01.pcap 'tcp port 9001'
```

Lasciare entrambi i processi `tcpdump` in esecuzione mentre si generano le
richieste.

### 7.3 Generazione delle richieste

> **Per riprodurre l'esperimento:** prima di lanciare lo script bisogna
> sostituire in `experiments/onion-correlation/traffic_generator.py` il valore
> di `ONION_URL` con l'indirizzo onion della propria rete. L'indirizzo viene
> generato alla creazione dell'onion service e cambia ogni volta che container
> o volume vengono ricreati, quindi quello scritto nello script vale solo per
> la rete originale. Senza questa modifica tutte le richieste vanno in timeout.
> L'indirizzo è il contenuto del file `hostname` del servizio nascosto:
>
> ```bash
> docker exec testing-tor-hs-NOME cat /var/lib/tor/.tor/hs/hostname
> ```
>
> Nello script scrivere `ONION_URL = "http://<indirizzo>.onion/"`, con
> `http://` e la `/` finale. Passare sempre `RUN_ID` (il valore predefinito
> dello script è `run_08`).

Con le due catture attive, dal terminale principale è stato lanciato:

```bash
RUN_ID=run_01 NUM_REQUESTS=100 \
  python experiments/onion-correlation/traffic_generator.py
```

Il generatore salva il log client in
`experiments/onion-correlation/results/run_01/client_requests_log.csv`.
Lo script usa il proxy SOCKS `socks5h://127.0.0.1:9050` se
`TOR_SOCKS_PROXY` non è impostata, attende un intervallo casuale di 2-5
secondi tra le richieste e applica un timeout di 30 secondi a ciascuna
richiesta. Il CSV registra ID, timestamp iniziale e finale, latenza,
dimensione della risposta, stato HTTP, esito ed eventuale messaggio di errore.
La cartella di output è specifica per `RUN_ID`; se esiste già, lo script
interrompe l'esecuzione senza sovrascriverla.
Al termine delle 100 richieste, fermare **entrambi** i processi `tcpdump`
premendo `Ctrl+C` nei rispettivi terminali. Attendere che ciascuno mostri il
riepilogo e chiuda il file PCAP prima di copiarlo.

La verifica del CSV prodotto per questa run ha trovato 100 righe di richieste,
tutte riuscite con stato HTTP 200.

### 7.4 Esportazione dei file

Assicurarsi che la cartella della run esista:

```bash
mkdir -p experiments/onion-correlation/results/run_01
```

Dopo aver fermato le catture, copiare i PCAP e il log HTTP dai container alla
cartella locale:

```bash
docker cp testing-tor-relay-ngpufjeq:/tmp/guard_client_run_01.pcap \
  experiments/onion-correlation/results/run_01/guard_client_capture.pcap

docker cp testing-tor-relay-sgufrlnh:/tmp/guard_service_run_01.pcap \
  experiments/onion-correlation/results/run_01/guard_service_capture.pcap

docker cp testing-tor-hs-zjlakoee:/tmp/webroot/server_access.log \
  experiments/onion-correlation/results/run_01/server_access.log
```

Verificare che il CSV, i due PCAP e il log HTTP siano presenti:

```bash
ls -lh experiments/onion-correlation/results/run_01/
```

Per `run_01` il CSV, i due PCAP e il log HTTP sono stati esportati nella
cartella locale. Prima di questa esportazione, i PCAP e il log HTTP erano
presenti solo nei rispettivi container.

### 7.5 Ritaglio temporale e CSV di metadati

Dopo aver raccolto i PCAP, è stata ricavata una finestra temporale dal CSV
delle richieste client. Il primo `timestamp_start` è
`1791023682.9766412`; sottraendo 5 secondi si ottiene l'inizio della finestra:

```text
1791023682.9766412 - 5 = 1791023677.9766412
```

L'ultimo `timestamp_end`, relativo alla richiesta 100, è `1791024029.3492723`;
aggiungendo 5 secondi si ottiene la fine:

```text
1791024029.3492723 + 5 = 1791024034.3492723
```

Il margine di cinque secondi prima e dopo la sequenza di richieste conserva
un breve contesto di traffico attorno alla finestra dell'esperimento. La
stessa finestra viene applicata a entrambe le catture per rendere confrontabili
nel tempo l'osservazione del Guard client e quella del Guard service. Il
ritaglio temporale riduce il traffico esterno all'intervallo scelto, ma non
dimostra che ogni pacchetto rimasto sia stato causato da una richiesta HTTP.

Per creare i PCAPNG ritagliati e i CSV di metadati per `run_01`:

```bash
mkdir -p experiments/onion-correlation/results/run_01/analysis/window_5s

editcap -A "1791023677.9766412" -B "1791024034.3492723" -F pcapng \
  experiments/onion-correlation/results/run_01/guard_client_capture.pcap \
  experiments/onion-correlation/results/run_01/analysis/window_5s/guard_client_capture_window.pcapng

editcap -A "1791023677.9766412" -B "1791024034.3492723" -F pcapng \
  experiments/onion-correlation/results/run_01/guard_service_capture.pcap \
  experiments/onion-correlation/results/run_01/analysis/window_5s/guard_service_capture_window.pcapng

tshark \
  -r experiments/onion-correlation/results/run_01/analysis/window_5s/guard_client_capture_window.pcapng \
  -T fields -e frame.time_epoch -e frame.len -e ip.src -e ip.dst \
  -E header=y -E separator=, \
  > experiments/onion-correlation/results/run_01/analysis/window_5s/client_metadata.csv

tshark \
  -r experiments/onion-correlation/results/run_01/analysis/window_5s/guard_service_capture_window.pcapng \
  -T fields -e frame.time_epoch -e frame.len -e ip.src -e ip.dst \
  -E header=y -E separator=, \
  > experiments/onion-correlation/results/run_01/analysis/window_5s/service_metadata.csv
```

I CSV contengono timestamp Unix del frame, lunghezza del frame e indirizzi IP
di origine e destinazione; sono tabelle leggere da caricare ed elaborare in un
Jupyter Notebook. La procedura è stata ripetuta per **tutte e cinque le run
`run_01`–`run_05`**. Per ogni run sono stati ricavati i limiti temporali dal
relativo CSV delle richieste, aggiungendo e sottraendo 5 secondi; i timestamp
usati per `run_01` qui sopra sono un esempio specifico e non vanno riutilizzati
per le altre run.

I due PCAPNG ritagliati e i due CSV di metadati sono presenti nelle rispettive
cartelle `results/<run_id>/analysis/window_5s/`. La verifica dei file e dei
CSV client mostra 100 richieste riuscite per ciascuna run:

| Run | Richieste riuscite | Record CSV Guard client | Record CSV Guard service |
|---|---:|---:|---:|
| `run_01` | 100 | 1.656 | 1.482 |
| `run_02` | 100 | 3.921 | 1.738 |
| `run_03` | 100 | 2.979 | 1.520 |
| `run_04` | 100 | 1.650 | 2.439 |
| `run_05` | 100 | 2.606 | 2.806 |

Ogni cartella di run contiene inoltre i PCAP originali, il CSV delle richieste
client e il log HTTP del server. I conteggi dei record nei CSV dei Guard
differiscono tra osservazioni e run perché rappresentano i frame estratti da
ciascun PCAP ritagliato, non il numero di richieste HTTP.

## Risultati delle prove precedenti

Per ricominciare con una cartella di output pulita, i risultati raccolti prima
del nuovo inizio sono stati spostati in
[`old_network_results/`](./old_network_results/). La cartella contiene i
risultati delle run precedenti e i log raccolti, mantenuti nella loro
struttura originale. La cartella [`results/`](./results/) contiene le nuove
run `run_01`–`run_05`, con PCAP, log HTTP, PCAPNG ritagliati e CSV di metadati.

## Nota di sicurezza

Questa rete `TestingTorNetwork` è destinata esclusivamente al laboratorio.
Non usare queste impostazioni come configurazione per la rete Tor pubblica.
La porta SOCKS del client è esposta su tutte le interfacce host (`0.0.0.0` e
`::`): mantenere l'ambiente su una macchina/rete fidata e non esporre la porta
9050 a reti non fidate.

## 8. Analisi dei burst e risultati delle cinque run

L'analisi dei risultati è stata eseguita nel notebook
[`onion_analysis.ipynb`](./onion_analysis.ipynb), usando i CSV di metadati
generati da `tshark` in `results/<run_id>/analysis/window_5s/`. Per ciascuna
osservazione (Guard client e Guard del servizio), i pacchetti TCP sulla porta
OR `9001` sono stati raggruppati in burst: inizia un nuovo burst quando
cambia la direzione del traffico o quando il distacco temporale dal pacchetto
precedente supera `0,1 s`. Sono mantenuti burst di almeno `1000 byte`.
L'analisi usa cinque run (`run_01`-`run_05`), con 100 richieste riuscite per
run; gli indirizzi dei Guard sono `10.5.184.229` (client) e `10.5.237.145`
(service).

I burst client vengono esaminati in ordine cronologico; ciascuno è abbinato
al burst service non ancora usato più vicino entro la finestra di lag
`[-0,1 s, +0,5 s]` (inizio service meno inizio client). Il matching è
strettamente uno-a-uno. I controlli sono lo spostamento della traccia service
della stessa run di 10, 20, 30, 40 o 50 secondi (media dei cinque
spostamenti) e il confronto con la traccia service di ciascuna delle altre
quattro run, allineata al primo timestamp di richiesta (media delle quattro
run). Una richiesta è coperta se la sua finestra temporale estesa dai margini
di lag contiene almeno un inizio di burst client abbinato. I parametri sono
scelte manuali, non soglie calibrate.

### Risultati per run

| Run | Burst client | Burst service | Coppie reali | Coppie reali % | Controllo shift % | Controllo cross-run % | Richieste coperte | Coperte, shift (media) | Coperte, cross-run (media) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `run_01` | 179 | 173 | 167 | 93,3 | 9,7 | 12,6 | 82/100 | 8,8 | 11,0 |
| `run_02` | 413 | 199 | 177 | 42,9 | 8,1 | 11,8 | 83/100 | 15,8 | 17,5 |
| `run_03` | 400 | 198 | 180 | 45,0 | 6,1 | 9,9 | 90/100 | 12,2 | 13,2 |
| `run_04` | 218 | 314 | 174 | 79,8 | 10,4 | 15,0 | 86/100 | 11,4 | 15,5 |
| `run_05` | 298 | 493 | 176 | 59,1 | 9,3 | 10,1 | 88/100 | 13,2 | 14,5 |
| **Totale** | **1508** | **1377** | **874** | **58,0** | **8,3** | **11,5** | **429/500** | **61,4** | **71,8** |

La mediana pooled del lag delle coppie reali è `-0,0008 s`. Le percentuali
sono statistiche di timing: indicano la quota di burst client che trova un
partner, non precision, recall o accuratezza. La copertura reale è `429/500`
(85,8%); la sensibilità riportata sotto confronta questa misura con i
controlli.

### Sensibilità a lag e direzione

I valori sono pooled sulle cinque run. Nei controlli, le percentuali di coppie
sono calcolate rispetto ai burst client; la copertura è la quota di richieste.

| Finestra di lag | Direzione | Coppie reali % | Shift % | Cross-run % | Richieste coperte % | Copertura cross-run % |
|---|---|---:|---:|---:|---:|---:|
| da `-0,1` a `+0,5 s` | any / same / opposite | 58,0 | 8,3 | 11,5 | 85,8 | 14,4 |
| da `-0,05` a `+0,05 s` | any / same / opposite | 57,9 | 1,5 | 2,6 | 85,8 | 3,1 |
| da `-0,02` a `+0,02 s` | any | 57,8 | 0,8 | 1,5 | 85,8 | 1,8 |
| da `-0,02` a `+0,02 s` | same / opposite | 57,8 / 57,7 | 0,8 | 1,5 | 85,8 | 1,7 / 1,8 |

Ridurre la finestra a `±0,05 s` o `±0,02 s` lascia quasi invariata la quota
reale di coppie e copertura delle richieste, mentre riduce i valori di
controllo. La direzione dei pacchetti (`any`, `same`, `opposite`) non cambia
materialmente i risultati.

### Diagnostica e limiti interpretativi

La tabella riporta i burst dentro le finestre di richiesta, quelli tra
richieste e i frame conteggiati nei due CSV di metadati:

| Run | Burst client per richiesta | Burst service per richiesta | Burst client tra richieste | Burst service tra richieste | Pacchetti client / service |
|---|---:|---:|---:|---:|---:|
| `run_01` | 1,79 | 1,67 | 0 | 6 | 1656 / 1482 |
| `run_02` | 3,93 | 1,79 | 20 | 20 | 3921 / 1738 |
| `run_03` | 4,00 | 1,80 | 0 | 18 | 2979 / 1520 |
| `run_04` | 2,18 | 3,14 | 0 | 0 | 1650 / 2439 |
| `run_05` | 2,95 | 2,63 | 3 | 230 | 2606 / 2806 |

I run `run_02` e `run_03` hanno più burst client per richiesta e circa il
doppio dei pacchetti rispetto al service; quasi tutti i burst client ricadono
nelle finestre di richiesta (non sono quindi traffico tra una richiesta e
l'altra). La tabella dei pacchetti per peer mostra che in quei run il Guard
client scambia circa 2,5 volte più pacchetti con altri relay che con il
container client (rapporto 2,72 in `run_02`, 2,53 in `run_03`, contro 1,05 in
`run_01`); lo stesso accade al Guard service in `run_04` (2,43) e in parte in
`run_05`. Un rapporto vicino a 1 è atteso per un Guard che inoltra solo il
traffico del proprio circuito, quindi i dati sono compatibili con un Guard che
trasporta anche traffico di altri circuiti: la rete ha pochi relay (5 relay, 3
exit, 3 DA che fanno anche da relay) e gli stessi nodi possono comparire in più
posizioni. Questa spiegazione **non è ancora confermata** da dati sui circuiti
(`GETINFO circuit-status`). In `run_05` la diagnostica mostra inoltre 230 burst
service tra le richieste, concentrati intorno a `t = 275 s` con dimensione
mediana di circa `3,3 kB`; la causa non è stata determinata.

**Collegamento diretto tra i due Guard.** I due Guard si scambiano anche
pacchetti tra loro: 657 in `run_03`, 118 in `run_04`, 18 in `run_02`, 8 in
`run_01`, 6 in `run_05`. Le stesse catture contengono questi pacchetti con gli
stessi timestamp, quindi possono abbinarsi in modo banale e gonfiare la
correlazione, soprattutto in `run_03` e `run_04`. Il notebook (sezione 7) ripete l'analisi senza questi pacchetti; i risultati vanno riportati qui dopo l'esecuzione (salvati in  onion_robustness_link.csv e  onion_robustness_size.csv).

Senza questi pacchetti (103 burst client in meno, da 1508 a 1405) la quota di burst abbinati scende dal 57,9% al 54,8% con la finestra `±0,05 s`, i controlli restano all'1,6% (shift) e al 2,4% (cross-run) e la copertura delle richieste resta all'85,8% (3,1% nel controllo cross-run). Il collegamento diretto gonfia quindi leggermente l'abbinamento ma non spiega la correlazione, che resta circa 23 volte sopra il controllo cross-run.

Le 71 richieste non coperte non hanno nessun burst service da almeno `1000
byte` nella propria finestra (46 non lo hanno nemmeno lato client), e nessuna
ha burst grandi su entrambi i lati senza coppia. Hanno comunque circa 13
burst più piccoli per lato. La mancata copertura dipende quindi dal filtro di
`1000 byte`, non dalla regola temporale; il notebook (sezione 7) ripete
l'analisi con soglie da 0 a 1500 byte, i cui risultati vanno confrontati con i
controlli prima di cambiare soglia.

**Conclusione e limiti.** I dati mostrano una correlazione temporale in
questa configurazione: con la finestra stretta `±0,05 s`, il 58% circa dei
burst client è abbinato nel dato reale contro l'1,5-2,6% nei controlli, e
l'85,8% delle richieste è coperto contro il 3,1% nel controllo cross-run.
Il lag mediano è vicino a zero, ma l'esperimento usa una sola macchina e un
solo clock, una pagina molto semplice, un solo client e un solo service;
non riproduce latenza e differenze di clock della rete Tor pubblica. I burst
sono dimensioni dei frame catturati (incluse celle Tor/TLS), non payload HTTP.
I log forniscono ground truth a livello di richiesta con risoluzione di un
secondo, non etichette individuali per burst. Le percentuali non sono
precision, recall o accuratezza e non provano la de-anonimizzazione.

## 9. Passi ancora aperti per il contributo onion (Persona B)

L'acquisizione delle cinque run, l'analisi e la documentazione del contributo
onion sono state completate. I passi ancora aperti riguardano l'integrazione
con il lavoro degli altri membri e, solo se richiesto, un'estensione della
validazione:

### Verifiche ancora aperte

- Confermare con `GETINFO circuit-status` (ControlPort dei due container,
  durante una run) se i circuiti di client e service condividono relay, come
  suggerisce la tabella dei pacchetti per peer.
- Controllare i log del container dell'onion service intorno a `t = 275 s` di
  `run_05` (`docker logs --since`); se i log non ci sono più, annotare "causa
  non determinata".
- Eseguire la sezione 7 del notebook (esclusione del collegamento diretto tra
  Guard e soglie sulla dimensione dei burst) e riportare i risultati in
  `ONION_CORRELATION_REPORT.md` (sezione 4.6) e nella sezione 8.

### Da fare per l'integrazione nel progetto di gruppo

- Condividere con Persona A e Persona C le convenzioni in
  [`../../docs/METHODOLOGIES.md`](../../docs/METHODOLOGIES.md) e chiarire che
  i punti osservati differiscono: Guard client–Guard service per onion,
  Guard client–Exit relay per clearnet.
- Fornire a Persona C una tabella esportabile dei risultati per run, usando
  le stesse definizioni e unità concordate. Se le feature o i parametri di
  Persona A differiscono, annotare le differenze e non presentare i numeri
  come direttamente equivalenti.
- Verificare con Persona C quali output e formato servono alla comparazione
  finale; i risultati attuali sono nel notebook e nel presente diario, ma non
  è stato aggiunto un file tabellare dedicato all'interscambio.

### Estensione facoltativa: etichette più forti a livello di burst

I log attuali sono ground truth delle richieste HTTP ricevute dal servizio:
contengono eventi con timestamp a risoluzione di un secondo e status, che il
notebook allinea al CSV client. Non etichettano però i singoli burst catturati
ai Guard; l'associazione burst-richiesta resta basata su finestre temporali
euristiche. Quindi questa estensione è necessaria **solo** se il gruppo o il
docente richiede metriche supervisionate a livello di burst, come precision
e recall:

1. Eseguire prove controllate con una richiesta per volta, intervalli di
   silenzio tra richieste e un identificatore univoco della richiesta
   registrato sia dal client sia dal server.
2. Acquisire simultaneamente entrambi i punti di osservazione e anche
   intervalli di controllo senza richieste, per distinguere il traffico
   associato all'esperimento dal traffico di fondo.
3. Valutare l'allineamento con timestamp più precisi, senza assumere che la
   vicinanza temporale da sola dimostri quale burst sia stato causato da una
   richiesta.
4. Se servono etichette certe per ogni burst o coppia, definire un metodo
   indipendente per collegare i pacchetti ai circuiti/eventi applicativi.
   Questo richiede strumentazione aggiuntiva di Tor o un'altra sorgente di
   osservabilità e va dichiarato come estensione del modello sperimentale.

Le prove controllate possono rafforzare l'attribuzione alle richieste, ma non
trasformano automaticamente ogni burst in ground truth. Finché non viene
definita e verificata un'etichettatura indipendente, mantenere i risultati
attuali descritti come copertura e coerenza temporale euristiche; non
ricalcolarli come precision, recall o accuratezza.

### Stato del commit e della pubblicazione

Le modifiche del contributo sono state registrate in un commit locale. Al
momento non è ancora possibile pubblicarlo con `push`, perché l'autrice non è
stata ancora aggiunta al repository con i permessi necessari. Il commit resta
quindi disponibile nella copia locale fino alla concessione dell'accesso.
