from __future__ import annotations

import json
import logging
import uuid
from functools import wraps
from typing import Any

from django.conf import settings as django_settings
from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from django.utils.translation import get_language

from .config import Settings
from .contracts import ChangePlan
from .models import PendingToolCall
from .copilot import build_agent

logger = logging.getLogger(__name__)

SESSION_KEY = "netwaive_state"
MAX_HISTORY = 100
MAX_SESSIONS = 8


def _gateway_timeout_response(exc: Exception, language: str):
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    text = str(exc).casefold()
    is_timeout = status == 504 or "gateway time-out" in text or "gateway timeout" in text or "timed out" in text
    if not is_timeout:
        return None
    message = (
        "The AI provider timed out. No change was planned or executed; retry the request."
        if language == "en"
        else "Le fournisseur IA n’a pas répondu à temps. Aucune modification n’a été planifiée ou exécutée ; relancez la demande."
    )
    result = JsonResponse({"error": message, "code": "llm_gateway_timeout"}, status=504)
    result["Cache-Control"] = "no-store"
    return result


def _safe_agent_call(callback, language: str):
    try:
        return callback(), None
    except Exception as exc:
        timeout_response = _gateway_timeout_response(exc, language)
        if timeout_response is None:
            raise
        return None, timeout_response




def _plugin_config() -> dict[str, Any]:
    configs = getattr(django_settings, "PLUGINS_CONFIG", {}) or {}
    return dict(configs.get("netwaive", {}) or {})


def _agent_settings() -> Settings:
    cfg = _plugin_config()
    explicit = {
        key: cfg[key]
        for key in (
            "netbox_url", "netbox_token", "netbox_verify_ssl", "llm_base_url",
            "llm_api_key", "llm_model", "mcp_server_url", "mcp_auth_token", "llm_timeout", "max_agent_turns",
            "max_search_results",
        )
        if cfg.get(key) not in (None, "")
    }
    return Settings(**explicit)


def _default_state(generation: str | None = None) -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    return {
        "generation": generation or str(uuid.uuid4()),
        "sessions": [{"id": session_id, "title": "Session 1", "history": [], "pending_write": None, "allow_session": False}],
        "active_session_id": session_id,
        "ui": {"open": True, "layout": "docked", "width": 320},
    }


def _generation_cache_key(request) -> str | None:
    session_key = getattr(request.session, "session_key", None)
    return f"netwaive:generation:{session_key}" if session_key else None


def _load_state(request) -> dict[str, Any]:
    state = request.session.get(SESSION_KEY)
    cache_key = _generation_cache_key(request)
    current_generation = cache.get(cache_key) if cache_key else None
    if current_generation and isinstance(state, dict) and state.get("generation") != current_generation:
        state = _default_state(current_generation)
    if not isinstance(state, dict) or not isinstance(state.get("sessions"), list):
        state = _default_state(current_generation)
    if not state["sessions"]:
        state = _default_state(current_generation)
    state.setdefault("generation", current_generation or str(uuid.uuid4()))
    if cache_key and not current_generation:
        cache.set(cache_key, state["generation"], timeout=None)
    return state


def _save_state(request, state: dict[str, Any]) -> None:
    request.session[SESSION_KEY] = state
    request.session.modified = True


def _generation_is_current(request, generation: str) -> bool:
    cache_key = _generation_cache_key(request)
    if cache_key:
        return cache.get(cache_key) == generation
    current = request.session.get(SESSION_KEY)
    return isinstance(current, dict) and current.get("generation") == generation


def _purge_agent_state(request) -> dict[str, Any]:
    """Supprime tout état NetWAIve sans invalider la session d’authentification Django."""
    request.session.pop(SESSION_KEY, None)
    generation = str(uuid.uuid4())
    cache_key = _generation_cache_key(request)
    if cache_key:
        cache.set(cache_key, generation, timeout=None)
    state = _default_state(generation)
    _save_state(request, state)
    return state




def _active_session(state: dict[str, Any], requested_id: str | None = None) -> dict[str, Any]:
    target = requested_id or state.get("active_session_id")
    for session in state["sessions"]:
        if session.get("id") == target:
            state["active_session_id"] = target
            return session
    session = state["sessions"][0]
    state["active_session_id"] = session["id"]
    return session


def _session_for_tab(state: dict[str, Any], tab_id: str | None) -> dict[str, Any]:
    """Return a session isolated to one browser tab, creating it on first use."""
    tab_id = str(tab_id or "").strip()
    if not tab_id:
        return _active_session(state)
    bindings = state.setdefault("tab_sessions", {})
    session_id = bindings.get(tab_id)
    if session_id and any(item.get("id") == session_id for item in state["sessions"]):
        return _active_session(state, session_id)
    used = set(bindings.values())
    if not bindings and state.get("sessions"):
        session = _active_session(state)
    else:
        session = {"id": str(uuid.uuid4()), "title": f"Session {len(state['sessions']) + 1}", "history": [], "pending_write": None, "allow_session": False}
        state["sessions"].append(session)
        state["sessions"] = state["sessions"][-MAX_SESSIONS:]
    bindings[tab_id] = session["id"]
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
    if history and history[-1].get("role") == role and history[-1].get("text") == text:
        return
    history.append({"role": role, "text": text})
    session["history"] = history[-MAX_HISTORY:]


@login_required
def chat(request):
    english = str(getattr(request, "LANGUAGE_CODE", None) or get_language() or "").lower().startswith("en")
    banner = "NetBox Assistant (Beta - under active development). Read/write based on global configuration. Changes require your confirmation." if english else "Assistant NetBox (Beta - en cours de développement). Lecture/écriture selon la configuration globale. Les modifications requièrent votre confirmation."
    return render(request, "netwaive/chat.html", {"plugin_version": "0.1.0", "banner": banner, "widget_title": "NetBox Assistant (Beta)" if english else "Assistant NetBox (Beta)"})


