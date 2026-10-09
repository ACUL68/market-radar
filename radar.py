from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import requests
import yfinance as yf
from openai import OpenAI

from sensor_alerts import pending_sensor_alerts, mark_sensor_alert
from event_alerts import (
    pending_bond_alerts, mark_bond_alerts, pending_news_alerts,
    format_news_alert, mark_news_alert,
)

from macro_learning import (
    EUROSTOXX_TICKER,
    MACRO_ALERT_THRESHOLD,
    calculate_macro_score,
    format_macro_alert,
    mark_macro_alert,
    record_observation,
    reliability_for_score,
    set_verified_crash_count,
    should_emit_macro_alert,
    update_learning_outcomes,
    update_learning_stats,
    verified_wti_context,
)

DISCOVERY_MODEL = os.getenv("OPENAI_DISCOVERY_MODEL", "gpt-6-luna")
ANALYSIS_MODEL = os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-6-sol")
STATE_FILE = Path(os.getenv("RADAR_STATE_FILE", ".radar_state.json"))
LARGE_CAP_MIN_MARKET_CAP = float(os.getenv("LARGE_CAP_MIN_MARKET_CAP", "10000000000"))
MIN_EQUITY_DROP_PCT = float(os.getenv("MIN_EQUITY_DROP_PCT", "-7.0"))

INDEX_BY_REGION = {
    "USA": "^NDX",
    "US": "^NDX",
    "EUROPE": "^STOXX50E",
    "EU": "^STOXX50E",
}


ASIA_WATCH = {
    "Nikkei 225": "^N225",
}

ROME = ZoneInfo("Europe/Rome")


@dataclass
class MarketSnapshot:
    ticker: str
    last: float
    prev_close: float
    change_pct: float
    six_month_change_pct: float
    six_month_high: float
    six_month_low: float
    history: Any


def _client() -> OpenAI:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY non configurata")
    return OpenAI()


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _ask_web(model: str, prompt: str) -> dict[str, Any]:
    response = _client().responses.create(
        model=model,
        tools=[{"type": "web_search"}],
        input=prompt,
    )
    return _extract_json(response.output_text)

def _discover_context(today: str) -> dict[str, Any]:
    prompt = f"""
Sei MARKET RADAR. Oggi è {today}, ora Europe/Rome.
Usa ricerca web e fonti finanziarie affidabili pubblicate o aggiornate OGGI.
Non dare consigli di acquisto o vendita.

Devi fare QUATTRO cose:
1) Stabilire la fase strutturale del mercato USA e del mercato europeo: bull, correction, bear oppure uncertain.
2) Controllare Treasury USA 10Y, Bund 10Y, BTP 10Y, OAT Francia 10Y e Gilt UK 10Y.
   Per ciascuno restituisci il movimento di rendimento di OGGI in punti base come numero firmato:
   positivo = rendimento in salita, negativo = rendimento in discesa. Calcola il movimento rispetto alla CHIUSURA della seduta precedente dello stesso titolo, non rispetto al prezzo del bond.\n   Verifica data e fonte del rendimento: se il movimento giornaliero non e verificabile usa null.
3) Controllare VSTOXX spot: livello attuale e variazione percentuale di OGGI come numero firmato.
4) Controllare WTI/front-month crude oil: livello attuale e variazione percentuale di OGGI come numero firmato.

Regole:
- segnala come important=true solo movimenti davvero rilevanti per EuroStoxx/azionario;
- non inventare numeri: se non sono verificabili usa null e spiega il limite;
- per bond/VSTOXX/WTI usa fonti di oggi e preferisci fonti ufficiali o finanziarie primarie;
- il programma usera questi numeri solo come sensori statistici e verifichera poi l'esito sull'EuroStoxx 50.

Restituisci SOLO JSON valido:
{{
  "market_regime": {{
    "usa": {{"phase": "bull|correction|bear|uncertain", "reason": "...", "sources": ["https://..."]}},
    "europe": {{"phase": "bull|correction|bear|uncertain", "reason": "...", "sources": ["https://..."]}}
  }},
  "bond_context": [
    {{
      "benchmark": "US Treasury 10Y",
      "observation_date": "YYYY-MM-DD",
      "current_yield_pct": 5.28,
      "move_bp": 7.4,
      "move": "rendimento +7,4 pb oggi",
      "why": "...",
      "important": true,
      "sources": ["https://..."]
    }}
  ],
  "vstoxx_context": {{
    "level": 24.6,
    "change_pct": 12.4,
    "why": "...",
    "important": true,
    "sources": ["https://..."]
  }},
  "wti_context": {{
    "level": 91.2,
    "change_pct": 4.1,
    "why": "...",
    "important": true,
    "sources": ["https://..."]
  }}
}}
"""
    return _ask_web(DISCOVERY_MODEL, prompt)

