from __future__ import annotations

from datetime import datetime
from typing import Any

from radar import (
    ANALYSIS_MODEL,
    ROME,
    _ask_web,
    _load_state,
    _save_state,
    mark_alert,
    market_snapshot,
    send_message,
    should_suppress,
)

WTI_TICKER = "CL=F"
WTI_ALERT_PCT = 3.0


def _analyze_wti(change_pct: float, last_price: float) -> dict[str, Any]:
    today = datetime.now(ROME).strftime("%Y-%m-%d")
    prompt = f"""
Sei MARKET RADAR. Oggi è {today}, ora Europe/Rome.
Il future WTI crude oil (CL=F) sta facendo {change_pct:+.2f}% rispetto alla chiusura precedente,
con ultimo prezzo verificato {last_price:.2f} USD.

Usa ricerca web e fonti finanziarie affidabili pubblicate o aggiornate OGGI.
Devi capire perche il WTI si sta muovendo di almeno il 3% gia nella mattinata europea.

Controlla in particolare OPEC/OPEC+, offerta, scorte e produzione USA,
geopolitica e rischi sulle forniture, Cina e domanda globale, dollaro USA,
dati macro e ogni evento specifico di oggi.

Non inventare una causa. Se le fonti di oggi non danno una spiegazione chiara, dichiaralo.

Restituisci SOLO JSON valido:
{{
  "cause": "...",
  "driver": "demand|supply|geopolitics|macro|currency|mixed|unclear",
  "market_reading": "massimo 500 caratteri, italiano semplice: cosa puo significare per Europa e mercati oggi",
  "confidence": 0,
  "sources": ["https://..."]
}}
"""
    return _ask_web(ANALYSIS_MODEL, prompt)


def run_morning_wti() -> None:
    snap = market_snapshot(WTI_TICKER)
    if not snap:
        print("WTI gate: dati insufficienti")
        return

    move = snap.change_pct
    if abs(move) < WTI_ALERT_PCT:
        print(f"WTI gate: nessun alert ({move:+.2f}%)")
        return

    direction = "UP" if move > 0 else "DOWN"
    key = f"WTI_{direction}_3"
    state = _load_state()

    if should_suppress(key, move, state):
        print(f"WTI gate duplicato soppresso: {move:+.2f}%")
        return

    analysis = _analyze_wti(move, snap.last)
    sources = analysis.get("sources") or []
    source_text = "\n".join(f"- {url}" for url in sources[:4])

    body = (
        "MARKET RADAR - WTI MATTINA\n\n"
        f"WTI: {move:+.2f}%\n"
        f"Prezzo: {snap.last:.2f} USD\n"
        f"Soglia: +/-{WTI_ALERT_PCT:.0f}%\n\n"
        f"Perche: {analysis.get('cause', 'causa non chiara')}\n"
        f"Driver: {analysis.get('driver', 'unclear')}\n\n"
        f"Lettura mercato: {analysis.get('market_reading', '')}\n"
        f"Confidenza: {analysis.get('confidence', 'n/d')}%"
    )
    if source_text:
        body += f"\n\nFonti:\n{source_text}"

    send_message(body)
    mark_alert(key, move, state)
    _save_state(state)
