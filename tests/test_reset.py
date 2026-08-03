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


def test_dedicated_reset_route_exists():
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