def _discover_equity_candidates_luna(today: str) -> list[dict[str, Any]]:
    searches = [
        (
            "USA",
            "Cerca sul web SOLO tra società USA large cap, con capitalizzazione almeno circa 10 miliardi e buona liquidità. "
            "Individua TUTTI i titoli importanti che OGGI stanno crollando o hanno un ribasso chiaramente anomalo. "
            "Controlla in particolare S&P 500, Nasdaq 100 e le principali large cap USA. "
            "Includi anche crolli legati ad acquisizioni/M&A, nuovo debito o diluizione, trimestrali, profit warning, "
            "tagli di guidance, downgrade, problemi regolatori/legali e altre notizie societarie specifiche."
        ),
        (
            "EUROPA",
            "Cerca sul web SOLO tra società europee large cap, con capitalizzazione almeno circa 10 miliardi e buona liquidità. "
            "Individua TUTTI i titoli importanti che OGGI stanno crollando o hanno un ribasso chiaramente anomalo. "
            "Controlla i principali mercati e indici europei, inclusi DAX, CAC 40, FTSE 100, FTSE MIB, AEX, IBEX e SMI. "
            "Includi anche crolli legati ad acquisizioni/M&A, nuovo debito o diluizione, trimestrali, profit warning, "
            "tagli di guidance, downgrade, problemi regolatori/legali e altre notizie societarie specifiche."
        ),
    ]

    def _search_one(label: str, focus: str) -> tuple[str, dict[str, Any]]:
        prompt = f"""
Sei il motore di scoperta azionaria di MARKET RADAR. Oggi è {today}, ora Europe/Rome.
Usa ricerca web e fonti finanziarie affidabili PUBBLICATE O AGGIORNATE OGGI.

{focus}

Regole:
- NON scegliere una shortlist arbitraria e NON fermarti ai primi quattro risultati.
- Riporta tutti i casi rilevanti che trovi in questa ricerca.
- Considera SOLO large cap: capitalizzazione indicativamente almeno 10 miliardi; escludi micro-cap, mid-cap piccole, penny stock e titoli illiquidi.
- Cerca ribassi di OGGI pari o superiori al 7% circa; il programma verificherà poi numericamente prezzo e market cap.
- Il movimento deve essere di OGGI. Una notizia vecchia non basta.
- Non serve decidere se il titolo sia da comprare: questa fase deve soltanto TROVARE l'anomalia.
- Usa, quando possibile, ticker compatibili con Yahoo Finance.

Restituisci SOLO JSON valido:
{{
  "equity_candidates": [
    {{
      "company": "...",
      "ticker": "...",
      "region": "USA|EUROPE",
      "sector": "...",
      "index_reference": "Nasdaq 100|S&P 500|Euro Stoxx 50|altro",
      "reported_change_pct": -8.4,
      "why_candidate": "evento/anomalia osservata oggi"
    }}
  ]
}}
"""
        return label, _ask_web(DISCOVERY_MODEL, prompt)

    merged: dict[str, dict[str, Any]] = {}

    # Due ricerche ampie e indipendenti, una USA e una Europa.
    # Le eseguiamo in sequenza per non saturare il limite token/minuto del modello.
    for label, focus in searches:
        result: dict[str, Any] | None = None
        for attempt in range(2):
            try:
                _, result = _search_one(label, focus)
                break
            except Exception as exc:
                message = str(exc)
                if attempt == 0 and ("429" in message or "rate limit" in message.lower()):
                    print(f"Luna {label}: rate limit, nuovo tentativo tra 10 secondi")
                    time.sleep(10)
                    continue
                print(f"Luna {label} non disponibile: {exc}")

        if not result:
            continue

        found = result.get("equity_candidates") or []
        print(f"Luna {label}: {len(found)} candidati")

        for candidate in found:
            ticker = str(candidate.get("ticker", "")).strip()
            if not ticker:
                continue
            key = ticker.upper()

            if key not in merged:
                merged[key] = candidate
                continue

            old_reason = str(merged[key].get("why_candidate", "")).strip()
            new_reason = str(candidate.get("why_candidate", "")).strip()
            if new_reason and new_reason not in old_reason:
                merged[key]["why_candidate"] = (
                    f"{old_reason} | {new_reason}" if old_reason else new_reason
                )

    candidates = list(merged.values())

    def _reported_drop(item: dict[str, Any]) -> float:
        try:
            return float(item.get("reported_change_pct", 0.0))
        except (TypeError, ValueError):
            return 0.0

    candidates.sort(key=_reported_drop)
    print(f"Discovery Luna totale: {len(candidates)} candidati unici")
    if candidates:
        print("Ticker scoperti: " + ", ".join(str(c.get("ticker", "?")) for c in candidates))

    return candidates


