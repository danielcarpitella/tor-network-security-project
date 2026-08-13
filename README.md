# Rete Tor privata per esperimenti

Questa repo è stat fatta a partire da un clone della repo: https://github.com/daxAKAhackerman/testing-tor-network su cui è basata l'articolo: https://medium.com/@dax_dev/creating-a-testing-tor-network-from-scratch-e952d76a18cb
cioè l'articolo che il prof ha consigliato nel docuemento dove sono esposti i progetti.

è suddivisa in una "base comune" che sarebbe la cartella shared-base e poi c'è la cartella experiments dove ognuno di noi 3 ha la sua cartellla dove si sviluppa individualmente la sua parte. 

In questo README ho inserito anche una breve parte teroca dove dovrebbe esserci lo stretto necessario da sapere per lavorare sul progetto. 

Prima di iniziare consiglio di fare qualche prova e mettere le mani in pasta sia con clernet che con onion service, docs/GETTING_STARTED.md qui ci sono le indicazioni per fare le prove. 

In docs/PROJECT_PLAN.md c'è una sorta di docuemntazione generale di tutto il progetto, così poi tipo il report da presentare al prof lo facciamo in poco.

Boh dovrebbe essere tutto.


- la cartella/parte di Carpi è experiment/clearnet-correlation


## Base condivisa ed esperimenti

Un nuovo membro del gruppo dovrebbe seguire questo percorso:

1. clonare la repository e seguire [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md);
2. avviare la rete e riprodurre le prove onion e clearnet;
3. iniziare il proprio lavoro nella cartella assegnata sotto
   [`experiments/`](experiments/).

La cartella [`shared-base/`](shared-base/) contiene la **base comune** del
laboratorio: gli script e le istruzioni riutilizzabili per gli scenari onion e
clearnet. Non contiene attacchi o risultati individuali.

La cartella [`experiments/`](experiments/) contiene invece il lavoro di ricerca
costruito sopra tale base. Ogni esperimento ha uno spazio separato per evitare
che una modifica all'analisi individuale cambi involontariamente lo scenario
comune:

- [`clearnet-correlation/`](experiments/clearnet-correlation/) — correlazione
  tra guard del client ed exit relay nello scenario clearnet;
- [`onion-correlation/`](experiments/onion-correlation/) — correlazione tra
  guard del client e guard del servizio onion;
- [`topology-comparison/`](experiments/topology-comparison/) — confronto tra
  scenari e topologie.

## Basi teoriche essenziali

Tor è una rete di anonimizzazione a bassa latenza. Invece di collegarsi
direttamente al server, il client crea un circuito attraverso più relay. Ogni
relay conosce solo il nodo precedente e il nodo successivo: nessun relay
intermedio conosce contemporaneamente identità del client e destinazione.

Prima di creare un circuito, il client scarica il **consensus**: un documento
firmato dalle directory authorities che elenca relay, chiavi, indirizzi e flag
disponibili. Il client sceglie poi normalmente un guard, un middle relay e,
per una destinazione non onion, un exit relay.

### Cifratura a strati

Il nome *Onion Router* deriva dalla cifratura a strati. Per un circuito
`Client → Guard → Middle → Exit`, il client prepara il traffico in modo che:

```mermaid
flowchart LR
    payload["Richiesta HTTP"] --> exitLayer["Strato per Exit"]
    exitLayer --> middleLayer["Strato per Middle"]
    middleLayer --> guardLayer["Strato per Guard"]
    guardLayer --> guard["Guard rimuove il proprio strato"]
    guard --> middle["Middle rimuove il proprio strato"]
    middle --> exit["Exit rimuove l'ultimo strato"]
    exit --> server["Server HTTP"]
```

Il guard sa quale client gli ha aperto il circuito, ma non conosce la
destinazione finale. L'exit conosce la destinazione, ma riceve traffico dal
middle e non conosce l'IP del client.

Questa proprietà non rende Tor invulnerabile: un osservatore che controlli o
osservi entrambi gli estremi del circuito può confrontare tempi, direzione,
dimensione dei burst e volume del traffico. Questo è il modello sperimentale
alla base del progetto.

Ruoli principali:

- **Directory authority**: pubblica il consensus, cioè la lista firmata dei
  relay disponibili.
- **Guard**: primo relay scelto dal client.
- **Middle relay**: inoltra traffico tra guard ed exit/rendezvous.
- **Exit relay**: ultimo relay per una destinazione clearnet.
- **Onion service**: server pubblicato con un indirizzo `.onion`.
- **Rendezvous relay**: punto d'incontro dei circuiti client e onion service.

### Scenario clearnet interno

```mermaid
flowchart LR
    user["curl o browser"] -->|"SOCKS5 :9050"| client["Client Tor"]
    client --> guard["Guard"]
    guard --> middle["Middle"]
    middle --> exit["Exit relay"]
    exit -->|"HTTP"| web["Server HTTP interno"]
```

Il server HTTP registra l'IP dell'exit, non quello del client. Nel laboratorio
il server è interno a Docker, ma il ruolo dell'exit resta lo stesso del caso
clearnet classico.

### Scenario onion service

```mermaid
flowchart LR
    client["Client Tor"] --> guardClient["Guard client"]
    guardClient --> middleClient["Middle client"]
    middleClient --> rendezvous["Rendezvous relay"]
    onion["Onion service"] --> guardService["Guard service"]
    guardService --> middleService["Middle service"]
    middleService --> rendezvous
```

Non c'è un exit relay: client e onion service non rivelano direttamente il
proprio IP all'altro lato. Il client ottiene le informazioni del servizio,
sceglie un rendezvous relay e costruisce il proprio circuito; il servizio
costruisce separatamente un circuito verso lo stesso rendezvous.

Per l'esperimento onion, un attaccante realistico non osserva direttamente il
container onion service. Le osservazioni usate per il matching sono quelle del
guard del client e del guard del servizio; i log del servizio servono solo
come ground truth per verificare il risultato.

## Stato delle prove

Sono già state verificate:

- richiesta HTTP verso un onion service attraverso SOCKS5;
- richiesta HTTP verso un server clearnet interno;
- separazione tra le reti Docker `testing-tor` e `clearnet-target`;
- server clearnet che osserva l'exit relay come peer TCP.

Per i comandi completi e riproducibili, seguire
[`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md).
