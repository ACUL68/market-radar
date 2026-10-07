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

Luna e Gemini lavorano come sentinelle indipendenti; Yahoo Finance verifica prezzo e market cap; l'analisi successiva cerca la causa concreta del crollo e manda su Telegram solo i casi che meritano studio.

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
- **15:35** Radar USA/pomeriggio
- **21:45** controllo finale

Ogni slot ha un tentativo di recupero +25 minuti nel workflow GitHub.

## Segreti GitHub necessari

In Settings -> Secrets and variables -> Actions:

- OPENAI_API_KEY
- GEMINI_API_KEY
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
- macro_learning.py: score macro, memoria degli eventi, verifica degli esiti e statistiche
- .github/workflows/market-radar.yml: esecuzione automatica
