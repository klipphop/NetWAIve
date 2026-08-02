from __future__ import annotations

import json
import uuid
from typing import Any

from django.conf import settings as django_settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from django.utils.translation import get_language

from .agent import NetBoxAgent
from .config import Settings
from .models import PendingToolCall

SESSION_KEY = "netwaive_state"
MAX_HISTORY = 100
MAX_SESSIONS = 8


def _plugin_config() -> dict[str, Any]:
    configs = getattr(django_settings, "PLUGINS_CONFIG", {}) or {}
    return dict(configs.get("netwaive", {}) or {})


def _agent_settings() -> Settings:
    cfg = _plugin_config()
    explicit = {
        key: cfg[key]
        for key in (
            "netbox_url", "netbox_token", "netbox_verify_ssl", "llm_base_url",
            "llm_api_key", "llm_model", "llm_timeout", "max_agent_turns",
            "max_search_results",
        )
        if cfg.get(key) not in (None, "")
    }
    return Settings(**explicit)


def _default_state() -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    return {
        "sessions": [{"id": session_id, "title": "Session 1", "history": [], "pending_write": None}],
        "active_session_id": session_id,
        "ui": {"open": True, "layout": "docked", "width": 320},
    }


def _load_state(request) -> dict[str, Any]:
    state = request.session.get(SESSION_KEY)
    if not isinstance(state, dict) or not isinstance(state.get("sessions"), list):
        state = _default_state()
    if not state["sessions"]:
        state = _default_state()
    return state


def _save_state(request, state: dict[str, Any]) -> None:
    request.session[SESSION_KEY] = state
    request.session.modified = True


def _active_session(state: dict[str, Any], requested_id: str | None = None) -> dict[str, Any]:
    target = requested_id or state.get("active_session_id")
    for session in state["sessions"]:
        if session.get("id") == target:
            state["active_session_id"] = target
            return session
    session = state["sessions"][0]
    state["active_session_id"] = session["id"]
    return session


def _state_payload(state: dict[str, Any]) -> dict[str, Any]:
    active = _active_session(state)
    pending = active.get("pending_write") if isinstance(active.get("pending_write"), dict) else None
    public_pending = {"message": pending.get("message"), "calls": pending.get("calls", [])} if pending else None
    return {
        "sessions": [{"id": item["id"], "title": item.get("title", "Session")} for item in state["sessions"]],
        "active_session_id": active["id"],
        "history": active.get("history", []),
        "pending_write": public_pending,
        "ui": state.get("ui", {}),
    }


def _can_write(user) -> bool:
    cfg = _plugin_config()
    if not bool(cfg.get("write_enabled", False)):
        return False
    return bool(user.is_superuser or user.groups.filter(name="netbox-llm-writers").exists())


def _append_history(session: dict[str, Any], role: str, text: str) -> None:
    history = session.setdefault("history", [])
    history.append({"role": role, "text": text})
    session["history"] = history[-MAX_HISTORY:]


@login_required
def chat(request):
    english = str(getattr(request, "LANGUAGE_CODE", None) or get_language() or "").lower().startswith("en")
    banner = "NetBox Assistant (Beta - under active development). Read/write based on global configuration. Changes require your confirmation." if english else "Assistant NetBox (Beta - en cours de développement). Lecture/écriture selon la configuration globale. Les modifications requièrent votre confirmation."
    return render(request, "netwaive/chat.html", {"plugin_version": "0.4.5", "banner": banner, "widget_title": "NetBox Assistant (Beta)" if english else "Assistant NetBox (Beta)"})


@login_required
@require_GET
def health_api(request):
    try:
        configured = _agent_settings()
        NetBoxAgent(configured)
        return JsonResponse({
            "configured": True,
            "model": configured.llm_model,
            "pynetbox_ready": True,
            "write_enabled": _can_write(request.user),
        })
    except Exception as exc:
        return JsonResponse({"configured": False, "error": str(exc), "pynetbox_ready": False})


@login_required
@require_GET
def history_api(request):
    state = _load_state(request)
    _save_state(request, state)
    return JsonResponse(_state_payload(state))


