from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import yfinance as yf

ROME = ZoneInfo("Europe/Rome")
EUROSTOXX_TICKER = "^STOXX50E"
WTI_TICKER = "CL=F"

MACRO_ALERT_THRESHOLD = float(os.getenv("MACRO_ALERT_THRESHOLD", "60"))
LEARNING_MAX_OBSERVATIONS = int(os.getenv("LEARNING_MAX_OBSERVATIONS", "250"))

# Pesi iniziali concordati: Bond 60%, VSTOXX 25%, WTI 15%.
# I pesi restano fissi: il modulo di apprendimento può solo proporre modifiche.
BOND_WEIGHTS = {
    "US Treasury 10Y": 14.0,
    "Bund 10Y": 14.0,
    "OAT Francia 10Y": 12.0,
    "BTP 10Y": 12.0,
    "Gilt UK 10Y": 8.0,
}
VSTOXX_WEIGHT = 25.0
WTI_WEIGHT = 15.0

BOND_FULL_SCALE_BP = 15.0
VSTOXX_FULL_SCALE_PCT = 15.0
WTI_FULL_SCALE_PCT = 5.0


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            value = value.replace(",", ".").replace("%", "").strip()
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_move_bp(bond: dict[str, Any]) -> float | None:
    for key in ("move_bp", "change_bp", "yield_change_bp"):
        value = safe_float(bond.get(key))
        if value is not None:
            return value

    text = str(bond.get("move", ""))
    match = re.search(
        r"([+-]?\d+(?:[.,]\d+)?)\s*(?:bp|bps|pb|punti\s+base)",
        text,
        flags=re.I,
    )
    if match:
        return safe_float(match.group(1))
    return None


def _bond_weight(benchmark: str) -> float:
    name = benchmark.lower()
    if "treasury" in name:
        return 14.0
    if "bund" in name:
        return 14.0
    if "oat" in name or "francia" in name:
        return 12.0
    if "btp" in name or "ital" in name:
        return 12.0
    if "gilt" in name or "uk" in name or "brit" in name:
        return 8.0
    return 0.0


