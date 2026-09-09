import json
from types import SimpleNamespace

from netwaive.contracts import ChangePlan
from netwaive import views


class Session(dict):
    modified = False


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status
        self.headers = {}
    def __setitem__(self, key, value): self.headers[key] = value


def unwrap(view):
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    return view


def request_with_pending(tab_id, conversation_id, user):
    session = Session()
    request = SimpleNamespace(session=session, user=user, GET={}, path="/plugins/netwaive/api/chat/", method="POST")
    state = views._load_state(request)
    active = views._active_session(state)
    state.setdefault("tab_sessions", {})[tab_id] = active["id"]
    plan = ChangePlan(summary="test", operations=[{"method":"DELETE","endpoint":"/dcim/sites/1/","data":{}}])
    active["pending_write"] = {"message":"test", "change_plan":plan.model_dump()}
    views._save_state(request, state)
    request.body = json.dumps({"message":"oui", "tab_id":tab_id, "conversation_id":conversation_id, "approve_pending":True, "plan_id":views._plan_id(plan)}).encode()
    return request, active, plan


def test_approval_rechecks_rbac(monkeypatch):
    user = SimpleNamespace(is_superuser=False, groups=SimpleNamespace(filter=lambda **kwargs: []))
    tab = "44444444-4444-4444-8444-444444444444"
    request, active, _ = request_with_pending(tab, "", user)
    request.body = json.dumps({"message":"oui", "tab_id":tab, "conversation_id":active["id"], "approve_pending":True}).encode()
    monkeypatch.setattr(views, "JsonResponse", Response)
    monkeypatch.setattr(views, "_can_write", lambda user: False)
    endpoint = unwrap(views.chat_api)
    response = endpoint(request)
    assert response.status_code == 403
    assert response.payload["code"] == "write_forbidden"


def test_approval_rejects_conversation_mismatch(monkeypatch):
    user = SimpleNamespace(is_superuser=True, groups=SimpleNamespace(filter=lambda **kwargs: []))
    tab = "55555555-5555-4555-8555-555555555555"
    request, _, _ = request_with_pending(tab, "wrong", user)
    monkeypatch.setattr(views, "JsonResponse", Response)
    response = unwrap(views.chat_api)(request)
    assert response.status_code == 409
    assert response.payload["code"] == "stale_conversation"


def test_approval_rejects_wrong_plan_id(monkeypatch):
    user = SimpleNamespace(is_superuser=True, groups=SimpleNamespace(filter=lambda **kwargs: []))
    tab = "66666666-6666-4666-8666-666666666666"
    request, active, _ = request_with_pending(tab, "", user)
    request.body = json.dumps({"message":"oui", "tab_id":tab, "conversation_id":active["id"], "approve_pending":True, "plan_id":"bad"}).encode()
    monkeypatch.setattr(views, "JsonResponse", Response)
    monkeypatch.setattr(views, "_can_write", lambda user: True)
    monkeypatch.setattr(views, "build_agent", lambda settings: None)
    monkeypatch.setattr(views, "_agent_settings", lambda: None)
    response = unwrap(views.chat_api)(request)
    assert response.status_code == 409
    assert response.payload["code"] == "stale_plan"