@login_required
@require_GET
def health_api(request):
    try:
        configured = _agent_settings()
        build_agent(configured)
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
    tab_id = str(request.GET.get("tab_id") or "")
    state = _load_state(request)
    active = _session_for_tab(state, tab_id)
    _save_state(request, state)
    return JsonResponse({**_state_payload(state), "active_session_id": active["id"], "history": active.get("history", []), "pending_write": active.get("pending_write")})


def _json_errors(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except Exception:
            logger.exception("netwaive chat request failed", extra={"path": request.path, "method": request.method})
            return JsonResponse({"error": "Erreur interne NetWAIve."}, status=400)
    return wrapped


@login_required
@require_POST
@_json_errors
def chat_api(request):
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Corps JSON invalide."}, status=400)
    message = str(body.get("message") or "").strip()
    if not message:
        return JsonResponse({"error": "Message vide."}, status=400)
    state = _load_state(request)
    request_generation = state["generation"]
    tab_id = str(body.get("tab_id") or "")
    active = _session_for_tab(state, tab_id)
    requested_conversation = str(body.get("conversation_id") or "")
    pending = active.get("pending_write") if isinstance(active.get("pending_write"), dict) else None
    agent = build_agent(_agent_settings())
    if pending and bool(body.get("approve_pending")):
        raw_plan = pending.get("change_plan")
        if isinstance(raw_plan, dict):
            plan = ChangePlan.model_validate(raw_plan)
            calls = [PendingToolCall(id="batch", name="netbox_batch_execute", arguments=plan.mcp_arguments())]
        else:
            calls = [PendingToolCall.model_validate(item) for item in pending.get("calls", [])]
        result, timeout_response = _safe_agent_call(lambda: agent.confirm(calls, message=str(pending.get("message") or ""), history=active.get("history", [])), "fr")
        if timeout_response is not None:
            return timeout_response
        assert result is not None
        answer = result.message
        active["pending_write"] = None
        status = "success" if result.tool_results and all(item.ok for item in result.tool_results) else "failed"
    else:
        active["pending_write"] = None
        active.pop("allow_session", None)
        result, timeout_response = _safe_agent_call(lambda: agent.run(message, history=active.get("history", [])), "fr")
        if timeout_response is not None:
            return timeout_response
        assert result is not None
        answer = result.message
        if result.pending_confirmation:
            batch_call = result.pending_confirmation[0]
            try:
                plan = ChangePlan(summary="Change Plan NetBox", operations=batch_call.arguments.get("operations", []))
                active["pending_write"] = {"message": message, "change_plan": plan.model_dump()}
            except Exception:
                active["pending_write"] = {"message": message, "calls": [item.model_dump() for item in result.pending_confirmation]}
        else:
            active["pending_write"] = None
        status = "pending" if result.pending_confirmation else "read_only"
    if not _generation_is_current(request, request_generation):
        response = JsonResponse({"error": "Contexte réinitialisé pendant la requête.", "reset": True}, status=409)
        response["Cache-Control"] = "no-store"
        return response
    if len(active.get("history", [])) == 0:
        active["title"] = message[:36] + ("…" if len(message) > 36 else "")
    _append_history(active, "user", message)
    _append_history(active, "assistant", answer)
    _save_state(request, state)
    return JsonResponse({**_state_payload(state), "message": answer, "quick_replies": result.quick_replies, "conversation_id": active["id"], "execution_status": status})


@login_required
@require_POST
def session_new_api(request):
    body = json.loads(request.body or b"{}")
    state = _load_state(request)
    tab_id = str(body.get("tab_id") or "")
    new_session = {"id": str(uuid.uuid4()), "title": f"Session {len(state['sessions']) + 1}", "history": [], "pending_write": None, "allow_session": False}
    state["sessions"].append(new_session)
    state["sessions"] = state["sessions"][-MAX_SESSIONS:]
    state["active_session_id"] = new_session["id"]
    if tab_id:
        state.setdefault("tab_sessions", {})[tab_id] = new_session["id"]
    _save_state(request, state)
    return JsonResponse(_state_payload(state))


@login_required
@require_POST
def session_select_api(request):
    body = json.loads(request.body or b"{}")
    state = _load_state(request)
    requested = str(body.get("session_id") or "")
    tab_id = str(body.get("tab_id") or "")
    if not any(item.get("id") == requested for item in state["sessions"]):
        return JsonResponse({"error": "Session inconnue."}, status=404)
    state["active_session_id"] = requested
    tab_id = str(body.get("tab_id") or "")
    if tab_id:
        state.setdefault("tab_sessions", {})[tab_id] = requested
    _save_state(request, state)
    return JsonResponse(_state_payload(state))


@login_required
@require_POST
def session_delete_api(request):
    body = json.loads(request.body or b"{}")
    state = _load_state(request)
    requested = str(body.get("session_id") or "")
    tab_id = str(body.get("tab_id") or "")
    state["sessions"] = [item for item in state["sessions"] if item.get("id") != requested]
    if not state["sessions"]:
        state = _default_state()
    if state.get("active_session_id") == requested:
        state["active_session_id"] = state["sessions"][0]["id"]
    if tab_id:
        state.setdefault("tab_sessions", {}).pop(tab_id, None)
    _save_state(request, state)
    return JsonResponse(_state_payload(state))


@login_required
@require_POST
def reset_api(request):
    state = _purge_agent_state(request)
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
