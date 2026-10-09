# Market Radar

Market Radar cerca anomalie utili per leggere l'**EURO STOXX 50** senza riempire Telegram di rumore.

## Struttura attuale

### 1. Gate Asia
Alle **08:15 Europe/Rome** controlla solo il **Nikkei 225**.

Soglie:
- LONG: +1%
- LONG FORTE: +1,5%
- LONG ESTREMO: +2%
- SHORT: -1%
- SHORT FORTE: -1,5%
- SHORT ESTREMO: -2%

Il Nikkei resta un modulo indipendente. Il suo valore viene comunque salvato come contesto per l'apprendimento statistico.

### 2. Crolli azionari large cap
Il Radar cerca in USA ed Europa società:
- large cap (circa almeno 10 mld di market cap);
- liquide;
- con ribasso giornaliero verificato di almeno **-7%**.

Luna cerca candidati tramite notizie finanziarie alle 09:05 e ripete una ricerca indipendente alle 10:30 (Europe/Rome), per intercettare notizie emerse più tardi. La seconda ricerca evita di inviare nuovamente gli avvisi azionari già trasmessi nella stessa giornata. Yahoo Finance verifica prezzo e market cap dei titoli individuati. Non è ancora presente una scansione sistematica dei prezzi di tutte le large cap: se Luna non scopre un titolo, la verifica numerica non lo esamina. L'analisi successiva cerca la causa concreta del crollo e invia su Telegram solo i casi che meritano studio.

### 3. Blocco macro EuroStoxx
Il punteggio iniziale usa solo questi sensori:

- **Bond 60%**: Treasury USA 10Y, Bund 10Y, OAT Francia 10Y, BTP 10Y, Gilt UK 10Y
- **VSTOXX 25%**
- **WTI 15%**

Un rialzo dei rendimenti, del VSTOXX e del WTI aumenta la pressione SHORT; un rientro coordinato aumenta il lato LONG.

Il Radar calcola:
- SHORT SCORE 0-100
- LONG SCORE 0-100

Per default manda un alert macro solo da **60/100** in su. Il messaggio bond esistente resta attivo.

### Alert bond: tre soglie, nessun rumore

Nelle scansioni macro (**09:05, 15:35, 21:45 Europe/Rome**) i rendimenti decennali di **Bund, BTP e OAT** sono confrontati con la chiusura della seduta precedente:

- 🟡 **±10 punti base:** preallarme
- 🟠 **±15 punti base:** anomalia
- 🔴 **±25 punti base:** movimento forte

Il messaggio Telegram parte solo al primo superamento di una soglia per titolo e direzione nella stessa giornata; un passaggio a un livello più grave genera un nuovo alert. Movimenti non verificabili o con data diversa da quella odierna non producono allarmi. Treasury USA e Gilt UK restano nel macro-score già presente.

### Notizie economiche e geopolitiche (ANSA)

A **ogni scansione dei giorni feriali**, inclusi gli slot **08:15** e **10:30**, il Radar legge gratuitamente i feed RSS **ANSA Economia** e **ANSA Mondo**. Sabato e domenica esegue **solo il controllo notizie** alle **09:05**, **15:35** e **21:45 Europe/Rome**: niente Nikkei, azioni, bond, VSTOXX, petrolio o chiamate AI. Gli orari si adeguano al cambio fra ora legale e solare e prevedono recuperi in caso di ritardi GitHub. Usa una selezione prudente di titoli relativi a shock economici e tensioni geopolitiche, senza ulteriori chiamate alle API AI. Notifica esclusivamente articoli recenti (ultime 14 ore), con titolo, fonte, orario e link, cercando di evitare duplicati di uno stesso evento.

Per non sovraccaricare Telegram, al massimo **1 notizia per scansione** e **2 al giorno**. La classificazione è una prima selezione automatica basata sui titoli: eventuali cause e conseguenze vanno verificate. Se un feed è irraggiungibile, il problema viene registrato nei log e le altre scansioni proseguono. Il monitoraggio delle notizie non modifica i pesi o i segnali macro esistenti.

### Alert autonomi WTI e VSTOXX

A ogni scansione macro programmata (**09:05, 15:35, 21:45 Europe/Rome**) il Radar controlla inoltre:
- **WTI**: variazione odierna **≥ +2%** oppure **≤ -2%**;
- **VSTOXX**: variazione odierna **≥ +5%** oppure **≤ -5%**.

L'allerta **non dipende dal macro-score** e arriva su Telegram con valore rilevato, variazione, possibile causa, impatto e fonti se disponibili. Un solo report per indicatore e direzione nella stessa giornata; un'eventuale inversione significativa può produrre un secondo report. Gli alert partono solo durante le scansioni (non c'è monitoraggio continuo), quindi movimenti tra una scansione e l'altra possono sfuggire. I pesi macro e l'apprendimento rimangono invariati.

L'unico mercato usato per misurare l'esito e l'**EURO STOXX 50**.

## Apprendimento

Ogni scansione macro registra:
- movimenti dei 5 bond;
- VSTOXX;
- WTI;
- Nikkei come contesto;
- numero di crolli large cap trovati/verificati;
- livello e variazione dell'EuroStoxx;
- punteggio LONG/SHORT.

Alle scansioni successive il Radar ricostruisce automaticamente l'esito dell'EuroStoxx:
- circa **+30 minuti**;
- circa **+2 ore**;
- **chiusura europea**;
- **chiusura della seduta successiva**.

Lo storico produce statistiche per fascia di score e per sensore. Dopo almeno 20 campioni per sensore può proporre di **aumentare, mantenere o ridurre** un peso.

**Importante:** il Radar non modifica automaticamente pesi, soglie o codice e non esegue ordini.

## Orari

Slot principali Europe/Rome:
- **08:15** Nikkei
- **09:05** Radar Europa
- **10:30** seconda ricerca **solo azioni**, senza ripetere controlli macro/bond; recupero 10:55
- **15:35** Radar USA/pomeriggio
- **21:45** controllo finale

Ogni slot ha un tentativo di recupero +25 minuti nel workflow GitHub (per le 09:05 il recupero in ora solare avviene alle 09:31, anziché alle 09:30, per evitare una collisione UTC).

## Segreti GitHub necessari

In Settings -> Secrets and variables -> Actions:

- OPENAI_API_KEY
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

L'uso delle API AI e della ricerca web e a consumo e non e incluso in ChatGPT Plus.

## Variabili opzionali

- MACRO_ALERT_THRESHOLD (default 60)
- LEARNING_MAX_OBSERVATIONS (default 250)
- LARGE_CAP_MIN_MARKET_CAP (default 10000000000)
- MIN_EQUITY_DROP_PCT (default -7.0)

## File principali

- main.py: orari e avvio
- radar.py: ricerca, verifica, alert e integrazione dei moduli
- event_alerts.py: soglie dei bond e notizie ANSA RSS
- macro_learning.py: score macro, memoria degli eventi, verifica degli esiti e statistiche
- .github/workflows/market-radar.yml: esecuzione automatica feriale
- .github/workflows/weekend-news.yml: controllo RSS sabato e domenica