def discover_market() -> dict[str, Any]:
    today = datetime.now(ROME).strftime("%Y-%m-%d")
    context = _discover_context(today)
    context["equity_candidates"] = _discover_equity_candidates_luna(today)
    return context

def market_snapshot(ticker: str) -> MarketSnapshot | None:
    try:
        hist = yf.Ticker(ticker).history(period="6mo", interval="1d", auto_adjust=True)
        if hist is None or len(hist) < 3 or "Close" not in hist:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 3:
            return None
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2])
        first = float(closes.iloc[0])
        return MarketSnapshot(
            ticker=ticker,
            last=last,
            prev_close=prev,
            change_pct=(last / prev - 1.0) * 100.0,
            six_month_change_pct=(last / first - 1.0) * 100.0,
            six_month_high=float(closes.max()),
            six_month_low=float(closes.min()),
            history=hist,
        )
    except Exception as exc:
        print(f"Market data error {ticker}: {exc}")
        return None


def market_cap_yahoo(ticker: str) -> tuple[float | None, str | None]:
    """Restituisce market cap e valuta da Yahoo Finance. Se il dato non è verificabile, fallisce chiuso."""
    try:
        tk = yf.Ticker(ticker)
        cap: float | None = None
        currency: str | None = None

        try:
            fast = tk.fast_info
            raw_cap = getattr(fast, "market_cap", None)
            if raw_cap is None:
                try:
                    raw_cap = fast["market_cap"]
                except Exception:
                    raw_cap = None
            raw_currency = getattr(fast, "currency", None)
            if raw_currency is None:
                try:
                    raw_currency = fast["currency"]
                except Exception:
                    raw_currency = None
            if raw_cap is not None:
                cap = float(raw_cap)
            if raw_currency:
                currency = str(raw_currency)
        except Exception:
            pass

        if cap is None:
            try:
                info = tk.info or {}
                raw_cap = info.get("marketCap")
                if raw_cap is not None:
                    cap = float(raw_cap)
                currency = currency or info.get("currency")
            except Exception:
                pass

        return cap, currency
    except Exception as exc:
        print(f"Market cap error {ticker}: {exc}")
        return None, None


