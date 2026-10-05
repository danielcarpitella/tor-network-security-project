# Convenzioni metodologiche condivise

Questo documento riassume le convenzioni utili a mantenere compatibili le
analisi dei due scenari e a consentire il confronto tra topologie. Non
prescrive che gli esperimenti abbiano gli stessi punti di osservazione:

- **Onion service:** Guard del client e Guard dell'onion service.
- **Clearnet:** Guard del client ed exit relay.

La topologia e il significato dei due punti osservati devono sempre essere
indicati nei risultati. Non aggregare direttamente i conteggi dei due scenari.

## Variabili e unità

Usare nomi e unità coerenti per le variabili condivise:

| Variabile | Significato | Unità / convenzione |
|---|---|---|
| `timestamp` | Istante di cattura del frame | Unix epoch, secondi |
| `size` | Lunghezza del frame catturato | Byte (`frame.len`), non payload HTTP |
| `direction` | Direzione rispetto al nodo osservato | `+1` uscente dal nodo, `-1` entrante |
| `timestamp_start`, `timestamp_end` | Inizio e fine del burst | Unix epoch, secondi |
| `total_size` | Somma delle dimensioni dei frame nel burst | Byte |
| `packet_count` | Numero di frame nel burst | Conteggio |
| `duration_sec` | `timestamp_end - timestamp_start` | Secondi |
| `lag_seconds` | `timestamp_start` del secondo punto meno quello del primo | Secondi; mantenere esplicito l'ordine |

Per ogni richiesta, mantenere inoltre un `request_id` stabile e i timestamp di
inizio e fine registrati dal generatore. Conservare separatamente il log del
client e il log del server; il log client da solo non conferma che il server
abbia ricevuto la richiesta.

## Regole di aggregazione e matching usate nell'analisi onion

Questi sono i valori e le regole adottati nel notebook onion; usarli anche
nell'analisi clearnet quando l'obiettivo è un confronto quantitativo diretto.
Se il modello clearnet richiede una scelta diversa, mantenerla documentata e
separare chiaramente i risultati invece di presentarli come direttamente
equivalenti.

- Ordinare i frame per timestamp separatamente per ciascun punto di
  osservazione.
- Iniziare un nuovo burst se il distacco dal frame precedente è maggiore di
  **0,1 secondi** oppure se cambia `direction`.
- Considerare per il matching i burst con `total_size >= 1000` byte.
- Usare un intervallo di lag da **-0,1 a +0,5 secondi**, definito come
  timestamp del secondo punto meno timestamp del primo.
- Applicare matching **uno-a-uno greedy**: per ogni burst del primo punto, in
  ordine cronologico, scegliere il burst disponibile del secondo punto con
  lag ammesso più vicino in valore assoluto; non riutilizzare un burst già
  abbinato.

Le soglie sono parametri esplorativi usati per l'analisi, non soglie
scientificamente calibrate. La dimensione dei burst è quella dei frame
catturati, inclusi gli header presenti nella cattura.

## Risultati da rendere confrontabili

Per ogni topologia e run riportare almeno:

- numero di richieste registrate e confermate dai log disponibili;
- numero di burst sopra la soglia per ciascun punto di osservazione;
- numero di coppie candidate e lag tra i burst abbinati;
- copertura dei burst su entrambi i punti di osservazione;
- richieste distinte rappresentate da almeno una coppia, se l'attribuzione ai
  log è possibile senza ambiguità.

Usare le stesse definizioni per le metriche in entrambi gli scenari e
riportare numeratori e denominatori. La compatibilità temporale con una
richiesta è una misura euristica: non dimostra causalità e non equivale a
precision, recall, accuracy o de-anonimizzazione. Non calcolare metriche
supervisionate senza una ground truth indipendente e una definizione esplicita
di veri/falsi positivi e negativi.

Per consentire la lettura congiunta, annotare per ogni run l'identificatore,
la topologia, i ruoli dei punti osservati, i parametri effettivi e le eventuali
deviazioni da queste convenzioni.
