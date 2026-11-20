# Esperimento clearnet su una rete Tor privata

Bozza del contributo clearnet. Per il report di gruppo, fare riferimento
alle [indicazioni del docente](../../docs/PROJECT_PLAN.md#consegna-e-report-finale).

## Obiettivo e ambiente

L'esperimento studia la compatibilità temporale tra le osservazioni sul
guard del client e sull'exit relay durante richieste a un server HTTP
interno. Il client entra nella rete Tor privata tramite SOCKS5. L'exit
raggiunge il server sulla rete Docker dedicata al target. La procedura di
avvio è descritta nella [guida clearnet](../../docs/GETTING_STARTED.md#prova-clearnet-interna).

## Raccolta dati e verifica preliminare

Ogni richiesta al target HTTP contiene un identificatore univoco nell'URL.
Il CSV del client registra inizio, fine ed esito di ciascun tentativo.
L'identificatore permette di verificare l'arrivo della richiesta nel log
di accesso del server.

Nella prova manuale `verifica_log_http` sono state inviate cinque richieste
sequenziali. Tutte e cinque hanno ricevuto HTTP 200 lato client e ciascun
ID è comparso una volta nel log del server, con HTTP 200. La prova verifica
la riconciliazione dei due log a livello di richiesta.

Configurazione, CSV del client e log del server sono registrati nella
[nota sulla verifica preliminare](README.md#verifica-preliminare-della-raccolta-dati).

## Misure e risultati

Da completare dopo le misure documentando configurazione, dataset, parametri
effettivi e risultati per run. Per le metriche di copertura, indicare
numeratori e denominatori secondo le
[convenzioni condivise](../../docs/METHODOLOGIES.md).

## Limiti

I container del laboratorio condividono macchina e clock. I risultati si
riferiscono alla configurazione provata. I log HTTP attestano gli esiti a
livello di richiesta e offrono un riferimento per interpretare le misure.
