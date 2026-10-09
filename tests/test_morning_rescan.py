"""La seconda ricerca mattutina non ripete macro né avvisi già inviati."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import main
import radar


def test_new_slot_is_independent_from_morning_scan():
    assert main.SLOTS["09:05"] == "radar"
    assert main.SLOTS["10:30"] == "equity_rescan"
    assert main.CRON_TARGETS["30 8 * * 1-5"] == "10:30"
    assert main.CRON_TARGETS["30 9 * * 1-5"] == "10:30"
    assert main.CRON_TARGETS["31 8 * * 1-5"] == "09:05"
    assert main.CRON_TARGETS["55 8 * * 1-5"] == "10:30"
    assert main.CRON_TARGETS["55 9 * * 1-5"] == "10:30"

    with patch.object(main, "run_equity_rescan") as rescan, patch.object(main, "run_radar") as macro:
        main._run_slot("10:30")
    rescan.assert_called_once()
    macro.assert_not_called()


def test_clock_mapping_in_summer_and_winter():
    rome = ZoneInfo("Europe/Rome")
    summer = datetime(2026, 10, 9, 8, 30, tzinfo=timezone.utc).astimezone(rome)
    winter = datetime(2026, 12, 9, 9, 30, tzinfo=timezone.utc).astimezone(rome)
    assert summer.strftime("%H:%M") == "10:30"
    assert winter.strftime("%H:%M") == "10:30"
    assert summer >= main._slot_time(summer, "10:30")
    assert winter >= main._slot_time(winter, "10:30")


def test_second_scan_does_not_launch_macro_or_bonds():
    candidates = [{"ticker": "DTE.DE", "reported_change_pct": -7.5}]
    state = {"alerts": {}}
    with (
        patch.object(radar, "_load_state", return_value=state),
        patch.object(radar, "_save_state") as save,
        patch.object(radar, "_discover_equity_candidates_luna", return_value=candidates) as luna,
        patch.object(radar, "_send_equity_alerts", return_value=1) as send,
        patch.object(radar, "discover_market") as full_discovery,
        patch.object(radar, "send_bond_context") as bonds,
    ):
        radar.run_equity_rescan()
    luna.assert_called_once()
    send.assert_called_once_with(candidates, state, regime={}, bonds=[], same_day_dedupe=True)
    save.assert_called_once_with(state)
    full_discovery.assert_not_called()
    bonds.assert_not_called()


def test_rescan_skips_equity_already_alerted_today():
    now = datetime.now(timezone.utc)
    state = {"alerts": {"DTE.DE": {"time": now.isoformat(), "change_pct": -7.5}}}
    candidates = [{"ticker": "DTE.DE", "reported_change_pct": -11.0}]
    with patch.object(radar, "market_snapshot") as snap:
        sent = radar._send_equity_alerts(candidates, state, {}, [], same_day_dedupe=True)
    assert sent == 0
    snap.assert_not_called()


def test_new_day_does_not_block_equity():
    yesterday = datetime.now(timezone.utc) - timedelta(days=2)
    state = {"alerts": {"DTE.DE": {"time": yesterday.isoformat(), "change_pct": -7.5}}}
    assert radar._was_alerted_today("DTE.DE", state) is False
    assert radar._was_alerted_today("SU.PA", state) is False