def verified_wti_context(base: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = dict(base or {})
    try:
        hist = yf.Ticker(WTI_TICKER).history(period="5d", interval="1d", auto_adjust=True)
        if hist is not None and "Close" in hist:
            closes = hist["Close"].dropna()
            if len(closes) >= 2:
                last = float(closes.iloc[-1])
                prev = float(closes.iloc[-2])
                ctx["level"] = last
                ctx["change_pct"] = (last / prev - 1.0) * 100.0
                ctx["verified_by"] = "Yahoo Finance CL=F"
    except Exception as exc:
        print(f"WTI verify error: {exc}")
    return ctx


def calculate_macro_score(
    bonds: list[dict[str, Any]],
    vstoxx: dict[str, Any] | None,
    wti: dict[str, Any] | None,
) -> dict[str, Any]:
    bond_details: list[dict[str, Any]] = []
    bond_total = 0.0

    for bond in bonds:
        benchmark = str(bond.get("benchmark", "")).strip()
        weight = _bond_weight(benchmark)
        move_bp = extract_move_bp(bond)
        if weight <= 0 or move_bp is None:
            continue

        intensity = min(abs(move_bp) / BOND_FULL_SCALE_BP, 1.0)
        contribution = weight * intensity * (1.0 if move_bp > 0 else -1.0 if move_bp < 0 else 0.0)
        bond_total += contribution
        bond_details.append(
            {
                "benchmark": benchmark,
                "move_bp": round(move_bp, 2),
                "weight": weight,
                "contribution": round(contribution, 2),
            }
        )

    vstoxx_change = safe_float((vstoxx or {}).get("change_pct"))
    vstoxx_contribution = 0.0
    if vstoxx_change is not None:
        intensity = min(abs(vstoxx_change) / VSTOXX_FULL_SCALE_PCT, 1.0)
        vstoxx_contribution = VSTOXX_WEIGHT * intensity * (
            1.0 if vstoxx_change > 0 else -1.0 if vstoxx_change < 0 else 0.0
        )

    wti_change = safe_float((wti or {}).get("change_pct"))
    wti_contribution = 0.0
    if wti_change is not None:
        intensity = min(abs(wti_change) / WTI_FULL_SCALE_PCT, 1.0)
        wti_contribution = WTI_WEIGHT * intensity * (
            1.0 if wti_change > 0 else -1.0 if wti_change < 0 else 0.0
        )

    signed_score = max(
        -100.0,
        min(100.0, bond_total + vstoxx_contribution + wti_contribution),
    )

    return {
        "signed_score": round(signed_score, 1),
        "short_score": round(max(0.0, signed_score), 1),
        "long_score": round(max(0.0, -signed_score), 1),
        "direction": "SHORT" if signed_score > 0 else "LONG" if signed_score < 0 else "NEUTRAL",
        "components": {
            "bonds": round(bond_total, 2),
            "bond_details": bond_details,
            "vstoxx": round(vstoxx_contribution, 2),
            "wti": round(wti_contribution, 2),
        },
        "inputs": {
            "vstoxx_change_pct": vstoxx_change,
            "wti_change_pct": wti_change,
        },
    }


def should_emit_macro_alert(state: dict[str, Any], score: dict[str, Any]) -> bool:
    strength = max(float(score.get("short_score", 0.0)), float(score.get("long_score", 0.0)))
    if strength < MACRO_ALERT_THRESHOLD:
        return False

    direction = str(score.get("direction", "NEUTRAL"))
    old = state.get("macro_alert")
    if not old:
        return True

    try:
        old_time = datetime.fromisoformat(str(old["time"]))
        now = datetime.now(ROME)
        if old_time.tzinfo is None:
            old_time = old_time.replace(tzinfo=ROME)
        age_minutes = (now - old_time.astimezone(ROME)).total_seconds() / 60.0
        same_direction = str(old.get("direction")) == direction
        score_delta = abs(strength - float(old.get("strength", 0.0)))
        if same_direction and age_minutes < 120 and score_delta < 15:
            return False
    except Exception:
        pass

    return True


def mark_macro_alert(state: dict[str, Any], score: dict[str, Any]) -> None:
    state["macro_alert"] = {
        "time": datetime.now(ROME).isoformat(),
        "direction": score.get("direction"),
        "strength": max(float(score.get("short_score", 0.0)), float(score.get("long_score", 0.0))),
    }


def _score_band(abs_score: float) -> str:
    if abs_score >= 75:
        return "75+"
    if abs_score >= 60:
        return "60-74"
    if abs_score >= 40:
        return "40-59"
    return "<40"


def record_observation(
    state: dict[str, Any],
    score: dict[str, Any],
    bonds: list[dict[str, Any]],
    vstoxx: dict[str, Any] | None,
    wti: dict[str, Any] | None,
    eurostoxx_price: float | None,
    eurostoxx_change_pct: float | None,
    regime: dict[str, Any] | None = None,
    discovered_large_cap_candidates: int = 0,
) -> str:
    now = datetime.now(ROME)
    learning = state.setdefault("learning", {})
    observations = learning.setdefault("observations", [])

    nikkei = state.get("sensor_cache", {}).get("nikkei", {})
    nikkei_change = safe_float(nikkei.get("change_pct"))

    bond_moves: dict[str, float] = {}
    for bond in bonds:
        benchmark = str(bond.get("benchmark", "")).strip()
        move = extract_move_bp(bond)
        if benchmark and move is not None:
            bond_moves[benchmark] = round(move, 2)

    obs_id = now.strftime("%Y%m%dT%H%M%S%z")
    observation = {
        "id": obs_id,
        "time_rome": now.isoformat(),
        "score": score,
        "features": {
            "bond_moves_bp": bond_moves,
            "bond_contribution": safe_float(score.get("components", {}).get("bonds")),
            "vstoxx_change_pct": safe_float((vstoxx or {}).get("change_pct")),
            "vstoxx_contribution": safe_float(score.get("components", {}).get("vstoxx")),
            "wti_change_pct": safe_float((wti or {}).get("change_pct")),
            "wti_contribution": safe_float(score.get("components", {}).get("wti")),
            "nikkei_change_pct": nikkei_change,
            "discovered_large_cap_candidates": int(discovered_large_cap_candidates),
            "verified_large_cap_crashes": None,
        },
        "market_regime": regime or {},
        "eurostoxx": {
            "ticker": EUROSTOXX_TICKER,
            "price": eurostoxx_price,
            "change_pct": eurostoxx_change_pct,
        },
        "outcomes": {},
    }

    observations.append(observation)
    if len(observations) > LEARNING_MAX_OBSERVATIONS:
        learning["observations"] = observations[-LEARNING_MAX_OBSERVATIONS:]

    return obs_id


def set_verified_crash_count(state: dict[str, Any], obs_id: str, count: int) -> None:
    for obs in reversed(state.get("learning", {}).get("observations", [])):
        if obs.get("id") == obs_id:
            obs.setdefault("features", {})["verified_large_cap_crashes"] = int(count)
            return


def _intraday_eurostoxx():
    try:
        hist = yf.Ticker(EUROSTOXX_TICKER).history(
            period="5d",
            interval="15m",
            auto_adjust=True,
        )
        if hist is None or hist.empty or "Close" not in hist:
            return None
        hist = hist.dropna(subset=["Close"]).copy()
        if hist.empty:
            return None
        if hist.index.tz is None:
            hist.index = hist.index.tz_localize(ROME)
        else:
            hist.index = hist.index.tz_convert(ROME)
        return hist
    except Exception as exc:
        print(f"Learning intraday error: {exc}")
        return None


def _nearest_price(hist, target: datetime, tolerance_minutes: int = 20) -> tuple[float, datetime] | None:
    if hist is None or hist.empty:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=ROME)
    else:
        target = target.astimezone(ROME)

    deltas = abs(hist.index - target)
    pos = int(deltas.argmin())
    ts = hist.index[pos]
    delta_minutes = abs((ts.to_pydatetime() - target).total_seconds()) / 60.0
    if delta_minutes > tolerance_minutes:
        return None
    return float(hist["Close"].iloc[pos]), ts.to_pydatetime()


