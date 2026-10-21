"""EuroStoxx-focused monitoring: numeric gates first, optional bounded AI second."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

ROME = ZoneInfo('Europe/Rome')
YAHOO = {'EuroStoxx 50': ('^STOXX50E', 1.0), 'Future S&P 500': ('ES=F', 1.0),
         'WTI future': ('CL=F', 2.0), 'Nikkei 225': ('^N225', 1.0)}
BONDS = {'DE10Y': 'Bund 10Y', 'IT10Y': 'BTP 10Y', 'FR10Y': 'OAT Francia 10Y',
         'GB10Y': 'Gilt UK 10Y', 'US10Y': 'US Treasury 10Y'}


def number(value):
    try:
        result = float(str(value).replace(',', '').removesuffix('%'))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def yahoo_quote(ticker, now):
    """Chart metadata includes previous-session close and exchange quote time."""
    from urllib.parse import quote
    r = requests.get(f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker, safe="")}',
                     params={'range': '1d', 'interval': '5m'},
                     headers={'User-Agent': 'MarketRadar/2.0'}, timeout=15)
    r.raise_for_status()
    meta = r.json()['chart']['result'][0]['meta']
    price = number(meta.get('regularMarketPrice'))
    previous = number(meta.get('chartPreviousClose'))
    at = datetime.fromtimestamp(float(meta['regularMarketTime']), ROME)
    # Today's closing quote is useful at 21:45; it is explicitly labelled with its time.
    if at.date() != now.date() or at > now + timedelta(minutes=5):
        raise ValueError('quotazione non della giornata')
    age = now - at
    closed = ticker in ('^STOXX50E', '^N225') and now.hour >= (18 if ticker == '^STOXX50E' else 9)
    if age > timedelta(minutes=30) and not closed:
        raise ValueError('quotazione troppo vecchia')
    if price is None or previous is None or previous <= 0 or price <= 0:
        raise ValueError('prezzo o chiusura precedente mancanti')
    return {'price': price, 'change': (price / previous - 1) * 100,
            'time': at.isoformat(), 'source': f'https://finance.yahoo.com/quote/{quote(ticker, safe="")}/',
            'unit': '%'}


def bond_quotes(now):
    """Public numeric benchmark yields; daily-bar date is not a quote timestamp."""
    symbols = ['TVC:' + symbol for symbol in BONDS]
    r = requests.post('https://scanner.tradingview.com/bonds/scan',
                      json={'symbols': {'tickers': symbols, 'query': {'types': []}},
                            'columns': ['name', 'close', 'change_abs', 'time', 'update_mode']},
                      timeout=15)
    r.raise_for_status()
    result = {}
    for item in r.json().get('data', []):
        symbol, price, delta, stamp, mode = item['d']
        if item.get('s') != 'TVC:' + symbol or symbol not in BONDS:
            continue
        price, delta, stamp = number(price), number(delta), number(stamp)
        if price is None or delta is None or stamp is None or not -5 < price < 30:
            continue
        bar = datetime.fromtimestamp(stamp, ROME)
        if bar.date() != now.date() or bar > now + timedelta(minutes=5):
            continue
        # Streaming endpoint fetched afresh. Exact last tick time is not supplied.
        if mode not in ('streaming', 'delayed_streaming_900'):
            continue
        result[BONDS[symbol]] = {'price': price, 'change': delta * 100,
                                'time': now.isoformat(), 'unit': 'pb',
                                'source': f'https://www.tradingview.com/symbols/TVC-{symbol}/',
                                'note': 'rilevato ora da barra giornaliera di oggi; ora ultimo tick non fornita'}
    return result


def vstoxx_quote(now):
    """Official STOXX closing history only; never substitutes VIX or a futures contract."""
    if now.hour < 18:
        raise ValueError('fonte ufficiale gratuita di chiusura: dato intraday non disponibile')
    url = 'https://www.stoxx.com/document/Indices/Current/HistoricalData/h_v2tx.txt'
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    values = []
    for line in r.text.splitlines():
        fields = line.strip().split(';')
        if len(fields) < 2:
            continue
        try:
            day = datetime.strptime(fields[0], '%d.%m.%Y').date()
        except ValueError:
            continue
        value = number(fields[2]) if len(fields) >= 3 and fields[1] == 'V2TX' else None
        if value is not None and value > 0:
            values.append((day, value))
    values.sort()
    if len(values) < 2 or values[-1][0] != now.date():
        raise ValueError('chiusura VSTOXX di oggi non ancora pubblicata')
    return {'price': values[-1][1], 'change': (values[-1][1] / values[-2][1] - 1) * 100,
            'time': now.replace(hour=17, minute=30, second=0, microsecond=0).isoformat(),
            'unit': '%', 'source': url, 'note': 'dato ufficiale di chiusura, non intraday'}


def collect(now, asia_only=False):
    quotes, missing = {}, []
    watch = {'Nikkei 225': YAHOO['Nikkei 225']} if asia_only else {
        key: value for key, value in YAHOO.items() if key != 'Nikkei 225'}
    for name, (symbol, _) in watch.items():
        try:
            quotes[name] = yahoo_quote(symbol, now)
        except Exception:
            missing.append(name)
    if not asia_only:
        try:
            quotes.update(bond_quotes(now))
        except Exception:
            pass
        missing.extend(name for name in BONDS.values() if name not in quotes)
        try:
            quotes['VSTOXX'] = vstoxx_quote(now)
        except Exception:
            missing.append('VSTOXX (intraday non disponibile; chiusura da verificare dopo le 18)')
    return quotes, missing


def anomaly_key(name, quote, now):
    move = quote['change']
    thresholds = (10, 15, 25) if quote['unit'] == 'pb' else (
        (1, 1.5, 2) if name == 'Nikkei 225' else ((5, 10, 15) if name == 'VSTOXX' else
        ((2, 3, 5) if name == 'WTI future' else (1, 1.5, 2))))
    tier = max((v for v in thresholds if abs(move) >= v), default=None)
    return None if tier is None else f'{now.date()}|{name}|{"UP" if move > 0 else "DOWN"}|{tier}'


def explain(events, state, save, now):
    """Reserve attempts before network: no retries, max 2/day and 30/month by default."""
    if not events or not os.getenv('OPENAI_API_KEY'):
        return 'Approfondimento AI non disponibile; controlli numerici attivi.'
    ledger = state.setdefault('euro_ai_usage', {})
    month, day = now.strftime('%Y-%m'), str(now.date())
    monthly = ledger.setdefault(month, {'attempts': 0, 'days': {}})
    if monthly['attempts'] >= int(os.getenv('RADAR_AI_MAX_MONTHLY_CALLS', '30')) or monthly['days'].get(day, 0) >= int(os.getenv('RADAR_AI_MAX_DAILY_CALLS', '2')):
        return 'Limite richieste AI raggiunto; controlli numerici attivi.'
    exhausted = ledger.get('quota_exhausted_at')
    if exhausted and os.getenv('FORCE_SCAN', '0') != '1':
        if now - datetime.fromisoformat(exhausted) < timedelta(hours=6):
            return 'Credito API esaurito; controlli numerici attivi.'
    monthly['attempts'] += 1
    monthly['days'][day] = monthly['days'].get(day, 0) + 1
    save(state)
    from openai import OpenAI
    prompt = ('Oggi ' + now.isoformat() + '. Spiega in italiano in massimo 180 parole le anomalie '
              'numeriche seguenti per un osservatore EuroStoxx. Cerca al massimo due fonti di oggi. '
              'Non inventare cause, non dare ordini di trading e non prevedere rendimenti. '
              'Se non trovi una causa verificata dichiaralo. Includi i link alle fonti. Dati: '
              + json.dumps(events, ensure_ascii=False))
    try:
        response = OpenAI(max_retries=0, timeout=45).responses.create(
            model=os.getenv('OPENAI_ANALYSIS_MODEL', 'gpt-6-luna'),
            input=prompt, tools=[{'type': 'web_search', 'search_context_size': 'low'}],
            max_tool_calls=2, max_output_tokens=1200)
        usage = response.usage
        monthly['input_tokens'] = monthly.get('input_tokens', 0) + getattr(usage, 'input_tokens', 0)
        monthly['output_tokens'] = monthly.get('output_tokens', 0) + getattr(usage, 'output_tokens', 0)
        save(state)
        if response.status != 'completed' or not response.output_text:
            return 'Approfondimento AI incompleto; nessun nuovo tentativo a pagamento.'
        return response.output_text[:2000]
    except Exception as exc:
        if getattr(exc, 'code', None) in ('insufficient_quota', 'credit_balance_exhausted') or 'credit_balance_exhausted' in str(exc):
            ledger['quota_exhausted_at'] = now.isoformat()
        save(state)
        print('AI non disponibile:', type(exc).__name__)
        return 'Approfondimento AI non disponibile; controlli numerici attivi.'


def run(*, asia_only=False):
    from radar import _load_state, _save_state, _scan_news, send_message
    now = datetime.now(ROME)
    state = _load_state()
    _scan_news(state)
    quotes, missing = collect(now, asia_only)
    state['euro_sensor_status'] = {'time': now.isoformat(), 'quotes': quotes, 'missing': missing}
    sent = state.setdefault('euro_events', {})
    state['euro_events'] = sent = {k: v for k, v in sent.items() if k.startswith(str(now.date()) + '|')}
    events = []
    for name, q in quotes.items():
        key = anomaly_key(name, q, now)
        if key and key not in sent:
            events.append({'name': name, 'key': key, **q})
    if events:
        # Reserve explanations independently of delivery, so a Telegram retry cannot buy it again.
        explained = state.setdefault('euro_explanations', {})
        fresh = [e for e in events if e['key'] not in explained]
        explanation = ''
        if fresh:
            for e in fresh:
                explained[e['key']] = 'Approfondimento già tentato; nessun doppione a pagamento.'
            _save_state(state)
            explanation = explain(fresh, state, _save_state, now)
            for e in fresh:
                explained[e['key']] = explanation
            _save_state(state)
        elif events:
            explanation = explained.get(events[0]['key'], '')
        body = '📡 RADAR EUROSTOXX — ANOMALIE\n\n' + '\n\n'.join(
            f"{e['name']}: {e['change']:+.2f} {e['unit']} | valore {e['price']:.3f}\n"
            f"Dato: {e['time']}\n{e.get('note', '')}\n{e['source']}" for e in events)
        if explanation:
            body += '\n\n' + explanation
        if missing:
            body += '\n\nSensori non disponibili: ' + ', '.join(missing)
        send_message(body[:4000])
        for e in events:
            # Higher tier includes lower ones to avoid lower-level notifications after a reversal in magnitude.
            prefix, tier = e['key'].rsplit('|', 1)
            for lower in (1, 1.5, 2, 3, 5, 10, 15, 25):
                if lower <= float(tier):
                    sent[f'{prefix}|{lower}'] = now.isoformat()
    if missing and state.get('euro_health_day') != str(now.date()):
        send_message('⚙️ RADAR EUROSTOXX — COPERTURA PARZIALE\nSensori non disponibili: '
                     + ', '.join(missing) + '\nNessun valore è stato sostituito o stimato con AI.')
        state['euro_health_day'] = str(now.date())
    # Prune cached explanations while retaining the monthly attempt ledger.
    state['euro_explanations'] = {k: v for k, v in state.get('euro_explanations', {}).items()
                                  if k.startswith(str(now.date()) + '|')}
    _save_state(state)
    print(f'EuroStoxx: {len(quotes)} sensori disponibili; {len(events)} anomalie; {len(missing)} mancanti')
