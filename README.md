# Market Radar

Market Radar e un agente automatico che cerca **anomalie interessanti** su grandi azioni USA/Europa e usa i principali bond governativi come contesto macro.

## Logica V1

Tre scansioni al giorno (ora italiana): **10:30, 16:00, 21:15**.

1. L'AI con ricerca web identifica la **fase strutturale** del mercato USA ed europeo (bull / correction / bear / uncertain).
2. Cerca large cap in forte ribasso o molto deboli rispetto a indice/settore.
3. Controlla il contesto dei benchmark: Treasury 10Y, Bund 10Y, BTP 10Y, OAT 10Y e Gilt 10Y.
4. Il programma verifica la quotazione con Yahoo Finance e confronta il titolo con l'indice di riferimento.
5. Solo i candidati rimasti vengono sottoposti a un'analisi AI approfondita con ricerca web: causa, settore, guidance, utili, debito, rischi seri, tassi/bond e fonti discordanti.
6. Se il caso merita studio, Telegram riceve un **grafico a 6 mesi** e un report sintetico. Non e una raccomandazione di acquisto/vendita.

## Segreti GitHub necessari

In `Settings -> Secrets and variables -> Actions` aggiungi:

- `OPENAI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

L'uso della OpenAI API e della ricerca web API e a consumo e non e incluso in ChatGPT Plus.

## Test manuale

Da GitHub: `Actions -> Market Radar -> Run workflow`.
Il trigger manuale forza una scansione anche fuori dagli orari previsti.

## File principali

- `main.py`: orari e avvio.
- `radar.py`: ricerca, verifica quotazioni, analisi, grafico e Telegram.
- `.github/workflows/market-radar.yml`: esecuzione automatica.

## Nota

La V1 privilegia semplicita e verificabilita. Dopo il test reale possiamo tarare soglie, numero di candidati e formato degli alert in base ai falsi positivi/negativi osservati.