def has_recent_market_data(snapshot: MarketSnapshot, max_age_days: int = 7) -> bool:
    """Scarta ticker non più attivi/delistati usando la freschezza dell'ultima barra Yahoo."""
    try:
        last_idx = snapshot.history.index[-1]
        if hasattr(last_idx, "to_pydatetime"):
            last_dt = last_idx.to_pydatetime()
        else:
            last_dt = last_idx
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=ROME)
        else:
            last_dt = last_dt.astimezone(ROME)
        age_days = (datetime.now(ROME).date() - last_dt.date()).days
        return age_days <= max_age_days
    except Exception:
        return False


def index_for_candidate(candidate: dict[str, Any]) -> str:
    ref = str(candidate.get("index_reference", "")).upper()
    if "S&P" in ref or "SP500" in ref or "S&P 500" in ref:
        return "^GSPC"
    if "NASDAQ" in ref:
        return "^NDX"
    if "EURO" in ref or "STOXX" in ref:
        return "^STOXX50E"
    return INDEX_BY_REGION.get(str(candidate.get("region", "")).upper(), "^GSPC")


def analyze_candidate(candidate: dict[str, Any], snapshot: MarketSnapshot, index_snapshot: MarketSnapshot | None,
                      regime: dict[str, Any], bond_context: list[dict[str, Any]]) -> dict[str, Any]:
    index_line = "non disponibile"
    if index_snapshot:
        index_line = f"{index_snapshot.ticker}: {index_snapshot.change_pct:+.2f}% oggi"

    prompt = f"""
Sei il secondo livello di MARKET RADAR: un analista che deve decidere se un movimento merita di essere STUDIATO, non se comprare o vendere.
Usa la ricerca web, fai tutte le verifiche successive che ritieni utili e incrocia più fonti.
Per spiegare il movimento live usa come fonti principali SOLO articoli/pubblicazioni di OGGI, secondo la data Europe/Rome.
Non attribuire il ribasso di oggi a una notizia vecchia solo perché sembra plausibile.
Le fonti dei giorni precedenti possono servire soltanto come contesto storico secondario.
Se non esiste una fonte di oggi che colleghi chiaramente il fatto al movimento, dichiaralo e imposta interesting_to_study=false.
Se le fonti non concordano, dichiaralo.

CANDIDATO
Società: {candidate.get('company')}
Ticker: {candidate.get('ticker')}
Regione: {candidate.get('region')}
Settore: {candidate.get('sector')}
Movimento verificato dal programma: {snapshot.change_pct:+.2f}%
Prezzo verificato: {snapshot.last:.4f}
Performance 6 mesi: {snapshot.six_month_change_pct:+.2f}%
Massimo 6 mesi: {snapshot.six_month_high:.4f}
Minimo 6 mesi: {snapshot.six_month_low:.4f}
Indice di riferimento verificato: {index_line}
Fase strutturale mercato: {json.dumps(regime, ensure_ascii=False)}
Contesto bond benchmark: {json.dumps(bond_context, ensure_ascii=False)}
Prima segnalazione: {candidate.get('why_candidate')}

Devi capire:
- causa concreta del ribasso;
- se è specifica della società oppure soprattutto di settore/indice;
- se il ribasso è sproporzionato rispetto al contesto;
- se sono emersi elementi seri: insolvenza/liquidità, aumento di capitale, frodi o problemi contabili, forti rischi legali/regolatori, downgrade del credito, profit warning o deterioramento strutturale;
- se guidance/utili/margini sono cambiati e in che misura;
- se il movimento dei tassi/bond può spiegare una parte rilevante del ribasso;
- quali fatti sono confermati e cosa resta incerto.

Restituisci SOLO JSON valido:
{{
  "interesting_to_study": true,
  "cause": "...",
  "context": "company_specific|sector|market|mixed|unclear",
  "serious_risk_flags": ["..."],
  "why_selected": "...",
  "analysis": "massimo 750 caratteri, italiano semplice e concreto",
  "confidence": 0,
  "sources": ["https://..."]
}}

interesting_to_study=true significa soltanto che il caso merita approfondimento umano. Non è una raccomandazione finanziaria.
"""
    return _ask_web(ANALYSIS_MODEL, prompt)


