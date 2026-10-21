# EuroStoxx mode

The default `RADAR_MODE=eurostoxx` replaces paid market discovery and equity
rescans with numeric monitoring. The 10:30 slot becomes another numeric scan.
There are no scheduled AI calls: new threshold events are grouped into one
optional explanation per scan, capped at 2 attempts/day and 30 attempts/month.
Daily/monthly caps include failed attempts. API automatic retries are disabled.
Each response is capped at 1,200 output tokens and 2 web tool calls. No API or
Telegram calls were made during unit tests.

## Data and thresholds

- Yahoo chart metadata: EuroStoxx 50 spot and S&P futures 1%, WTI futures 2%.
- Nikkei: 1%, 1.5%, 2%, both directions, at the existing Asia slot.
- TradingView numeric benchmark yields: Bund, BTP, OAT, Gilt and Treasury;
  alerts at 10/15/25 basis points. `change_abs` is in yield percentage points,
  so the code multiplies by 100. The endpoint supplies today's daily bar and
  streaming status, but no exact last-tick timestamp; alerts say so explicitly.
- VSTOXX: official STOXX closing file, 5/10/15%. Intraday VSTOXX is currently
  **not covered**. Today's official closing value is checked after 18:00 Rome.
  Yesterday's value is never presented as today's; VIX and volatility futures
  are never substituted for the VSTOXX spot index.
- RSS news: existing ANSA feed filters without AI.

Unavailable sources produce an explicit daily coverage notice and do not
trigger paid discovery. A quiet scan makes zero AI calls. AI quota errors
leave numeric alerts running, with a 6-hour AI cooldown. A manual run can
retry immediately after a top-up. Explanations are cached before Telegram
sends, preventing another purchase if message delivery fails. Higher anomaly
tiers suppress lower-tier repeats on the same day and in the same direction.

The legacy equity pipeline remains accessible only via `RADAR_MODE=legacy`.
Legacy macro learning/scoring is not updated by this mode; it cannot safely
reuse the old AI-derived observations as if all sensors were still present.

## Spending scope

These are request/token/tool limits, **not a guaranteed EUR 5 monthly cap**.
Prices, input/search tokens, taxes, exchange rates and other API users are not
accounted for by the request counter. The existing model is retained.
The usage ledger is stored with `.radar_state.json`; GitHub restores it before
runs and saves it even after failures. GitHub cache eviction or deletion can
reset this ledger. A prepaid balance without automatic recharge is the
account-side spending control; inspect actual Usage after the first days.

## Verification

`python -m pytest -q` and `python -m py_compile main.py radar.py euro_radar.py`.
Tests mock paid/network/delivery operations. A separate read-only live probe
returned EuroStoxx, S&P futures, WTI and all five benchmark yields. VSTOXX
intraday is reported missing, as designed. No paid AI end-to-end check has
been made before replenishing the account.
