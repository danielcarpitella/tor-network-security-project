# Correlazione entry–exit

Spazio per l'esperimento di correlazione nello scenario clearnet interno.

## Obiettivo

Valutare se osservazioni sul `GuardClient` e sull'`ExitRelay` permettono di
associare correttamente i flussi della stessa richiesta HTTP.

## Base condivisa

Usare gli script in [`shared-base/clearnet/`](../../shared-base/clearnet/) e
la procedura di avvio della rete in
[`docs/GETTING_STARTED.md`](../../docs/GETTING_STARTED.md). Questa cartella
non deve modificare configurazioni Tor o il target comune.

## Confini dei dati

- Osservazioni dell'attaccante: catture sui relay `GuardClient` ed
  `ExitRelay` scelti.
- Ground truth: identificatore della sessione, richiesta generata e log del
  server HTTP, mantenuti separati dalle osservazioni usate dal matching.

## Metriche previste

Usare almeno precision, recall, falsi positivi e percentuale di matching
corretto; registrare anche timing, burst, byte e durata osservati.

Quando servirà, aggiungere qui `scripts/`, `captures/`, `analysis/` e
`results/`. I risultati derivati non vengono versionati.