def make_chart(snapshot: MarketSnapshot, company: str) -> Path:
    out = Path("chart.png")
    closes = snapshot.history["Close"].dropna()
    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.plot(closes.index, closes.values, linewidth=1.8)
    ax.scatter([closes.index[-1]], [closes.iloc[-1]], s=32)
    ax.set_title(f"{company} ({snapshot.ticker}) — ultimi 6 mesi")
    ax.set_ylabel("Prezzo")
    ax.grid(True, alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def _telegram_creds() -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID non configurati")
    return token, chat_id


def send_message(text: str) -> None:
    token, chat_id = _telegram_creds()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, data={"chat_id": chat_id, "text": text[:4000]}, timeout=30)
    r.raise_for_status()


def send_chart(path: Path, caption: str) -> None:
    token, chat_id = _telegram_creds()
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    with path.open("rb") as f:
        r = requests.post(url, data={"chat_id": chat_id, "caption": caption[:900]}, files={"photo": f}, timeout=45)
    r.raise_for_status()


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"alerts": {}}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def should_suppress(ticker: str, change_pct: float, state: dict[str, Any]) -> bool:
    old = state.get("alerts", {}).get(ticker)
    if not old:
        return False
    try:
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(old["time"])).total_seconds() / 3600
        delta = abs(change_pct - float(old["change_pct"]))
        return age_h < 10 and delta < 3.0
    except Exception:
        return False


def mark_alert(ticker: str, change_pct: float, state: dict[str, Any]) -> None:
    state.setdefault("alerts", {})[ticker] = {
        "time": datetime.now(timezone.utc).isoformat(),
        "change_pct": change_pct,
    }


def format_alert(candidate: dict[str, Any], snap: MarketSnapshot, idx: MarketSnapshot | None,
                 analysis: dict[str, Any], regime: dict[str, Any]) -> str:
    region = str(candidate.get("region", "USA")).lower()
    regime_info = regime.get("europe" if "euro" in region else "usa", {})
    idx_text = f"{idx.change_pct:+.2f}%" if idx else "n/d"
    flags = analysis.get("serious_risk_flags") or []
    flag_text = "; ".join(flags) if flags else "nessun elemento grave emerso nelle fonti controllate"
    sources = analysis.get("sources") or []
    source_text = "\n".join(f"- {u}" for u in sources[:4])
    body = (
        "🚨 MARKET RADAR\n\n"
        f"{candidate.get('company')} ({candidate.get('ticker')}) — {snap.change_pct:+.2f}%\n"
        f"Fase mercato: {str(regime_info.get('phase', 'uncertain')).upper()}\n"
        f"Indice di riferimento oggi: {idx_text}\n"
        f"6 mesi: {snap.six_month_change_pct:+.2f}%\n\n"
        f"Causa rilevata: {analysis.get('cause', 'non chiara')}\n"
        f"Contesto: {analysis.get('context', 'unclear')}\n"
        f"Rischi seri: {flag_text}\n\n"
        f"Perché è nel radar: {analysis.get('why_selected', '')}\n\n"
        f"Lettura: {analysis.get('analysis', '')}\n"
        f"Confidenza analisi: {analysis.get('confidence', 'n/d')}%"
    )
    if source_text:
        body += f"\n\nFonti:\n{source_text}"
    return body.strip()


def _scan_news(state: dict[str, Any], *, strict: bool = False) -> None:
    """Controllo RSS leggero a ogni slot, senza chiamate AI aggiuntive."""
    try:
        for news in pending_news_alerts(state):
            send_message(format_news_alert(news))
            mark_news_alert(state, news)
            _save_state(state)
    except Exception as exc:
        if strict:
            raise  # Weekend: ritenta al cron di recupero se Telegram fallisce.
        print(f"News: controllo o invio non riuscito: {exc}")


