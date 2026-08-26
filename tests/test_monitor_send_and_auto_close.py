# -*- coding: utf-8 -*-
"""Monitor DM reuse + headed-verify auto-close helpers."""

from aisec_agent.web import session_rag_chat as sr


def test_task_runtime_preserves_use_alive_monitor_flag():
    fields = sr._dm_task_submit_runtime_fields({
        'account_cookie': 'sessionid=abc',
        'account_id': '9',
        'target_profile_url': 'https://www.douyin.com/user/x',
        'use_alive_monitor': True,
        'headless': False,
    })
    assert fields['use_alive_monitor'] == 'true'


def test_schedule_auto_close_ignores_non_positive_delay():
    # Should not raise when keep_browser_open is false / delay is 0.
    sr._dm_schedule_auto_close_session(
        'edge',
        {'keep_browser_open': True, 'persistent_context': True},
        0,
    )
    sr._dm_schedule_auto_close_session(
        'edge',
        {'keep_browser_open': False, 'persistent_context': True},
        15000,
    )


def test_verify_passes_auto_close_after_ms(monkeypatch):
    from aisec_agent.web import douyin_account_verify as verify_mod

    captured = {}

    def fake_apply(payload, executor=None):
        captured['payload'] = payload
        return {
            'success': True,
            'opened': True,
            'requires_login': False,
            'account_type_skipped': False,
            'account_profile': {'account_type': 'personal', 'account_type_label': '普通号'},
        }

    scheduled = {}

    monkeypatch.setattr(sr, 'build_douyin_account_cookie_apply_response', fake_apply)
    monkeypatch.setattr(
        sr,
        '_dm_schedule_auto_close_session',
        lambda browser, options, delay: scheduled.setdefault('delay', delay),
    )
    monkeypatch.setattr(sr, '_dm_cancel_auto_close_session', lambda *a, **k: None)

    verify_mod.build_douyin_account_verify_response({
        'account_cookie': 'sessionid=abc',
        'browser_name': 'edge',
        'headless': False,
        'keep_browser_open': True,
        'auto_close_after_ms': 15000,
    })
    assert captured['payload']['auto_close_after_ms'] == 15000
    assert scheduled.get('delay') == 15000
