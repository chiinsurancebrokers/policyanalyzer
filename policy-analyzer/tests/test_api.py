import os
from fastapi.testclient import TestClient
import api

client = TestClient(api.app)

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

def test_analyze_requires_bridge_key(monkeypatch):
    monkeypatch.setenv("HAL_POLICY_ANALYZER_API_KEY", "secret")
    r = client.post("/api/v1/analyze", json={"quotation_text":"x"})
    assert r.status_code == 401

def test_analyze_returns_structured_result(monkeypatch):
    monkeypatch.setenv("HAL_POLICY_ANALYZER_API_KEY", "secret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(api, "analyze_with_ai", lambda text, digest: {"confidence":"medium","categories":{"inpatient":"Covered"}})
    r = client.post(
        "/api/v1/analyze",
        headers={"x-hal-policy-analyzer-key":"secret"},
        json={"provider_label":"Bupa","quotation_text":"certificate","wording_text":"wording","benefit_rows":[{"code":"hospital_accommodation","label":"Hospital accommodation"}]},
    )
    assert r.status_code == 200
    body=r.json()
    assert body["categories"]["inpatient"]=="Covered"
    assert body["hal_benefit_rows"]==[]