def run_weekend_news() -> None:
    """Sabato/domenica: solo feed ANSA Economia e Mondo; nessun dato di mercato."""
    print("Weekend: controllo solo notizie economiche e geopolitiche.")
    _scan_news(_load_state(), strict=True)


def send_bond_context(bonds: list[dict[str, Any]], state: dict[str, Any]) -> None:
    """Tre soglie autonome sui decennali Bund, BTP e OAT; un solo messaggio per scansione."""
    pending = pending_bond_alerts(state, bonds)
    if not pending:
        return
    send_message("🟦 MARKET RADAR — BOND / TASSI\n\n" + "\n\n".join(line for _, _, line in pending))
    mark_bond_alerts(state, pending)
    _save_state(state)



def _analyze_asia_signal(changes: dict[str, float]) -> dict[str, Any]:
    prompt = f"""
Sei MARKET RADAR. Devi interpretare un forte movimento della seduta asiatica appena conclusa.
Non dare consigli di trading. Usa ricerca web e fonti finanziarie affidabili PUBBLICATE O AGGIORNATE OGGI, secondo la data Europe/Rome.
Non usare articoli dei giorni precedenti per spiegare il segnale asiatico di oggi, salvo puro contesto storico secondario.
Se non trovi una fonte di oggi che spieghi il movimento, indica scope="unclear" ed europe_us_risk="low".

Movimenti verificati:
{json.dumps(changes, ensure_ascii=False)}

Devi capire:
- causa principale del movimento;
- se è un problema locale (Giappone/Corea) oppure potenzialmente globale;
- se tassi USA, yen, dollaro, Cina, semiconduttori, geopolitica o dati macro stanno contribuendo;
- se il segnale può ragionevolmente aumentare il rischio di pressione su Europa e USA nella giornata,
  senza presentarlo come previsione certa.

Restituisci SOLO JSON valido:
{{
  "scope": "local|mixed|global|unclear",
  "cause": "...",
  "europe_us_risk": "low|medium|high",
  "why_it_matters": "massimo 450 caratteri, italiano semplice",
  "confidence": 0,
  "sources": ["https://..."]
}}
"""
    return _ask_web(ANALYSIS_MODEL, prompt)


def run_asia_early_warning() -> None:
    _scan_news(_load_state())
    snap = market_snapshot(ASIA_WATCH["Nikkei 225"])
    if not snap:
        print("Nikkei gate: dati insufficienti")
        return

    nikkei = snap.change_pct

    # Salva sempre il Nikkei come sensore di contesto per l'apprendimento.
    # Non entra ancora nei pesi del macro-score: resta un modulo indipendente.
    state = _load_state()
    state.setdefault("sensor_cache", {})["nikkei"] = {
        "time": datetime.now(ROME).isoformat(),
        "change_pct": nikkei,
        "price": snap.last,
    }
    _save_state(state)

    # Gate Nikkei mattutino.
    # Sei soglie logiche, con priorità al livello più alto raggiunto:
    # >= +1%, >= +1.5%, >= +2% e simmetricamente <= -1%, <= -1.5%, <= -2%.
    if nikkei >= 2.0:
        signal = "NIKKEI LONG ESTREMO >= +2%"
        key = "NIKKEI_LONG_2"
        level = 2.0
    elif nikkei >= 1.5:
        signal = "NIKKEI LONG FORTE >= +1.5%"
        key = "NIKKEI_LONG_1_5"
        level = 1.5
    elif nikkei >= 1.0:
        signal = "NIKKEI LONG >= +1%"
        key = "NIKKEI_LONG_1"
        level = 1.0
    elif nikkei <= -2.0:
        signal = "NIKKEI SHORT ESTREMO <= -2%"
        key = "NIKKEI_SHORT_2"
        level = -2.0
    elif nikkei <= -1.5:
        signal = "NIKKEI SHORT FORTE <= -1.5%"
        key = "NIKKEI_SHORT_1_5"
        level = -1.5
    elif nikkei <= -1.0:
        signal = "NIKKEI SHORT <= -1%"
        key = "NIKKEI_SHORT_1"
        level = -1.0
    else:
        print(f"Nikkei gate: nessun setup ({nikkei:+.2f}%)")
        return

    if should_suppress(key, nikkei, state):
        print(f"Nikkei gate duplicato soppresso: {signal}")
        return

    body = (
        "🟧 MARKET RADAR — GATE NIKKEI\n\n"
        f"{signal}\n"
        f"Nikkei 225: {nikkei:+.2f}%\n"
        f"Soglia attivata: {level:+.1f}%"
    )

    send_message(body)
    mark_alert(key, nikkei, state)
    _save_state(state)

