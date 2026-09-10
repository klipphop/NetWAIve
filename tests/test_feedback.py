import json
from types import SimpleNamespace

from netwaive import views


class Session(dict):
    modified = False


class Manager:
    called = None
    @classmethod
    def update_or_create(cls, **kwargs):
        cls.called = kwargs
        return SimpleNamespace(response_id=kwargs["response_id"], rating=kwargs["defaults"]["rating"]), True


class Response:
    def __init__(self, payload, status=200): self.payload, self.status_code = payload, status
    def __setitem__(self, key, value): pass


def unwrap(view):
    while hasattr(view, "__wrapped__"): view = view.__wrapped__
    return view


def test_feedback_accepts_owned_response(monkeypatch):
    tab = "77777777-7777-4777-8777-777777777777"
    session = Session()
    request = SimpleNamespace(session=session, user=SimpleNamespace(pk=1), GET={}, path="/feedback", method="POST")
    state = views._load_state(request); active = views._active_session(state); state.setdefault("tab_sessions", {})[tab] = active["id"]
    active["history"] = [{"role":"user","text":"question"},{"role":"assistant","text":"answer","response_id":"88888888-8888-4888-8888-888888888888"}]
    views._save_state(request,state)
    request.body=json.dumps({"tab_id":tab,"conversation_id":active["id"],"response_id":"88888888-8888-4888-8888-888888888888","rating":"down","reason":"incomplet","expected_answer":"mieux"}).encode()
    monkeypatch.setattr(views, "JsonResponse", Response)
    monkeypatch.setattr(views, "_feedback_model", lambda: SimpleNamespace(objects=Manager))
    response=unwrap(views.feedback_api)(request)
    assert response.status_code == 200
    assert Manager.called["defaults"]["prompt"] == "question"
    assert Manager.called["defaults"]["answer"] == "answer"


def test_feedback_rejects_unknown_response(monkeypatch):
    tab="99999999-9999-4999-8999-999999999999"; session=Session(); request=SimpleNamespace(session=session,user=SimpleNamespace(pk=1),GET={},path="/feedback",method="POST")
    state=views._load_state(request); active=views._active_session(state); state.setdefault("tab_sessions",{})[tab]=active["id"]; views._save_state(request,state)
    request.body=json.dumps({"tab_id":tab,"conversation_id":active["id"],"response_id":"88888888-8888-4888-8888-888888888888","rating":"up"}).encode()
    monkeypatch.setattr(views,"JsonResponse",Response)
    assert unwrap(views.feedback_api)(request).status_code == 404
