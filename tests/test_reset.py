from types import SimpleNamespace

from netwaive import urls, views


class Session(dict):
    modified = False


def dirty_request():
    state = {
        "active_session_id": "old-1",
        "sessions": [
            {
                "id": "old-1",
                "title": "Old",
                "history": [{"role": "user", "text": "secret context"}],
                "pending_write": {"goal": "mutate", "calls": [{"id": "call-1"}]},
                "allow_session": True,
            },
            {
                "id": "old-2",
                "title": "Other",
                "history": [{"role": "assistant", "text": "stale"}],
                "pending_write": {"goal": "other"},
                "allow_session": True,
            },
        ],
        "ui": {"open": True, "layout": "docked"},
    }
    return SimpleNamespace(session=Session({views.SESSION_KEY: state}))


def assert_fresh(state):
    assert len(state["sessions"]) == 1
    active = state["sessions"][0]
    assert active["history"] == []
    assert active["pending_write"] is None
    assert active.get("allow_session") is not True
    assert state["active_session_id"] == active["id"]
    assert active["id"] not in {"old-1", "old-2"}


def test_server_reset_purges_all_history_pending_and_session_flags():
    request = dirty_request()
    state = views._purge_agent_state(request)
    assert_fresh(state)
    assert_fresh(request.session[views.SESSION_KEY])
    assert request.session.modified is True
    assert "secret context" not in str(request.session)
    assert "call-1" not in str(request.session)


def test_reset_has_no_chat_command_path():
    assert not hasattr(views, "RESET_COMMANDS")
    assert not hasattr(views, "_reset_command_payload")


def test_stale_chat_generation_is_rejected_after_reset():
    request = dirty_request()
    state = views._load_state(request)
    views._save_state(request, state)
    old_generation = state["generation"]
    views._purge_agent_state(request)
    assert not views._generation_is_current(request, old_generation)


def test_inflight_chat_cannot_restore_state_after_reset(monkeypatch):
    import json
    from netwaive.models import AgentResponse

    class Response:
        def __init__(self, payload, status=200):
            self.payload, self.status_code, self.headers = payload, status, {}
        def __setitem__(self, key, value): self.headers[key] = value
        def __getitem__(self, key): return self.headers[key]

    request = dirty_request()
    request.body = json.dumps({"message":"requête lente"}).encode()
    request.user = SimpleNamespace()

    class Agent:
        def __init__(self, settings): pass
        @staticmethod
        def _detect_language(message): return "fr"
        def run(self, message, history=None):
            views._purge_agent_state(request)
            return AgentResponse(message="ancienne réponse")

    monkeypatch.setattr(views, "JsonResponse", Response)
    monkeypatch.setattr(views, "NetBoxAgent", Agent)
    monkeypatch.setattr(views, "_agent_settings", lambda: None)
    endpoint = views.chat_api
    while hasattr(endpoint, "__wrapped__"): endpoint = endpoint.__wrapped__
    response = endpoint(request)
    assert response.status_code == 409
    assert response.payload["reset"] is True
    assert "ancienne réponse" not in str(request.session)
    assert_fresh(request.session[views.SESSION_KEY])


def test_reset_endpoint_returns_fresh_public_state(monkeypatch):
    class Response:
        status_code = 200
        def __init__(self, payload):
            self.payload = payload
            self.headers = {}
        def __setitem__(self, key, value):
            self.headers[key] = value
        def __getitem__(self, key):
            return self.headers[key]
    monkeypatch.setattr(views, "JsonResponse", Response)
    request = dirty_request()
    endpoint = views.reset_api
    while hasattr(endpoint, "__wrapped__"):
        endpoint = endpoint.__wrapped__
    response = endpoint(request)
    payload = response.payload
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert payload["history"] == []
    assert payload["pending_write"] is None
    assert_fresh(request.session[views.SESSION_KEY])

    routes = {str(pattern.pattern) for pattern in urls.urlpatterns}
    assert "api/reset/" in routes


def test_frontend_waits_for_backend_before_clearing_dom():
    from pathlib import Path
    root = Path(__file__).parents[1] / "src" / "netwaive" / "static" / "netwaive"
    chat = (root / "chat.js").read_text()
    floating = (root / "floating.js").read_text()
    chat_handler = chat[chat.index("clearButton?.addEventListener"):chat.index("fetch(\"/plugins/netwaive/api/history/\"")]
    floating_handler = floating[floating.index("clearBtn?.addEventListener"):floating.index("dockBtn?.addEventListener")]
    assert chat_handler.index('fetch("/plugins/netwaive/api/reset/"') < chat_handler.index("messages.replaceChildren()")
    assert floating_handler.index("fetch(api.reset") < floating_handler.index("renderConversation()")
    assert "activeChatController?.abort()" in chat_handler
    assert "activeChatController?.abort()" in floating_handler
    assert "resetEpoch += 1" in chat_handler
    assert "resetEpoch += 1" in floating_handler