def _was_alerted_today(ticker: str, state: dict[str, Any]) -> bool:
    """La seconda scansione non rimanda azioni già segnalate nella giornata italiana."""
    old = state.get("alerts", {}).get(ticker)
    if not old:
        return False
    try:
        sent_at = datetime.fromisoformat(str(old["time"]))
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        return sent_at.astimezone(ROME).date() == datetime.now(ROME).date()
    except (KeyError, TypeError, ValueError):
        return False


def _send_equity_alerts(
    candidates: list[dict[str, Any]],
    state: dict[str, Any],
    regime: dict[str, Any],
    bonds: list[dict[str, Any]],
    *,
    same_day_dedupe: bool = False,
) -> int:
    sent = 0

    for candidate in candidates:
        ticker = str(candidate.get("ticker", "")).strip()
        if not ticker:
            continue
        if same_day_dedupe and _was_alerted_today(ticker, state):
            print(f"Seconda ricerca: {ticker} già segnalato oggi, nessun duplicato")
            continue
        snap = market_snapshot(ticker)
        if not snap:
            print(f"Skip {ticker}: quotazione non verificabile")
            continue
        if not has_recent_market_data(snap):
            print(f"Skip {ticker}: ticker non attivo o dati Yahoo non recenti")
            continue

        market_cap, market_cap_currency = market_cap_yahoo(ticker)
        if market_cap is None:
            print(f"Skip {ticker}: market cap Yahoo non verificabile")
            continue
        if market_cap < LARGE_CAP_MIN_MARKET_CAP:
            print(
                f"Skip {ticker}: market cap {market_cap / 1_000_000_000:.2f} mld "
                f"{market_cap_currency or ''} < 10 mld"
            )
            continue

        # Filtro duro richiesto: solo large cap con crollo verificato di almeno il 7% oggi.
        if snap.change_pct > MIN_EQUITY_DROP_PCT:
            print(
                f"Skip {ticker}: ribasso verificato {snap.change_pct:+.2f}% "
                f"(serve <= {MIN_EQUITY_DROP_PCT:.2f}%)"
            )
            continue

        print(
            f"PASS {ticker}: {snap.change_pct:+.2f}% | "
            f"market cap {market_cap / 1_000_000_000:.2f} mld {market_cap_currency or ''}"
        )

        idx_ticker = index_for_candidate(candidate)
        idx = market_snapshot(idx_ticker)

        analysis = analyze_candidate(candidate, snap, idx, regime, bonds)
        if not analysis.get("interesting_to_study"):
            print(f"Scartato dopo analisi: {ticker}")
            continue
        if should_suppress(ticker, snap.change_pct, state):
            print(f"Alert duplicato soppresso: {ticker}")
            continue

        chart = make_chart(snap, str(candidate.get("company", ticker)))
        send_chart(chart, f"{candidate.get('company')} ({ticker}) — grafico 6 mesi")
        send_message(format_alert(candidate, snap, idx, analysis, regime))
        mark_alert(ticker, snap.change_pct, state)
        sent += 1

    return sent


