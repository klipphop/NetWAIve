from __future__ import annotations

import hashlib
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




def _feedback_model():
    try:
        from django.conf import settings
        if not settings.configured:
            return None
        from .feedback_models import ResponseFeedback
        return ResponseFeedback
    except Exception:
        return None


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


def _valid_tab_id(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value or "")))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("tab_id invalide")


def _plan_id(plan: ChangePlan) -> str:
    raw = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _redact(value: Any, key: str = "") -> Any:
    sensitive = {"token", "password", "secret", "api_key", "private_key", "credential"}
    if any(part in key.casefold() for part in sensitive):
        return "***"
    if isinstance(value, dict):
        return {item_key: _redact(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _public_plan(plan: ChangePlan) -> dict[str, Any]:
    return {
        "id": _plan_id(plan),
        "summary": plan.summary,
        "risk": plan.risk,
        "affected_objects": plan.affected_objects,
        "dependencies": plan.dependencies,
        "operations": [
            {"index": index + 1, "method": item.method, "endpoint": item.endpoint, "data": _redact(item.data)}
            for index, item in enumerate(plan.operations)
        ],
    }


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
    public_pending = None
    if pending and isinstance(pending.get("change_plan"), dict):
        try:
            public_pending = {"message": pending.get("message"), "change_plan": _public_plan(ChangePlan.model_validate(pending["change_plan"]))}
        except Exception:
            public_pending = None
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


def _append_history(session: dict[str, Any], role: str, text: str, response_id: str | None = None) -> None:
    history = session.setdefault("history", [])
    if history and history[-1].get("role") == role and history[-1].get("text") == text:
        return
    item = {"role": role, "text": text}
    if response_id:
        item["response_id"] = response_id
    history.append(item)
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
    except Exception:
        logger.exception("netwaive health check failed")
        return JsonResponse({"configured": False, "error": "Configuration NetWAIve indisponible.", "code": "not_configured", "pynetbox_ready": False}, status=503)


@login_required
@require_GET
def history_api(request):
    tab_id = str(request.GET.get("tab_id") or "")
    state = _load_state(request)
    active = _session_for_tab(state, tab_id)
    _save_state(request, state)
    return JsonResponse({**_state_payload(state), "active_session_id": active["id"], "history": active.get("history", [])})


def _json_errors(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except Exception:
            logger.exception("netwaive API failure", extra={"path": request.path, "method": request.method})
            return JsonResponse({"error": "Erreur interne NetWAIve.", "code": "internal_error"}, status=500)
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
    context = body.get("context") if isinstance(body.get("context"), dict) else {}
    safe_context = {
        "path": str(context.get("path") or "")[:500],
        "title": str(context.get("title") or "")[:200],
        "object": context.get("object") if isinstance(context.get("object"), dict) else None,
    }
    agent_message = message
    Feedback = _feedback_model()
    preferences = [] if Feedback is None else list(Feedback.objects.filter(user=request.user, rating="down").exclude(expected_answer="").order_by("-updated").values_list("reason", "expected_answer")[:3])
    if preferences:
        guidance = [{"reason": reason[:200], "expected_style": expected[:800]} for reason, expected in preferences]
        agent_message += f"\n\n[Préférences de réponse validées par cet utilisateur: {json.dumps(guidance, ensure_ascii=False)}]"
    if safe_context["path"]:
        agent_message += f"\n\n[Contexte NetBox courant: {json.dumps(safe_context, ensure_ascii=False)}]"
    state = _load_state(request)
    request_generation = state["generation"]
    tab_id = _valid_tab_id(body.get("tab_id"))
    active = _session_for_tab(state, tab_id)
    last_execution = active.get("last_execution") if isinstance(active.get("last_execution"), dict) else None
    if last_execution:
        agent_message += f"\n\n[Dernière exécution NetBox réelle, utilisable pour une demande d'annulation/suppression: {json.dumps(last_execution, ensure_ascii=False)}]"
    if bool(body.get("regenerate")):
        previous_answer = next((str(item.get("text") or "") for item in reversed(active.get("history", [])) if item.get("role") == "assistant"), "")
        agent_message += f"\n\n[RÉGÉNÉRATION: critique la réponse précédente et produis une réponse différente, plus précise et plus utile. Ne répète pas simplement le même contenu. Réponse précédente: {previous_answer[:12000]}]"
    requested_conversation = str(body.get("conversation_id") or "")
    if requested_conversation and requested_conversation != active["id"]:
        return JsonResponse({"error": "Conversation ou onglet périmé.", "code": "stale_conversation"}, status=409)
    pending = active.get("pending_write") if isinstance(active.get("pending_write"), dict) else None
    if pending and bool(body.get("approve_pending")):
        if not _can_write(request.user):
            return JsonResponse({"error": "Écriture NetBox non autorisée.", "code": "write_forbidden"}, status=403)
        raw_plan = pending.get("change_plan")
        if not isinstance(raw_plan, dict):
            return JsonResponse({"error": "Change Plan absent ou invalide."}, status=409)
        plan = ChangePlan.model_validate(raw_plan)
        if str(body.get("plan_id") or "") != _plan_id(plan):
            return JsonResponse({"error": "Change Plan périmé ou modifié.", "code": "stale_plan"}, status=409)
        agent = build_agent(_agent_settings())
        result, timeout_response = _safe_agent_call(lambda: agent.confirm(plan), "fr")
        if timeout_response is not None:
            return timeout_response
        assert result is not None
        answer = result.message
        if result.tool_results and all(item.ok for item in result.tool_results):
            payload = result.tool_results[0].data
            if isinstance(payload, dict) and isinstance(payload.get("results"), list):
                active["last_execution"] = {"operations": [
                    {"method": item.get("method"), "endpoint": item.get("endpoint"), "id": (item.get("result") or {}).get("id"), "label": item.get("label")}
                    for item in payload["results"] if isinstance(item, dict) and item.get("method") == "POST" and isinstance((item.get("result") or {}).get("id"), int)
                ]}
        active["pending_write"] = None
        status = "success" if result.tool_results and all(item.ok for item in result.tool_results) else "failed"
    else:
        active["pending_write"] = None
        active.pop("allow_session", None)
        agent = build_agent(_agent_settings())
        result, timeout_response = _safe_agent_call(lambda: agent.run(agent_message, history=active.get("history", [])), "fr")
        if timeout_response is not None:
            return timeout_response
        assert result is not None
        answer = result.message
        if result.change_plan:
            active["pending_write"] = {"message": message, "change_plan": result.change_plan.model_dump()}
        else:
            active["pending_write"] = None
        status = "pending" if result.change_plan else "read_only"
    if not _generation_is_current(request, request_generation):
        response = JsonResponse({"error": "Contexte réinitialisé pendant la requête.", "reset": True}, status=409)
        response["Cache-Control"] = "no-store"
        return response
    if len(active.get("history", [])) == 0:
        active["title"] = message[:36] + ("…" if len(message) > 36 else "")
    response_id = str(uuid.uuid4())
    _append_history(active, "user", message)
    _append_history(active, "assistant", answer, response_id=response_id)
    _save_state(request, state)
    return JsonResponse({**_state_payload(state), "message": answer, "response_id": response_id, "quick_replies": result.quick_replies, "conversation_id": active["id"], "execution_status": status})


@login_required
@require_POST
@_json_errors
def cancel_pending_api(request):
    body = json.loads(request.body or b"{}")
    tab_id = _valid_tab_id(body.get("tab_id"))
    state = _load_state(request)
    active = _session_for_tab(state, tab_id)
    if str(body.get("conversation_id") or "") != active["id"]:
        return JsonResponse({"error": "Conversation ou onglet périmé.", "code": "stale_conversation"}, status=409)
    active["pending_write"] = None
    _save_state(request, state)
    return JsonResponse({**_state_payload(state), "message": "Change Plan annulé.", "conversation_id": active["id"], "execution_status": "cancelled"})


@login_required
@require_POST
@_json_errors
def feedback_api(request):
    body = json.loads(request.body or b"{}")
    tab_id = _valid_tab_id(body.get("tab_id"))
    conversation_id = str(body.get("conversation_id") or "")
    response_id = str(body.get("response_id") or "")
    rating = str(body.get("rating") or "")
    if rating not in {"up", "down"}:
        return JsonResponse({"error": "Évaluation invalide."}, status=400)
    state = _load_state(request)
    active = _session_for_tab(state, tab_id)
    if conversation_id != active["id"]:
        return JsonResponse({"error": "Conversation ou onglet périmé.", "code": "stale_conversation"}, status=409)
    history = active.get("history", [])
    answer_index = next((index for index, item in enumerate(history) if item.get("role") == "assistant" and item.get("response_id") == response_id), None)
    if answer_index is None:
        return JsonResponse({"error": "Réponse inconnue."}, status=404)
    answer = str(history[answer_index].get("text") or "")
    prompt = next((str(item.get("text") or "") for item in reversed(history[:answer_index]) if item.get("role") == "user"), "")
    Feedback = _feedback_model()
    if Feedback is None:
        return JsonResponse({"error": "Feedback indisponible."}, status=503)
    feedback, _ = Feedback.objects.update_or_create(
        response_id=response_id,
        defaults={
            "user": request.user,
            "conversation_id": active["id"],
            "rating": rating,
            "reason": str(body.get("reason") or "")[:500],
            "expected_answer": str(body.get("expected_answer") or "")[:4000],
            "prompt": prompt[:8000],
            "answer": answer[:16000],
        },
    )
    return JsonResponse({"ok": True, "response_id": str(feedback.response_id), "rating": feedback.rating})


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
