from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from radar import run_asia_early_warning, run_radar

ROME = ZoneInfo("Europe/Rome")
SLOTS = [(8, 30), (10, 30), (16, 0), (21, 15)]


def due_target(now: datetime) -> datetime | None:
    if now.weekday() >= 5:
        return None
    candidates = []
    for hour, minute in SLOTS:
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if abs(now - target) <= timedelta(minutes=25):
            candidates.append(target)
    if not candidates:
        return None
    return min(candidates, key=lambda target: abs(now - target))


def due_now() -> bool:
    if os.getenv("FORCE_SCAN", "0") == "1":
        return True
    return due_target(datetime.now(ROME)) is not None


if __name__ == "__main__":
    if due_now():
        forced = os.getenv("FORCE_SCAN", "0") == "1"
        now = datetime.now(ROME)

        # GitHub viene chiamato 15 minuti prima del nostro orario-obiettivo.
        # Se parte puntuale, aspettiamo fino allo slot reale; se GitHub è in ritardo,
        # partiamo subito. In questo modo riduciamo i ritardi senza leggere i dati troppo presto.
        if not forced:
            target = due_target(now)
            if target and now < target:
                wait_seconds = (target - now).total_seconds()
                print(f"Avvio anticipato: attendo {int(wait_seconds // 60)} min fino alle {target.strftime('%H:%M')}.")
                time.sleep(wait_seconds)
                now = datetime.now(ROME)

        if (now.hour, now.minute) < (9, 10) and not forced:
            run_asia_early_warning()
        else:
            run_radar()
    else:
        now = datetime.now(ROME).strftime("%Y-%m-%d %H:%M")
        print(f"Nessuna scansione prevista alle {now} Europe/Rome.")