def _last_price_on_date(hist, day) -> tuple[float, datetime] | None:
    if hist is None or hist.empty:
        return None
    mask = [ts.date() == day for ts in hist.index]
    day_hist = hist.loc[mask]
    if day_hist.empty:
        return None
    ts = day_hist.index[-1]
    return float(day_hist["Close"].iloc[-1]), ts.to_pydatetime()


def _write_outcome(obs: dict[str, Any], key: str, result: tuple[float, datetime] | None) -> None:
    if not result:
        return
    base = safe_float(obs.get("eurostoxx", {}).get("price"))
    if base is None or base == 0:
        return
    price, observed_at = result
    obs.setdefault("outcomes", {})[key] = {
        "price": round(price, 4),
        "return_pct": round((price / base - 1.0) * 100.0, 4),
        "observed_at": observed_at.isoformat(),
    }


def update_learning_outcomes(state: dict[str, Any]) -> int:
    observations = state.get("learning", {}).get("observations", [])
    if not observations:
        return 0

    hist = _intraday_eurostoxx()
    if hist is None:
        return 0

    now = datetime.now(ROME)
    updated = 0

    for obs in observations:
        outcomes = obs.setdefault("outcomes", {})
        try:
            event = datetime.fromisoformat(str(obs["time_rome"]))
            if event.tzinfo is None:
                event = event.replace(tzinfo=ROME)
            else:
                event = event.astimezone(ROME)
        except Exception:
            continue

        before = len(outcomes)

        if "30m" not in outcomes and now >= event + timedelta(minutes=30):
            _write_outcome(obs, "30m", _nearest_price(hist, event + timedelta(minutes=30)))

        if "2h" not in outcomes and now >= event + timedelta(hours=2):
            _write_outcome(obs, "2h", _nearest_price(hist, event + timedelta(hours=2)))

        same_close = event.replace(hour=17, minute=30, second=0, microsecond=0)
        if (
            "close" not in outcomes
            and event.time() <= dtime(17, 30)
            and now >= same_close
        ):
            _write_outcome(obs, "close", _last_price_on_date(hist, event.date()))

        if "next_close" not in outcomes:
            dates = sorted({ts.date() for ts in hist.index if ts.date() > event.date()})
            if dates:
                next_day = dates[0]
                next_close_due = datetime.combine(next_day, dtime(17, 30), tzinfo=ROME)
                if now >= next_close_due:
                    _write_outcome(obs, "next_close", _last_price_on_date(hist, next_day))

        if len(outcomes) > before:
            updated += 1

    return updated


