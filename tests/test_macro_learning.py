import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macro_learning import calculate_macro_score, extract_move_bp, update_learning_stats


def test_extract_move_bp_from_text():
    assert extract_move_bp({"move": "rendimento +14,9 pb oggi"}) == 14.9
    assert extract_move_bp({"move": "rendimento -8.5 bp"}) == -8.5


def test_macro_score_short_when_stress_is_concordant():
    bonds = [
        {"benchmark": "US Treasury 10Y", "move_bp": 12},
        {"benchmark": "Bund 10Y", "move_bp": 12},
        {"benchmark": "OAT Francia 10Y", "move_bp": 12},
        {"benchmark": "BTP 10Y", "move_bp": 12},
        {"benchmark": "Gilt UK 10Y", "move_bp": 12},
    ]
    score = calculate_macro_score(
        bonds,
        {"change_pct": 12},
        {"change_pct": 4},
    )
    assert score["direction"] == "SHORT"
    assert score["short_score"] >= 60
    assert score["long_score"] == 0


def test_macro_score_long_when_stress_recedes():
    bonds = [
        {"benchmark": "US Treasury 10Y", "move_bp": -12},
        {"benchmark": "Bund 10Y", "move_bp": -12},
        {"benchmark": "OAT Francia 10Y", "move_bp": -12},
        {"benchmark": "BTP 10Y", "move_bp": -12},
        {"benchmark": "Gilt UK 10Y", "move_bp": -12},
    ]
    score = calculate_macro_score(
        bonds,
        {"change_pct": -12},
        {"change_pct": -4},
    )
    assert score["direction"] == "LONG"
    assert score["long_score"] >= 60
    assert score["short_score"] == 0


def test_learning_stats_measure_directional_hit_rate():
    state = {
        "learning": {
            "observations": [
                {
                    "score": {"signed_score": 80},
                    "features": {
                        "bond_contribution": 40,
                        "vstoxx_contribution": 15,
                        "wti_contribution": 10,
                    },
                    "outcomes": {"2h": {"return_pct": -1.0}},
                },
                {
                    "score": {"signed_score": -80},
                    "features": {
                        "bond_contribution": -40,
                        "vstoxx_contribution": -15,
                        "wti_contribution": -10,
                    },
                    "outcomes": {"2h": {"return_pct": 0.8}},
                },
            ]
        }
    }
    stats = update_learning_stats(state)
    assert stats["score_bands"]["75+"]["samples"] == 2
    assert stats["score_bands"]["75+"]["hit_rate_pct"] == 100.0
