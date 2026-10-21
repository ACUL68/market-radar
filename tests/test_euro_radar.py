from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import euro_radar as e
import radar

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=e.ROME)


def quote(move=1.2, unit='%'):
    return dict(price=6200, change=move, time=NOW.isoformat(), source='https://example.com', unit=unit)


def test_default_mode_never_runs_paid_discovery(monkeypatch):
    monkeypatch.delenv('RADAR_MODE', raising=False)
    with patch.object(e, 'run') as run, patch.object(radar, 'discover_market') as discovery:
        radar.run_radar()
        radar.run_equity_rescan()
        radar.run_asia_early_warning()
    assert run.call_count == 3
    discovery.assert_not_called()


def test_numeric_thresholds():
    assert e.anomaly_key('EuroStoxx 50', quote(.9), NOW) is None
    assert e.anomaly_key('WTI future', quote(1.9), NOW) is None
    assert e.anomaly_key('VSTOXX', quote(4.9), NOW) is None
    assert e.anomaly_key('Bund 10Y', quote(9.9, 'pb'), NOW) is None
    assert e.anomaly_key('Bund 10Y', quote(-15.1, 'pb'), NOW).endswith('DOWN|15')


def test_monthly_limit_prevents_network(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test')
    state = {'euro_ai_usage': {'2026-10': {'attempts': 30, 'days': {}}}}
    with patch('openai.OpenAI') as api:
        assert 'Limite' in e.explain([quote()], state, Mock(), NOW)
    api.assert_not_called()


def test_daily_limit_and_missing_key_prevent_network(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test')
    state = {'euro_ai_usage': {'2026-10': {'attempts': 2, 'days': {'2026-10-21': 2}}}}
    with patch('openai.OpenAI') as api:
        assert 'Limite' in e.explain([quote()], state, Mock(), NOW)
        monkeypatch.delenv('OPENAI_API_KEY')
        e.explain([quote()], {}, Mock(), NOW)
    api.assert_not_called()


def test_attempt_reserved_even_on_failure(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test')
    state, snapshots = {}, []
    import copy
    def save(s): snapshots.append(copy.deepcopy(s))
    with patch('openai.OpenAI') as api:
        api.return_value.responses.create.side_effect = RuntimeError('offline')
        e.explain([quote()], state, save, NOW)
        args = api.return_value.responses.create.call_args.kwargs
        assert args['max_tool_calls'] == 2
        assert args['max_output_tokens'] == 1200
        api.assert_called_once_with(max_retries=0, timeout=45)
    assert snapshots[0]['euro_ai_usage']['2026-10']['attempts'] == 1
    assert state['euro_ai_usage']['2026-10']['attempts'] == 1


def test_yahoo_rejects_old_quote_and_uses_previous_session():
    meta = dict(regularMarketPrice=102, chartPreviousClose=100,
                regularMarketTime=NOW.timestamp())
    response = Mock()
    response.json.return_value = {'chart': {'result': [{'meta': meta}]}}
    with patch.object(e.requests, 'get', return_value=response) as get:
        assert e.yahoo_quote('ES=F', NOW)['change'] == 2.0000000000000018
        assert get.call_args.kwargs['params']['range'] == '1d'
        meta['regularMarketTime'] = (NOW - timedelta(days=1)).timestamp()
        import pytest
        with pytest.raises(ValueError): e.yahoo_quote('ES=F', NOW)


def test_bond_conversion_to_basis_points():
    response = Mock()
    response.json.return_value = {'data': [
        {'s': 'TVC:DE10Y', 'd': ['DE10Y', 3.60, .15, NOW.timestamp(), 'streaming']},
        {'s': 'TVC:IT10Y', 'd': ['IT10Y', 4, .3, (NOW-timedelta(days=1)).timestamp(), 'streaming']}]}
    with patch.object(e.requests, 'post', return_value=response):
        quotes = e.bond_quotes(NOW)
    assert round(quotes['Bund 10Y']['change'], 6) == 15
    assert 'BTP 10Y' not in quotes


def test_quiet_scan_never_calls_ai():
    state = {}
    with patch.object(radar, '_load_state', return_value=state), patch.object(radar, '_save_state'), \
         patch.object(radar, '_scan_news'), patch.object(radar, 'send_message') as send, \
         patch.object(e, 'collect', return_value=({'EuroStoxx 50': quote(.1)}, [])), \
         patch.object(e, 'explain') as ai:
        e.run()
    ai.assert_not_called()
    send.assert_not_called()


def test_delivery_failure_does_not_repeat_paid_explanation():
    state = {}
    with patch.object(radar, '_load_state', return_value=state), patch.object(radar, '_save_state'), \
         patch.object(radar, '_scan_news'), patch.object(radar, 'send_message') as send, \
         patch.object(e, 'collect', return_value=({'EuroStoxx 50': quote(1.1)}, [])), \
         patch.object(e, 'explain', return_value='Cause verificate') as ai:
        import pytest
        send.side_effect = RuntimeError('telegram offline')
        with pytest.raises(RuntimeError): e.run()
        send.side_effect = None
        e.run()
        e.run()
        ai.assert_called_once()
        assert send.call_count == 2


def test_vstoxx_does_not_substitute_vix():
    import pytest
    with patch.object(e.requests, 'get') as get:
        with pytest.raises(ValueError): e.vstoxx_quote(NOW)
    get.assert_not_called()


def test_vstoxx_official_file_uses_indexvalue_column():
    response = Mock(text='Date;Symbol;Indexvalue\n20.10.2026;V2TX;20.0\n21.10.2026;V2TX;22.0\n')
    with patch.object(e.requests, 'get', return_value=response):
        q = e.vstoxx_quote(NOW.replace(hour=22))
    assert q['price'] == 22
    assert round(q['change'], 5) == 10