@login_required
@require_POST
def chat_api(request):
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Corps JSON invalide."}, status=400)
    message = str(body.get("message") or "").strip()
    if not message:
        return JsonResponse({"error": "Message vide."}, status=400)

    state = _load_state(request)
    active = _active_session(state, str(body.get("conversation_id") or "") or None)
    pending = active.get("pending_write") if isinstance(active.get("pending_write"), dict) else None
    agent = NetBoxAgent(_agent_settings())
    normalized = message.lower().strip()
    language = NetBoxAgent._detect_language(message)
    approved = bool(body.get("approve_pending"))
    approval_scope = str(body.get("approval_scope") or "once")
    execution_status = "none"

    if pending and not (approved or normalized in {"oui", "o", "confirme", "je confirme", "valide", "je valide", "non", "n", "annule", "annuler"}):
        active["pending_write"] = None
        pending = None

    if pending and normalized in {"non", "n", "annule", "annuler"}:
        active["pending_write"] = None
        execution_status = "cancelled"
        answer = "Action cancelled. No NetBox write was executed." if language == "en" else "Action annulée. Aucune écriture NetBox n’a été exécutée."
    elif pending and (approved or normalized in {"oui", "o", "confirme", "je confirme", "valide", "je valide"}):
        if not _can_write(request.user):
            active["pending_write"] = None
            answer = "Writes are not authorized for this NetBox account." if language == "en" else "Écriture non autorisée pour ce compte NetBox."
        else:
            if approval_scope == "session":
                active["allow_session"] = True
            else:
                active.pop("allow_session", None)
            calls = [PendingToolCall.model_validate(item) for item in pending.get("calls", [])]
            pending_history = pending.get("history") if isinstance(pending.get("history"), list) else active.get("history", [])
            result = agent.confirm(str(pending.get("message") or ""), calls, history=pending_history)
            answer = result.message
            execution_status = "success" if len(result.tool_results) == len(calls) and all(item.ok for item in result.tool_results) else "failed"
            if execution_status == "success" and result.pending_confirmation:
                active["pending_write"] = {
                    "message": str(pending.get("message") or ""),
                    "calls": [item.model_dump() for item in result.pending_confirmation],
                    "history": pending_history,
                }
            else:
                active["pending_write"] = None
    else:
        recent_history = list(active.get("history", []))[-16:]
        result = agent.run(message, history=recent_history)
        answer = result.message
        if result.pending_confirmation:
            if active.get("allow_session") and _can_write(request.user):
                executed = agent.confirm(message, result.pending_confirmation, history=recent_history)
                answer = executed.message
                execution_status = "success" if len(executed.tool_results) == len(result.pending_confirmation) and all(item.ok for item in executed.tool_results) else "failed"
                active["pending_write"] = None
            elif not _can_write(request.user):
                answer = "This request requires a write, but this account is not authorized." if language == "en" else "Cette demande nécessite une écriture, mais ce compte n’est pas autorisé."
                active["pending_write"] = None
            else:
                active["pending_write"] = {
                    "message": message,
                    "calls": [item.model_dump() for item in result.pending_confirmation],
                    "history": recent_history,
                }
        else:
            active["pending_write"] = None

    if len(active.get("history", [])) == 0:
        active["title"] = message[:36] + ("…" if len(message) > 36 else "")
    _append_history(active, "user", message)
    _append_history(active, "assistant", answer)
    _save_state(request, state)
    return JsonResponse({**_state_payload(state), "message": answer, "conversation_id": active["id"], "execution_status": execution_status})


@login_required
@require_POST
def session_new_api(request):
    state = _load_state(request)
    new_session = {"id": str(uuid.uuid4()), "title": f"Session {len(state['sessions']) + 1}", "history": [], "pending_write": None}
    state["sessions"].append(new_session)
    state["sessions"] = state["sessions"][-MAX_SESSIONS:]
    state["active_session_id"] = new_session["id"]
    _save_state(request, state)
    return JsonResponse(_state_payload(state))


@login_required
@require_POST
def session_select_api(request):
    body = json.loads(request.body or b"{}")
    state = _load_state(request)
    requested = str(body.get("session_id") or "")
    if not any(item.get("id") == requested for item in state["sessions"]):
        return JsonResponse({"error": "Session inconnue."}, status=404)
    state["active_session_id"] = requested
    _save_state(request, state)
    return JsonResponse(_state_payload(state))


@login_required
@require_POST
def session_delete_api(request):
    body = json.loads(request.body or b"{}")
    state = _load_state(request)
    requested = str(body.get("session_id") or "")
    state["sessions"] = [item for item in state["sessions"] if item.get("id") != requested]
    if not state["sessions"]:
        state = _default_state()
    elif state.get("active_session_id") == requested:
        state["active_session_id"] = state["sessions"][0]["id"]
    _save_state(request, state)
    return JsonResponse(_state_payload(state))


@login_required
@require_POST
def history_clear_api(request):
    # Reset total : supprime toutes les conversations, pending writes et contexte serveur.
    request.session.pop(SESSION_KEY, None)
    state = _default_state()
    _save_state(request, state)
    response = JsonResponse(_state_payload(state))
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
def ui_api(request):
    body = json.loads(request.body or b"{}")
    state = _load_state(request)
    current = state.setdefault("ui", {})
    current.update({key: body[key] for key in ("open", "layout", "width") if key in body})
    _save_state(request, state)
    return JsonResponse(_state_payload(state))
