"""Verifica che la scoperta azionaria usi solo Luna e preservi i dati macro."""
from unittest.mock import patch

import radar


def test_discover_market_preserves_macro_context_and_uses_luna():
    macro = {"bond_context": [{"benchmark": "Bund 10Y", "move_bp": 3.0}]}
    candidates = [{"ticker": "DTE.DE", "reported_change_pct": -7.5}]

    with (
        patch.object(radar, "_discover_context", return_value=macro) as context_scan,
        patch.object(radar, "_discover_equity_candidates_luna", return_value=candidates) as equity_scan,
    ):
        result = radar.discover_market()

    assert result["bond_context"] == macro["bond_context"]
    assert result["equity_candidates"] == candidates
    context_scan.assert_called_once()
    equity_scan.assert_called_once()
