# Scenario onion condiviso

Questo è lo scenario di base per provare un onion service nella rete Tor
privata. È comune a tutto il gruppo: non contiene packet capture, matching o
risultati individuali.

Per creare il servizio, avviare il server HTTP e inviare la richiesta tramite
SOCKS5, seguire la sezione [Prova onion service](../../docs/GETTING_STARTED.md#prova-onion-service).

Prima di iniziare un esperimento individuale, verificare che:

- il client raggiunga `Bootstrapped 100%`;
- la richiesta verso l'indirizzo `.onion` restituisca la pagina HTTP prevista;
- il nome del client, del servizio e dei relay coinvolti sia annotato nei
  metadati della successiva esecuzione sperimentale.

Per il progetto, l'osservazione utile al matching è sui relay selezionati
`GuardClient` e `GuardService`. I log del container onion service sono
ground truth e non dati disponibili all'attaccante.