def _observation_target_return(obs: dict[str, Any]) -> float | None:
    outcomes = obs.get("outcomes", {})
    event = None
    try:
        event = datetime.fromisoformat(str(obs.get("time_rome")))
        if event.tzinfo is None:
            event = event.replace(tzinfo=ROME)
        else:
            event = event.astimezone(ROME)
    except Exception:
        pass

    # Durante la seduta preferiamo +2h, poi chiusura.
    # Dopo la chiusura usiamo la chiusura della seduta successiva.
    if event and event.time() > dtime(17, 30):
        order = ("next_close", "2h", "close", "30m")
    else:
        order = ("2h", "close", "next_close", "30m")

    for key in order:
        value = safe_float((outcomes.get(key) or {}).get("return_pct"))
        if value is not None:
            return value
    return None


def _sensor_stats(observations: list[dict[str, Any]], feature_key: str, min_abs: float) -> dict[str, Any]:
    used = 0
    correct = 0
    directional_returns: list[float] = []

    for obs in observations:
        ret = _observation_target_return(obs)
        contribution = safe_float(obs.get("features", {}).get(feature_key))
        if ret is None or contribution is None or abs(contribution) < min_abs or ret == 0:
            continue
        used += 1
        expected_sign = -1.0 if contribution > 0 else 1.0
        actual_sign = 1.0 if ret > 0 else -1.0
        if actual_sign == expected_sign:
            correct += 1
        directional_returns.append(ret * expected_sign)

    return {
        "samples": used,
        "hit_rate_pct": round(correct / used * 100.0, 1) if used else None,
        "avg_directional_return_pct": round(sum(directional_returns) / len(directional_returns), 3)
        if directional_returns
        else None,
    }


def update_learning_stats(state: dict[str, Any]) -> dict[str, Any]:
    learning = state.setdefault("learning", {})
    observations = learning.get("observations", [])

    score_bands: dict[str, dict[str, Any]] = {}
    for band in ("40-59", "60-74", "75+"):
        score_bands[band] = {"samples": 0, "correct": 0, "returns": []}

    completed = 0
    for obs in observations:
        ret = _observation_target_return(obs)
        signed = safe_float(obs.get("score", {}).get("signed_score"))
        if ret is None or signed is None:
            continue
        completed += 1
        abs_score = abs(signed)
        band = _score_band(abs_score)
        if band == "<40":
            continue
        bucket = score_bands[band]
        bucket["samples"] += 1
        is_correct = (signed > 0 and ret < 0) or (signed < 0 and ret > 0)
        if is_correct:
            bucket["correct"] += 1
        directional_return = -ret if signed > 0 else ret
        bucket["returns"].append(directional_return)

    normalized_bands: dict[str, Any] = {}
    for band, bucket in score_bands.items():
        samples = int(bucket["samples"])
        returns = bucket["returns"]
        normalized_bands[band] = {
            "samples": samples,
            "hit_rate_pct": round(bucket["correct"] / samples * 100.0, 1) if samples else None,
            "avg_directional_return_pct": round(sum(returns) / len(returns), 3) if returns else None,
        }

    sensors = {
        "bonds": _sensor_stats(observations, "bond_contribution", 8.0),
        "vstoxx": _sensor_stats(observations, "vstoxx_contribution", 5.0),
        "wti": _sensor_stats(observations, "wti_contribution", 3.0),
    }

    recommendations: list[dict[str, Any]] = []
    for sensor, stats in sensors.items():
        samples = int(stats.get("samples") or 0)
        hit_rate = safe_float(stats.get("hit_rate_pct"))
        if samples < 20 or hit_rate is None:
            continue
        if hit_rate >= 65:
            suggestion = "aumentare"
        elif hit_rate <= 45:
            suggestion = "ridurre"
        else:
            suggestion = "mantenere"
        recommendations.append(
            {
                "sensor": sensor,
                "suggestion": suggestion,
                "hit_rate_pct": hit_rate,
                "samples": samples,
            }
        )

    stats = {
        "updated_at": datetime.now(ROME).isoformat(),
        "completed_samples": completed,
        "score_bands": normalized_bands,
        "sensors": sensors,
        "recommendations": recommendations,
        "note": "Le raccomandazioni non modificano automaticamente pesi o soglie.",
    }
    learning["stats"] = stats
    return stats


