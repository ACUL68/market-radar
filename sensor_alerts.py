"""Soglie autonome WTI e VSTOXX, senza interferire con il macro-score.

Un report per indicatore e direzione in ogni giornata (Europe/Rome).
I controlli avvengono solo durante le scansioni del Market Radar.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")

SENSORS = (
    ("WTI", "PETROLIO WTI", 2.0, "USD/barile"),
    ("VSTOXX", "VSTOXX", 5.0, "punti"),
)


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            value = value.replace(",", ".").replace("%", "").strip()
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def pending_sensor_alerts(
    state: dict[str, Any],
    wti: dict[str, Any] | None,
    vstoxx: dict[str, Any] | None,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """Prepara report da inviare. Nessuna mutazione dello stato prima dell'invio."""
    current = (now or datetime.now(ROME)).astimezone(ROME)
    date_key = current.date().isoformat()
    timestamp = current.strftime("%d/%m/%Y %H:%M")
    already_sent = state.get("sensor_alerts", {})
    contexts = {"WTI": wti or {}, "VSTOXX": vstoxx or {}}
    result: list[tuple[str, str]] = []

    for symbol, name, threshold, unit in SENSORS:
        ctx = contexts[symbol]
        change = _number(ctx.get("change_pct"))
        if change is None or abs(change) < threshold:
            continue

        direction = "UP" if change > 0 else "DOWN"
        key = f"{date_key}|{symbol}|{direction}"
        if key in already_sent:
            continue

        level = _number(ctx.get("level"))
        why = str(ctx.get("why") or "").strip()
        source = str(ctx.get("verified_by") or "").strip()
        sources = ctx.get("sources")
        urls = [str(url).strip() for url in sources if isinstance(url, str) and url.startswith("http")] if isinstance(sources, list) else []

        lines = [
            f"🚨 MARKET RADAR — {name}",
            f"Variazione odierna: {change:+.2f}% (soglia ±{threshold:g}%)",
        ]
        if level is not None:
            lines.append(f"Quotazione rilevata: {level:,.2f} {unit}".replace(",", " "))
        lines.extend([
            f"Scansione: {timestamp} Europe/Rome",
            "Causa riportata: " + (why if why else "non ancora verificata"),
        ])

        if symbol == "WTI":
            impact = (
                "Possibile pressione su inflazione e costi energetici; non implica automaticamente un ribasso degli indici."
                if change > 0 else
                "Possibile sollievo sui costi energetici, ma il calo può anche riflettere timori sulla domanda."
            )
        else:
            impact = (
                "Volatilità implicita europea in aumento; non è una previsione certa di ribasso."
                if change > 0 else
                "Volatilità implicita europea in diminuzione; non garantisce rialzi."
            )
        lines.append("Impatto possibile: " + impact)

        if source:
            lines.append("Prezzo verificato con: " + source)
        if urls:
            lines.extend(["Fonti:"] + urls[:3])
        lines.append("Segnale informativo, non ordine di trading.")
        result.append((key, "\n".join(lines)))

    return result


def mark_sensor_alert(state: dict[str, Any], key: str, now: datetime | None = None) -> None:
    """Marca solo dopo l'invio Telegram riuscito; rimuove i record dei giorni precedenti."""
    current = (now or datetime.now(ROME)).astimezone(ROME)
    date_key = current.date().isoformat()
    alerts = state.setdefault("sensor_alerts", {})
    alerts[key] = current.isoformat()
    state["sensor_alerts"] = {
        k: v for k, v in alerts.items() if k.startswith(date_key + "|")
    }