def run_radar() -> None:
    # Prima aggiorniamo gli esiti dei segnali precedenti sull'EuroStoxx.
    # In questo modo il Radar costruisce memoria statistica senza nuovi cron dedicati.
    state = _load_state()
    _scan_news(state)
    updated_outcomes = update_learning_outcomes(state)
    stats = update_learning_stats(state)
    if updated_outcomes:
        print(f"Learning: aggiornati esiti per {updated_outcomes} osservazioni")
    print(f"Learning: campioni completati {stats.get('completed_samples', 0)}")
    _save_state(state)

    discovery = discover_market()
    regime = discovery.get("market_regime") or {}
    bonds = discovery.get("bond_context") or []
    candidates = discovery.get("equity_candidates") or []
    vstoxx = discovery.get("vstoxx_context") or {}
    wti = verified_wti_context(discovery.get("wti_context") or {})

    # Alert autonomi: WTI ±2%, VSTOXX ±5%. Indipendenti dal macro-score.
    # Salvataggio dopo ogni Telegram riuscito per evitare doppioni in caso di errori successivi.
    for sensor_key, report in pending_sensor_alerts(state, wti, vstoxx):
        send_message(report)
        mark_sensor_alert(state, sensor_key)
        _save_state(state)

    # Il punteggio usa solo i sensori concordati: Bond 60%, VSTOXX 25%, WTI 15%.
    macro_score = calculate_macro_score(bonds, vstoxx, wti)
    euro = market_snapshot(EUROSTOXX_TICKER)

    obs_id = record_observation(
        state=state,
        score=macro_score,
        bonds=bonds,
        vstoxx=vstoxx,
        wti=wti,
        eurostoxx_price=euro.last if euro else None,
        eurostoxx_change_pct=euro.change_pct if euro else None,
        regime=regime,
        discovered_large_cap_candidates=len(candidates),
    )
    update_learning_stats(state)

    strength = max(
        float(macro_score.get("short_score", 0.0)),
        float(macro_score.get("long_score", 0.0)),
    )
    print(
        f"Macro score {macro_score.get('direction')}: {strength:.1f}/100 "
        f"(alert >= {MACRO_ALERT_THRESHOLD:.0f})"
    )

    if should_emit_macro_alert(state, macro_score):
        reliability = reliability_for_score(state, macro_score)
        send_message(
            format_macro_alert(
                score=macro_score,
                bonds=bonds,
                vstoxx=vstoxx,
                wti=wti,
                eurostoxx_price=euro.last if euro else None,
                eurostoxx_change_pct=euro.change_pct if euro else None,
                reliability=reliability,
            )
        )
        mark_macro_alert(state, macro_score)

    # Alert bond a soglie fisse: 10 / 15 / 25 punti base, senza duplicati.
    send_bond_context(bonds, state)
    _save_state(state)

    sent = _send_equity_alerts(candidates, state, regime, bonds)

    # Il numero di crolli large-cap verificati viene registrato come feature,
    # ma non modifica ancora il macro-score: prima raccogliamo statistica reale.
    set_verified_crash_count(state, obs_id, sent)
    update_learning_stats(state)
    _save_state(state)
    print(f"Market Radar completato. Alert azionari inviati: {sent}")



def run_equity_rescan() -> None:
    """Seconda ricerca azionaria, senza rilanciare Nikkei, bond o altri sensori macro."""
    today = datetime.now(ROME).strftime("%Y-%m-%d")
    state = _load_state()
    _scan_news(state)
    candidates = _discover_equity_candidates_luna(today)
    sent = _send_equity_alerts(
        candidates, state, regime={}, bonds=[], same_day_dedupe=True
    )
    _save_state(state)
    print(
        f"Seconda ricerca azionaria 10:30 completata. "
        f"Candidati trovati: {len(candidates)}; nuovi alert inviati: {sent}"
    )
