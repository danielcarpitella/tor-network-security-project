# Esperimento clearnet — piano e diario

Questo README è il riferimento di lavoro per la parte clearnet. Qui si
aggiornano il piano, le decisioni metodologiche e il diario delle prove.
La [bozza del report clearnet](CLEARNET_REPORT.md) usa queste note per
descrivere metodo, risultati e limiti.

L'esperimento studia la compatibilità temporale tra le osservazioni sul guard
del client e sull'exit relay durante richieste HTTP al server interno della
[rete Tor privata del laboratorio](../../shared-base/clearnet/).

## Prossimo passo

Preparare **la prima raccolta sperimentale** usando la topologia e il target
HTTP già verificati. La configurazione di riferimento proposta è:

- Un client, un target HTTP e la stessa pagina per tutte le richieste.
- 100 richieste sequenziali, con pause di 2–5 secondi, riprendendo numero e
  cadenza delle richieste dall'[esperimento onion](../onion-correlation/ONION_CORRELATION_MINIMAL.md).
- Un ID univoco per richiesta e log di client e server per verificare gli
  esiti HTTP, come in `verifica_log_http`.

La topologia di `primo_run_sperimentale` è registrata nel diario. Prima di inviare le
richieste, definire il riepilogo del run. Per i log HTTP, riportare tentativi,
risposte 200 lato client, richieste confermate dal server ed errori. Per le
misure temporali aggregate, indicare fonte, unità, periodo osservato e
criterio di valutazione. Il [report onion sintetico](../onion-correlation/ONION_CORRELATION_MINIMAL.md)
è un riferimento per la struttura della tabella e per la descrizione del
controllo.

## Piano

- [x] Verificare la connettività verso il target HTTP via SOCKS5.
- [x] Controllare che gli ID delle richieste colleghino i log di client e server.
- [x] Salvare l'elenco dei container attivi per `primo_run_sperimentale`.
- [ ] Fissare e registrare la configurazione del primo run sperimentale.
- [ ] Svolgere il run e riepilogare richieste tentate, riuscite lato client,
  confermate dal server ed eventuali errori.
- [ ] Documentare le misure di copertura con numeratore, denominatore e
  condizioni della prova secondo le
  [convenzioni metodologiche](../../docs/METHODOLOGIES.md).
- [ ] Riportare metodo, risultati e limiti nella bozza clearnet, seguendo le
  [indicazioni per il report finale](../../docs/PROJECT_PLAN.md#consegna-e-report-finale).

## Diario

### Verifica preliminare della raccolta dati

- **19 novembre 2026 · `verifica_log_http`.** Cinque richieste HTTP sequenziali
  inviate via SOCKS5 al server interno, con un `request_id` univoco nell'URL.
- **Esito.** Cinque risposte HTTP 200 lato client. Ogni ID compare una volta
  nel log del server, con HTTP 200.
- **Cosa verifica.** Il controllo conferma che gli stessi ID permettono di
  riconciliare i due log HTTP a livello di richiesta.
- **File.** `results/verifica_log_http/setup.txt`, `client_requests.csv` e
  `server.log` nella stessa cartella. Il CSV e il log conservano gli ID
  originali delle richieste. I dati grezzi sono locali a `results/`, che Git
  ignora.

### Controllo prima del primo run sperimentale

- **20 novembre 2026.** Docker mostra gli stessi container di
  `verifica_log_http` in esecuzione. La topologia comprende 3 directory
  authority, 5 relay, 3 exit relay, 1 client Tor e 1 server HTTP.
- **Esito.** Una richiesta attraverso SOCKS5 su `127.0.0.1:9050` al target
  `172.30.0.10:80` ha restituito la pagina «Private clearnet target».

### Preparazione di `primo_run_sperimentale`

- **20 novembre 2026.** Salvato l'elenco dei container attivi in
  `results/primo_run_sperimentale/setup.txt`. I nomi coincidono con quelli di
  `verifica_log_http`.
- **Stato.** La cartella del run contiene l'inventario Docker. L'invio
  delle richieste e la raccolta sperimentale sono da svolgere.
- **Ripristino dopo il riavvio della VM.** Riavviati i container Tor
  esistenti e il server HTTP. Il test di connettività via SOCKS5 ha
  restituito di nuovo la pagina «Private clearnet target».
- **Contatori Docker facoltativi.** `contatori_prima.txt` contiene i
  byte cumulativi ricevuti e inviati dai container al momento della
  lettura. Per riportare il volume complessivo di un run, salvare una
  lettura subito prima e una subito dopo, quindi calcolare la differenza
  per container. Il volume include il traffico di fondo. Ha senso citarlo
  nel report come misura accessoria del carico di rete. Per la correlazione
  temporale servono osservazioni con un riferimento temporale.

### Decisioni di lavoro

- La verifica HTTP preliminare è conclusa. Lo script dedicato a ripeterla
  è stato rimosso.
- L'eventuale confronto con onion resta da concordare con il gruppo. Per
  il confronto va indicata la tabella onion di riferimento e vanno annotate
  le differenze tra le condizioni delle prove.

Per ogni prossimo run, aggiungere qui data e ID, configurazione effettiva,
esito, anomalie e percorso dei file locali. Registrare anche le decisioni di
sviluppo che incidono sulle misure o sulla loro riproducibilità.

## Riferimenti

- [Guida clearnet](../../docs/GETTING_STARTED.md#prova-clearnet-interna)
  per l'avvio del target e la verifica HTTP.
- [Report onion sintetico](../onion-correlation/ONION_CORRELATION_MINIMAL.md)
  per un esempio di presentazione di risultati, controllo e limiti.
- [Report onion completo](../onion-correlation/ONION_CORRELATION_REPORT.md),
  sezioni 4 e 6, per il dettaglio delle misure e dei limiti.
