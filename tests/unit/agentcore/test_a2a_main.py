"""Unit tests for the seller inbound A2A server (a2a_main.py).

Offline: the shared invocation core (_handle_invocation) is monkeypatched, so
these tests exercise only the A2A JSON-RPC wire contract and the agent card,
never a real crew/LLM call.
"""

import sys
from unittest.mock import MagicMock

import pytest

# BedrockAgentCoreApp is imported transitively via http_main; stub it so the
# import chain works offline (mirrors tests/unit/agentcore/test_crew_tools.py).
sys.modules.setdefault("bedrock_agentcore", MagicMock())
sys.modules.setdefault("bedrock_agentcore.runtime", MagicMock())

from starlette.testclient import TestClient  # noqa: E402

import ad_seller.interfaces.agentcore.a2a_main as a2a  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    async def fake_handle(payload):
        # Echo the prompt back in the seller's chat-shaped result envelope.
        return {
            "response": f"seller saw: {payload.get('prompt')}",
            "metadata": {"session_id": payload.get("session_id")},
        }

    # _handle_message_send imports _handle_invocation lazily from http_main.
    import ad_seller.interfaces.agentcore.http_main as http_main

    monkeypatch.setattr(http_main, "_handle_invocation", fake_handle)
    return TestClient(a2a.build_app())


def _jsonrpc(text: str, context_id: str | None = None) -> dict:
    params: dict = {"message": {"parts": [{"kind": "text", "text": text}]}}
    if context_id:
        params["contextId"] = context_id
    return {"jsonrpc": "2.0", "id": "req-1", "method": "message/send", "params": params}


class TestAgentCard:
    def test_agent_card_served(self, client):
        resp = client.get("/a2a/seller/.well-known/agent-card.json")
        assert resp.status_code == 200
        card = resp.json()
        assert card["protocolVersion"] == "0.3.0"
        assert "text" in card["defaultInputModes"]
        assert {s["id"] for s in card["skills"]} == {"discovery", "pricing", "negotiation"}

    def test_ping(self, client):
        resp = client.get("/ping")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


class TestMessageSend:
    def test_happy_path_returns_text_and_data_parts(self, client):
        resp = client.post("/a2a/seller/jsonrpc", json=_jsonrpc("show me all ctv inventory"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["jsonrpc"] == "2.0"
        assert body["id"] == "req-1"
        result = body["result"]
        assert result["status"]["state"] == "completed"
        kinds = {p["kind"] for p in result["parts"]}
        assert kinds == {"text", "data"}
        text_part = next(p for p in result["parts"] if p["kind"] == "text")
        assert "show me all ctv inventory" in text_part["text"]

    def test_context_id_threads_as_session(self, client):
        resp = client.post("/a2a/seller/jsonrpc", json=_jsonrpc("hi", context_id="ctx-42"))
        result = resp.json()["result"]
        assert result["contextId"] == "ctx-42"
        data_part = next(p for p in result["parts"] if p["kind"] == "data")
        assert data_part["data"]["metadata"]["session_id"] == "ctx-42"

    def test_method_not_found(self, client):
        resp = client.post(
            "/a2a/seller/jsonrpc",
            json={"jsonrpc": "2.0", "id": "x", "method": "tasks/cancel", "params": {}},
        )
        body = resp.json()
        assert body["error"]["code"] == -32601

    def test_missing_text_is_invalid_request(self, client):
        resp = client.post(
            "/a2a/seller/jsonrpc",
            json={"jsonrpc": "2.0", "id": "x", "method": "message/send", "params": {"message": {"parts": []}}},
        )
        body = resp.json()
        assert body["error"]["code"] == -32600

    def test_bad_envelope_is_invalid_request(self, client):
        resp = client.post("/a2a/seller/jsonrpc", json={"not": "jsonrpc"})
        assert resp.json()["error"]["code"] == -32600

    def test_invocations_alias_reaches_same_handler(self, client):
        # AgentCore's data plane always POSTs /invocations; it must reach the
        # same message/send handler as /a2a/seller/jsonrpc.
        resp = client.post("/invocations", json=_jsonrpc("list ctv inventory"))
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["status"]["state"] == "completed"


class TestHelpers:
    def test_extract_text_joins_multiple_parts(self):
        params = {"message": {"parts": [{"kind": "text", "text": "a"}, {"kind": "text", "text": "b"}]}}
        assert a2a._extract_text(params) == "a\nb"

    def test_extract_text_falls_back_to_prompt(self):
        assert a2a._extract_text({"prompt": "p"}) == "p"

    def test_result_to_parts_dict_response(self):
        parts = a2a._result_to_parts({"response": "hello", "metadata": {}})
        assert parts[0] == {"kind": "text", "text": "hello"}
        assert parts[1]["kind"] == "data"