def reliability_for_score(state: dict[str, Any], score: dict[str, Any]) -> dict[str, Any] | None:
    signed = abs(float(score.get("signed_score", 0.0)))
    band = _score_band(signed)
    if band == "<40":
        return None
    stats = state.get("learning", {}).get("stats", {}).get("score_bands", {}).get(band, {})
    samples = int(stats.get("samples") or 0)
    hit_rate = safe_float(stats.get("hit_rate_pct"))
    if samples < 10 or hit_rate is None:
        return None
    return {"band": band, "samples": samples, "hit_rate_pct": hit_rate}


def format_macro_alert(
    score: dict[str, Any],
    bonds: list[dict[str, Any]],
    vstoxx: dict[str, Any] | None,
    wti: dict[str, Any] | None,
    eurostoxx_price: float | None,
    eurostoxx_change_pct: float | None,
    reliability: dict[str, Any] | None = None,
) -> str:
    direction = str(score.get("direction", "NEUTRAL"))
    strength = float(score.get("short_score" if direction == "SHORT" else "long_score", 0.0))

    lines = [
        "🧠 MARKET RADAR — MACRO EURO STOXX",
        "",
        f"{direction} SCORE: {strength:.0f}/100",
    ]

    if eurostoxx_price is not None:
        change_text = f" ({eurostoxx_change_pct:+.2f}%)" if eurostoxx_change_pct is not None else ""
        lines.append(f"EuroStoxx 50: {eurostoxx_price:.2f}{change_text}")

    bond_bits = []
    for bond in bonds:
        move = extract_move_bp(bond)
        if move is not None:
            name = str(bond.get("benchmark", "")).replace(" 10Y", "")
            bond_bits.append(f"{name} {move:+.1f} pb")
    if bond_bits:
        lines.append("Bond: " + " | ".join(bond_bits[:5]))

    vstoxx_change = safe_float((vstoxx or {}).get("change_pct"))
    if vstoxx_change is not None:
        lines.append(f"VSTOXX: {vstoxx_change:+.2f}%")

    wti_change = safe_float((wti or {}).get("change_pct"))
    if wti_change is not None:
        lines.append(f"WTI: {wti_change:+.2f}%")

    components = score.get("components", {})
    lines.append(
        "Contributi: "
        f"bond {float(components.get('bonds', 0.0)):+.1f} | "
        f"VSTOXX {float(components.get('vstoxx', 0.0)):+.1f} | "
        f"WTI {float(components.get('wti', 0.0)):+.1f}"
    )

    if reliability:
        lines.append(
            f"Storico fascia {reliability['band']}: "
            f"{reliability['hit_rate_pct']:.1f}% su {reliability['samples']} casi"
        )
    else:
        lines.append("Storico: raccolta dati in corso")

    lines.append("Il Radar registra automaticamente l'esito su EuroStoxx per imparare.")
    return "\n".join(lines)
