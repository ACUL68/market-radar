from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from radar import run_asia_early_warning, run_radar
from wti import run_morning_wti

ROME = ZoneInfo("Europe/Rome")
STATE_FILE = Path(os.getenv("RADAR_STATE_FILE", ".radar_state.json"))

# Orari principali Europe/Rome.
SLOTS = {
    "08:15": "nikkei",
    "09:05": "radar",
    "15:35": "radar",
    "21:45": "radar",
}

# Ogni slot ha due tentativi GitHub: principale e recupero +25 minuti.
# Per ora legale/solare sono presenti entrambe le varianti UTC.
# Le varianti che capitano troppo presto vengono ignorate; quelle tardive
# possono servire come ulteriore recupero se lo slot non risulta completato.
CRON_TARGETS = {
    "15 6 * * 1-5": "08:15",
    "15 7 * * 1-5": "08:15",
    "40 6 * * 1-5": "08:15",
    "40 7 * * 1-5": "08:15",

    "5 7 * * 1-5": "09:05",
    "5 8 * * 1-5": "09:05",
    "30 7 * * 1-5": "09:05",
    "30 8 * * 1-5": "09:05",

    "35 13 * * 1-5": "15:35",
    "35 14 * * 1-5": "15:35",
    "0 14 * * 1-5": "15:35",
    "0 15 * * 1-5": "15:35",

    "45 19 * * 1-5": "21:45",
    "45 20 * * 1-5": "21:45",
    "10 20 * * 1-5": "21:45",
    "10 21 * * 1-5": "21:45",
}


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"alerts": {}}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _scan_key(now: datetime, slot: str) -> str:
    return f"{now.date().isoformat()}|{slot}"


def _slot_time(now: datetime, slot: str) -> datetime:
    hour, minute = (int(x) for x in slot.split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _already_completed(now: datetime, slot: str) -> bool:
    state = _load_state()
    return _scan_key(now, slot) in state.get("completed_scans", {})


def _mark_completed(now: datetime, slot: str) -> None:
    # Ricarica lo stato dopo il radar, perché radar.py può averlo aggiornato
    # con nuovi alert durante la stessa esecuzione.
    state = _load_state()
    completed = state.setdefault("completed_scans", {})
    completed[_scan_key(now, slot)] = datetime.now(ROME).isoformat()

    # Manteniamo solo gli ultimi giorni per non far crescere il file.
    today = now.date().isoformat()
    state["completed_scans"] = {
        key: value
        for key, value in completed.items()
        if key.startswith(today + "|")
    }
    _save_state(state)


def _run_slot(slot: str) -> None:
    if SLOTS[slot] == "nikkei":
        run_asia_early_warning()
        run_morning_wti()
    else:
        run_radar()


def _manual_run(now: datetime) -> None:
    if (now.hour, now.minute) < (9, 0):
        run_asia_early_warning()
        run_morning_wti()
    else:
        run_radar()


if __name__ == "__main__":
    now = datetime.now(ROME)

    if os.getenv("FORCE_SCAN", "0") == "1":
        _manual_run(now)
        raise SystemExit(0)

    if now.weekday() >= 5:
        print("Weekend: nessuna scansione prevista.")
        raise SystemExit(0)

    trigger = os.getenv("TRIGGER_SCHEDULE", "").strip()
    slot = CRON_TARGETS.get(trigger)

    if not slot:
        print(f"Trigger non riconosciuto: {trigger!r}. Nessuna scansione.")
        raise SystemExit(0)

    target = _slot_time(now, slot)

    # Un cron della variante CET/CEST può arrivare un'ora prima nella stagione
    # opposta: non deve anticipare la scansione. I ritardi invece sono accettati.
    if now < target:
        print(
            f"Trigger anticipato per lo slot {slot}: "
            f"ora {now.strftime('%H:%M')}. Nessuna scansione."
        )
        raise SystemExit(0)

    if _already_completed(now, slot):
        print(f"Slot {slot} già completato oggi: backup non necessario.")
        raise SystemExit(0)

    print(f"Eseguo slot {slot} alle {now.strftime('%H:%M:%S')} Europe/Rome.")
    _run_slot(slot)

    # Arriviamo qui solo se il programma non ha generato un errore.
    # Anche 'nessun segnale trovato' è una scansione completata correttamente.
    _mark_completed(now, slot)
    print(f"Slot {slot} completato e registrato.")
