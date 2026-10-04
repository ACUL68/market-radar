from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import requests
import yfinance as yf
from openai import OpenAI

DISCOVERY_MODEL = os.getenv("OPENAI_DISCOVERY_MODEL", "gpt-6-luna")
ANALYSIS_MODEL = os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-6-sol")
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", "4"))
STATE_FILE = Path(os.getenv("RADAR_STATE_FILE", ".radar_state.json"))

INDEX_BY_REGION = {
    "USA": "^NDX",
    "US": "^NDX",
    "EUROPE": "^STOXX50E",
    "EU": "^STOXX50E",
}

INDEX_WATCH = {
    "DAX": "^GDAXI",
    "Euro Stoxx 50": "^STOXX50E",
    "Nasdaq 100": "^NDX",
    "S&P 500": "^GSPC",
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


def discover_market() -> dict[str, Any]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = f"""
Sei il primo livello di MARKET RADAR. Oggi è {today}.
Usa la ricerca web e fonti finanziarie recenti e affidabili. Non dare consigli di acquisto o vendita.

Obiettivi:
1) Stabilisci la FASE STRUTTURALE del mercato USA e del mercato europeo: bull, correction, bear oppure uncertain. La fase strutturale riguarda settimane/mesi, NON la sola seduta di oggi. Riporta una motivazione molto breve e fonti recenti.
2) Trova società quotate USA o Europa di grande capitalizzazione e buona liquidità che OGGI stanno avendo un ribasso forte o chiaramente anomalo rispetto al proprio indice/settore. Non usare una soglia rigida: privilegia anomalie relative. Escludi micro-cap, penny stock e titoli illiquidi.
3) Controlla anche i benchmark obbligazionari che influenzano i mercati: Treasury USA 10Y, Bund 10Y, BTP 10Y, OAT Francia 10Y, Gilt UK 10Y. Segnala soltanto movimenti davvero rilevanti per il contesto azionario.
4) Per i ticker azionari usa, quando possibile, il simbolo compatibile con Yahoo Finance (es. GOOGL, MBG.DE, AIR.PA, ISP.MI).

Restituisci SOLO JSON valido con questa struttura:
{{
  "market_regime": {{
    "usa": {{"phase": "bull|correction|bear|uncertain", "reason": "...", "sources": ["https://..."]}},
    "europe": {{"phase": "bull|correction|bear|uncertain", "reason": "...", "sources": ["https://..."]}}
  }},
  "bond_context": [
    {{"benchmark": "US Treasury 10Y", "move": "...", "why": "...", "important": true, "sources": ["https://..."]}}
  ],
  "equity_candidates": [
    {{
      "company": "...",
      "ticker": "...",
      "region": "USA|EUROPE",
      "sector": "...",
      "index_reference": "Nasdaq 100|S&P 500|Euro Stoxx 50|altro",
      "reported_change_pct": -8.4,
      "why_candidate": "..."
    }}
  ]
}}

Massimo {MAX_CANDIDATES} candidati azionari, ordinati per anomalia/interesse da approfondire.
"""
    return _ask_web(DISCOVERY_MODEL, prompt)


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
Usa la ricerca web, fai tutte le verifiche successive che ritieni utili e incrocia più fonti. Se le fonti non concordano, dichiaralo.

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


def send_bond_context(bonds: list[dict[str, Any]]) -> None:
    important = [b for b in bonds if b.get("important")]
    if not important:
        return
    lines = ["🟦 MARKET RADAR — BOND / TASSI"]
    for b in important[:5]:
        lines.append(f"\n{b.get('benchmark')}\nMovimento: {b.get('move')}\nPerché conta: {b.get('why')}")
    send_message("\n".join(lines))



def detect_index_anomaly() -> dict[str, Any] | None:
    """
    Cerca un indice che 'canta fuori dal coro'.
    Prima dell'apertura USA confronta solo DAX ed Euro Stoxx 50.
    Dalle 15:45 Europe/Rome usa tutti e quattro gli indici.
    """
    now = datetime.now(ROME)
    names = ["DAX", "Euro Stoxx 50"]
    if (now.hour, now.minute) >= (15, 45):
        names = ["DAX", "Euro Stoxx 50", "Nasdaq 100", "S&P 500"]

    snaps: dict[str, MarketSnapshot] = {}
    for name in names:
        snap = market_snapshot(INDEX_WATCH[name])
        if snap:
            snaps[name] = snap

    if len(snaps) < 2:
        return None

    changes = {name: snap.change_pct for name, snap in snaps.items()}

    # Con la sola coppia europea segnaliamo solo una divergenza molto evidente.
    if len(changes) == 2:
        a, b = list(changes)
        gap = abs(changes[a] - changes[b])
        if gap < 1.50:
            return None
        outlier = a if abs(changes[a]) >= abs(changes[b]) else b
        other = b if outlier == a else a
        return {
            "outlier": outlier,
            "change_pct": changes[outlier],
            "reference_move_pct": changes[other],
            "gap_pct_points": changes[outlier] - changes[other],
            "changes": changes,
            "mode": "europe_pair",
        }

    best: dict[str, Any] | None = None
    for name, value in changes.items():
        others = [v for n, v in changes.items() if n != name]
        center = median(others)
        gap = value - center
        positive_others = sum(v >= 0.30 for v in others)
        negative_others = sum(v <= -0.30 for v in others)
        opposite_to_majority = (
            (value <= -0.30 and positive_others >= 2)
            or (value >= 0.30 and negative_others >= 2)
        )

        # Pochi falsi positivi: direzione opposta + almeno 1 punto di scarto,
        # oppure divergenza estrema di almeno 1,75 punti.
        important = (opposite_to_majority and abs(gap) >= 1.00) or abs(gap) >= 1.75
        if not important:
            continue

        candidate = {
            "outlier": name,
            "change_pct": value,
            "reference_move_pct": center,
            "gap_pct_points": gap,
            "changes": changes,
            "mode": "global_four",
        }
        if best is None or abs(candidate["gap_pct_points"]) > abs(best["gap_pct_points"]):
            best = candidate

    return best


def analyze_index_anomaly(anomaly: dict[str, Any], regime: dict[str, Any],
                          bond_context: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = f"""
Sei MARKET RADAR. Devi spiegare una divergenza anomala tra i principali indici, non dare consigli di trading.
Usa ricerca web e fonti finanziarie recenti e affidabili. Incrocia più fonti.

Movimenti verificati dal programma:
{json.dumps(anomaly.get('changes', {}), ensure_ascii=False)}

Indice fuori dal coro: {anomaly.get('outlier')}
Scarto rispetto agli altri: {anomaly.get('gap_pct_points'):+.2f} punti percentuali.
Fase strutturale USA/Europa: {json.dumps(regime, ensure_ascii=False)}
Contesto bond/tassi: {json.dumps(bond_context, ensure_ascii=False)}

Devi capire PERCHÉ quell'indice si sta muovendo in modo diverso dagli altri.
Controlla in particolare: tassi e bond, valuta, composizione settoriale dell'indice,
banche/tecnologia/industria, dati macro, politica fiscale o monetaria,
geopolitica, trimestrali pesanti e notizie locali.

Restituisci SOLO JSON valido:
{{
  "important": true,
  "cause": "...",
  "why_it_matters": "massimo 500 caratteri, italiano semplice",
  "confidence": 0,
  "sources": ["https://..."]
}}

important=false soltanto se la discrepanza è spiegabile da orari di mercato, dati non confrontabili
o rumore tecnico e non rappresenta una vera anomalia.
"""
    return _ask_web(ANALYSIS_MODEL, prompt)


def scan_and_send_index_anomaly(regime: dict[str, Any], bonds: list[dict[str, Any]],
                                state: dict[str, Any]) -> None:
    anomaly = detect_index_anomaly()
    if not anomaly:
        print("Nessuna anomalia rilevante tra gli indici")
        return

    outlier = str(anomaly["outlier"])
    key = f"INDEX:{outlier}"
    change = float(anomaly["change_pct"])
    if should_suppress(key, change, state):
        print(f"Anomalia indice duplicata soppressa: {outlier}")
        return

    try:
        analysis = analyze_index_anomaly(anomaly, regime, bonds)
    except Exception as exc:
        print(f"Analisi indice non disponibile: {exc}")
        return

    if not analysis.get("important", True):
        print(f"Anomalia indice scartata dopo analisi: {outlier}")
        return

    moves = anomaly.get("changes") or {}
    move_lines = "\n".join(f"{name}: {value:+.2f}%" for name, value in moves.items())
    sources = analysis.get("sources") or []
    source_text = "\n".join(f"- {u}" for u in sources[:4])

    body = (
        "🟨 MARKET RADAR — ANOMALIA INDICI\n\n"
        f"{move_lines}\n\n"
        f"Fuori dal coro: {outlier}\n"
        f"Scarto dagli altri: {float(anomaly['gap_pct_points']):+.2f} punti\n\n"
        f"Causa probabile: {analysis.get('cause', 'non chiara')}\n"
        f"Perché conta: {analysis.get('why_it_matters', '')}\n"
        f"Confidenza: {analysis.get('confidence', 'n/d')}%"
    )
    if source_text:
        body += f"\n\nFonti:\n{source_text}"

    send_message(body)
    mark_alert(key, change, state)


def run_radar() -> None:
    discovery = discover_market()
    regime = discovery.get("market_regime") or {}
    bonds = discovery.get("bond_context") or []
    candidates = discovery.get("equity_candidates") or []

    send_bond_context(bonds)
    state = _load_state()

    # Controllo separato delle divergenze tra DAX, Euro Stoxx 50, Nasdaq 100 e S&P 500.
    # Un indice che si muove in direzione opposta o molto più forte/debole degli altri
    # genera un alert dedicato, dopo verifica della causa con ricerca web.
    scan_and_send_index_anomaly(regime, bonds, state)

    sent = 0

    for candidate in candidates[:MAX_CANDIDATES]:
        ticker = str(candidate.get("ticker", "")).strip()
        if not ticker:
            continue
        snap = market_snapshot(ticker)
        if not snap:
            print(f"Skip {ticker}: quotazione non verificabile")
            continue
        idx_ticker = index_for_candidate(candidate)
        idx = market_snapshot(idx_ticker)

        # L'AI trova i candidati; il programma richiede anche un'anomalia numerica minima.
        relative_gap = abs(snap.change_pct - (idx.change_pct if idx else 0.0))
        if snap.change_pct > -4.0 and relative_gap < 5.0:
            print(f"Skip {ticker}: movimento verificato non abbastanza anomalo")
            continue

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

    _save_state(state)
    print(f"Market Radar completato. Alert azionari inviati: {sent}")
