"""
Security-fix regression tests.

Covers:
  - export/exporter.sanitize_formula_injection (CSV/Excel formula
    injection mitigation)
  - utils/security.check_api_key / check_rate_limit (the request
    gate wired into app.py's before_request)

These build a minimal Flask app directly around utils/security's
functions rather than importing app.py, so they run without needing
flask-cors installed (see testing/test_omixa.py's note on why that
dependency isn't reachable in this sandbox). Anywhere flask-cors
*is* available, app.py itself wires the same two functions in the
same order, see app.py's `_security_gate`.

Run with:
    pip install pytest --break-system-packages   # or in a venv
    pytest testing/test_omixa_security.py -v
"""

import importlib
import os

import pandas as pd
import pytest
from flask import Flask, jsonify

from export.exporter import sanitize_formula_injection


# --------------------------------------------------------------------
# Formula injection (CSV/Excel export)
# --------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    "=cmd|'/c calc'!A1",
    "+1+1",
    "-5 apples",
    "@SUM(A1)",
    "\ttabbed",
])
def test_formula_trigger_chars_get_quote_prefixed(payload):
    df = pd.DataFrame({"col": [payload, "safe"]})
    out = sanitize_formula_injection(df)
    assert out["col"].iloc[0] == "'" + payload
    assert out["col"].iloc[1] == "safe"


def test_safe_text_with_internal_dash_untouched():
    # Only a LEADING trigger character is dangerous, "5-10 units"
    # doesn't start with one, so it must pass through unchanged.
    df = pd.DataFrame({"col": ["5-10 units", "call 555-1234"]})
    out = sanitize_formula_injection(df)
    assert list(out["col"]) == ["5-10 units", "call 555-1234"]


def test_numeric_and_null_values_untouched():
    df = pd.DataFrame({"amount": [1, -5, 3], "note": ["x", None, "y"]})
    out = sanitize_formula_injection(df)
    assert list(out["amount"]) == [1, -5, 3]  # genuine negative numbers, not text, never touched
    assert pd.isna(out["note"].iloc[1])


def test_sanitization_runs_automatically_on_export(tmp_path):
    from export.exporter import export_dataframe
    df = pd.DataFrame({"col": ["=2+2", "safe"]})
    out_path = tmp_path / "out.csv"
    export_dataframe(df, str(out_path), ext="csv")
    content = out_path.read_text()
    assert "'=2+2" in content
    assert content.count("=2+2") == 1  # only the defused, quote-prefixed form appears


# --------------------------------------------------------------------
# API key + rate limit gate
# --------------------------------------------------------------------

def _build_test_app(monkeypatch, api_key=None, rate_limit=0):
    """A minimal Flask app wired the same way app.py wires
    utils/security, without needing flask-cors."""
    monkeypatch.setenv("OMIXA_API_KEY", api_key or "")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", str(rate_limit))
    import config
    importlib.reload(config)
    import utils.security as security
    importlib.reload(security)  # picks up the reloaded Config

    app = Flask(__name__)

    @app.before_request
    def _gate():
        limited = security.check_rate_limit()
        if limited:
            return limited
        return security.check_api_key()

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/api/report/<job_id>")
    def report(job_id):
        return jsonify({"job_id": job_id}), 200

    return app


def test_auth_disabled_by_default(monkeypatch):
    app = _build_test_app(monkeypatch, api_key=None)
    client = app.test_client()
    r = client.get("/api/report/abc")
    assert r.status_code == 200


def test_missing_key_rejected_when_configured(monkeypatch):
    app = _build_test_app(monkeypatch, api_key="secret123")
    client = app.test_client()
    r = client.get("/api/report/abc")
    assert r.status_code == 401


def test_wrong_key_rejected(monkeypatch):
    app = _build_test_app(monkeypatch, api_key="secret123")
    client = app.test_client()
    r = client.get("/api/report/abc", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_correct_key_accepted(monkeypatch):
    app = _build_test_app(monkeypatch, api_key="secret123")
    client = app.test_client()
    r = client.get("/api/report/abc", headers={"X-API-Key": "secret123"})
    assert r.status_code == 200


def test_health_check_always_exempt_from_auth(monkeypatch):
    app = _build_test_app(monkeypatch, api_key="secret123")
    client = app.test_client()
    r = client.get("/api/health")
    assert r.status_code == 200


def test_rate_limit_blocks_after_threshold(monkeypatch):
    app = _build_test_app(monkeypatch, api_key=None, rate_limit=2)
    client = app.test_client()
    assert client.get("/api/report/abc").status_code == 200
    assert client.get("/api/report/abc").status_code == 200
    r = client.get("/api/report/abc")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_rate_limit_disabled_when_zero(monkeypatch):
    app = _build_test_app(monkeypatch, api_key=None, rate_limit=0)
    client = app.test_client()
    for _ in range(10):
        assert client.get("/api/report/abc").status_code == 200


def test_health_check_exempt_from_rate_limit(monkeypatch):
    app = _build_test_app(monkeypatch, api_key=None, rate_limit=1)
    client = app.test_client()
    for _ in range(5):
        assert client.get("/api/health").status_code == 200
