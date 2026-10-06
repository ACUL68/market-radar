from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from radar import run_asia_early_warning, run_radar

ROME = ZoneInfo("Europe/Rome")
SLOTS = [(8, 15), (9, 30), (15, 45), (21, 0)]


def due_now() -> bool:
    if os.getenv("FORCE_SCAN", "0") == "1":
        return True
    now = datetime.now(ROME)
    if now.weekday() >= 5:
        return False
    for hour, minute in SLOTS:
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if abs(now - target) <= timedelta(minutes=25):
            return True
    return False


if __name__ == "__main__":
    if due_now():
        now = datetime.now(ROME)
        if (now.hour, now.minute) < (9, 0) and os.getenv("FORCE_SCAN", "0") != "1":
            run_asia_early_warning()
        else:
            run_radar()
    else:
        now = datetime.now(ROME).strftime("%Y-%m-%d %H:%M")
        print(f"Nessuna scansione prevista alle {now} Europe/Rome.")
