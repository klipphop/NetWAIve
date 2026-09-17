(() => {
  const messages = document.getElementById("netwaive-messages");
  const form = document.getElementById("netwaive-form");
  const input = document.getElementById("netwaive-input");
  const status = document.getElementById("netwaive-status");
  const clearButton = document.getElementById("netwaive-clear");
  let conversationId = null;
  let pendingWrite = null;
  let lastUserMessage = "";
  let resetEpoch = 0;
  let activeChatController = null;
  const TAB_KEY = "netwaive-tab-id-v1";
  const tabId = (() => {
    let value = sessionStorage.getItem(TAB_KEY);
    if (!value) { value = crypto.randomUUID(); sessionStorage.setItem(TAB_KEY, value); }
    return value;
  })();
  const NAV_KEY = "netwaive-navigation-v1";
  const navKey = `${NAV_KEY}:${tabId}:${location.pathname}`;
  const saveNavigation = () => {
    try { sessionStorage.setItem(navKey, JSON.stringify({ pageY: window.scrollY, chatY: messages.scrollTop })); } catch {}
  };
  const restoreNavigation = () => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(navKey) || "null");
      if (!saved) return;
      requestAnimationFrame(() => { window.scrollTo({ top: Number(saved.pageY) || 0, behavior: "instant" }); messages.scrollTop = Number(saved.chatY) || 0; });
    } catch {}
  };
  window.addEventListener("pagehide", saveNavigation);
  window.addEventListener("scroll", saveNavigation, { passive: true });
  messages.addEventListener("scroll", saveNavigation, { passive: true });

  const pageContext = () => {
    const match = location.pathname.match(/^\/(?:plugins\/)?([^/]+)\/([^/]+)\/(\d+)\/?/);
    return { path: location.pathname.slice(0, 500), title: document.title.slice(0, 200), object: match ? { app: match[1], resource: match[2], id: Number(match[3]) } : null };
  };

  const renderMarkdown = (text) => {
    const esc = (value) => value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\"/g, "&quot;");
    const inline = (value) => esc(value)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>");
    const lines = String(text).split(/\r?\n/);
    let html = "";
    let table = false;
    let codeBlock = false;
    const closeTable = () => { if (table) { html += "</tbody></table>"; table = false; } };
    for (const line of lines) {
      if (line.trim().startsWith("```")) {
        closeTable();
        if (codeBlock) {
          html += "</code></pre>";
          codeBlock = false;
        } else {
          html += "<pre class=\"netwaive-code\"><code>";
          codeBlock = true;
        }
        continue;
      }
      if (codeBlock) {
        html += `${esc(line)}\n`;
        continue;
      }
      if (line.trim().startsWith("|") && line.split("|").length >= 3) {
        const cells = line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(x => x.trim());
        if (cells.every(x => /^:?-{3,}:?$/.test(x))) continue;
        if (!table) {
          html += "<table class=\"netwaive-table\"><thead><tr>" + cells.map(x => `<th>${inline(x)}</th>`).join("") + "</tr></thead><tbody>";
          table = true;
        } else {
          html += "<tr>" + cells.map(x => `<td>${inline(x)}</td>`).join("") + "</tr>";
        }
      } else {
        closeTable();
        const value = line.trim();
        if (!value) { html += "<div class=\"netwaive-spacer\"></div>"; continue; }
        if (value.startsWith("### ")) html += `<h4>${inline(value.slice(4))}</h4>`;
        else if (value.startsWith("## ")) html += `<h3>${inline(value.slice(3))}</h3>`;
        else if (value.startsWith("# ")) html += `<h2>${inline(value.slice(2))}</h2>`;
        else if (/^[-*] /.test(value)) html += `<div class=\"netwaive-list-item\">• ${inline(value.slice(2))}</div>`;
        else html += `<p>${inline(value)}</p>`;
      }
    }
    closeTable();
    if (codeBlock) html += "</code></pre>";
    return html;
  };

  const sendFeedback = async (responseId, rating, reason = "", expected = "") => {
    const response = await fetch("/plugins/netwaive/api/feedback/", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]")?.value || "" },
      body: JSON.stringify({ tab_id: tabId, conversation_id: conversationId, response_id: responseId, rating, reason, expected_answer: expected }),
    });
    if (!response.ok) throw new Error((await response.json()).error || "Feedback impossible");
  };

  const renderQuickReplies = (replies) => {
    if (!Array.isArray(replies) || !replies.length) return;
    const wrap = document.createElement("div");
    wrap.className = "netwaive-quick-replies d-flex flex-wrap gap-2 mt-1";
    replies.slice(0, 6).forEach((reply) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-sm btn-outline-primary";
      button.textContent = reply;
      button.addEventListener("click", () => { input.value = reply; form.requestSubmit(); });
      wrap.appendChild(button);
    });
    messages.appendChild(wrap);
  };

  const renderQuestion = (question) => {
    if (!question) return;
    const wrap = document.createElement("div"); wrap.className = "netwaive-question d-flex flex-wrap gap-2 mt-1";
    (question.options || []).forEach((option) => {
      const button = document.createElement("button"); button.type = "button"; button.className = "btn btn-sm btn-primary"; button.textContent = option.label;
      button.addEventListener("click", () => { input.value = option.value; form.requestSubmit(); }); wrap.appendChild(button);
    });
    messages.appendChild(wrap);
  };

  const add = (role, text, responseId = null, isLast = false) => {
    const el = document.createElement("div");
    el.className = `mb-2 ${role === "user" ? "text-end" : "netwaive-assistant-row"}`;
    if (role === "assistant") { el.style.display = "flex"; el.style.flexDirection = "column"; el.style.alignItems = "stretch"; }
    const box = document.createElement("span");
    box.className = role === "user" ? "badge text-bg-primary text-wrap" : "badge text-bg-light text-dark text-wrap text-start";
    box.style.maxWidth = "90%";
    if (role === "assistant") {
      box.style.display = "block";
      box.style.maxHeight = "55vh";
      box.style.overflowY = "auto";
      box.style.whiteSpace = "pre-wrap";
      box.innerHTML = renderMarkdown(text);
    } else {
      box.textContent = text;
    }

    el.appendChild(box);
    if (role === "assistant" && responseId && isLast) {
      const controls = document.createElement("div"); controls.className = "small mt-1 d-flex align-items-center gap-1";
      for (const [rating, label, title] of [["up", "👍", "Réponse utile"], ["down", "👎", "Réponse à améliorer"]]) {
        const button = document.createElement("button"); button.type = "button"; button.className = "btn btn-sm btn-link p-1"; button.textContent = label; button.title = title; button.setAttribute("aria-label", title);
        button.addEventListener("click", async () => {
          try {
            if (rating === "down") {
              const editor = document.createElement("div"); editor.className = "netwaive-feedback-editor d-flex gap-1 mt-1";
              const field = document.createElement("textarea"); field.rows = 2; field.maxLength = 500; field.placeholder = "Que faut-il améliorer ?"; field.className = "form-control form-control-sm";
              const submit = document.createElement("button"); submit.type = "button"; submit.className = "btn btn-sm btn-primary"; submit.textContent = "Envoyer";
              editor.append(field, submit); controls.after(editor);
              submit.addEventListener("click", async () => { await sendFeedback(responseId, rating, field.value, ""); editor.remove(); controls.textContent = "Merci pour votre retour."; });
              return;
            }
            await sendFeedback(responseId, rating);
            controls.textContent = "Merci pour votre retour.";
          } catch (error) { controls.textContent = `Erreur : ${error.message}`; }
        });
        controls.appendChild(button);
      }
      const regenerate = document.createElement("button"); regenerate.type = "button"; regenerate.className = "btn btn-sm btn-outline-secondary ms-1"; regenerate.textContent = "↻ Régénérer";
      regenerate.addEventListener("click", async () => { if (lastUserMessage) { input.value = lastUserMessage; form.dataset.regenerate = "1"; form.requestSubmit(); } });
      controls.appendChild(regenerate);
      el.appendChild(controls);
    }
    messages.appendChild(el);
    messages.scrollTop = messages.scrollHeight;
  };

  const formatOperation = (operation, index) => {
    const data = operation.data || {};
    const verb = { POST: "Créer", PATCH: "Modifier", DELETE: "Supprimer" }[operation.method] || operation.method;
    const identity = data.name || data.model || data.prefix || data.address || (data.vid != null ? `VLAN ${data.vid}` : `objet ${index + 1}`);
    const labels = { status: "statut", description: "description", enabled: "activé", mgmt_only: "gestion uniquement", type: "type", site: "site", group: "groupe VLAN", vlan: "VLAN", device: "équipement", assigned_object_id: "interface", device_type: "modèle", role: "rôle", scope_id: "site" };
    const details = Object.entries(data).filter(([key]) => !["name", "model", "prefix", "address", "vid", "slug"].includes(key)).map(([key, value]) => {
      let display = typeof value === "string" ? value.replace(/\$\{(\d+)\.id\}/g, (_, n) => `objet résolu à l’étape ${Number(n) + 1}`) : value;
      if (typeof display === "string" && display.startsWith("${available_ip:")) display = "première IP libre du préfixe";
      if (["true", "false"].includes(String(display))) display = String(display) === "true" ? "oui" : "non";
      if (typeof display === "number") display = "objet NetBox résolu";
      return `${labels[key] || key}: ${display}`;
    }).join(" · ");
    return `${verb} ${identity}${details ? ` — ${details}` : ""}`;
  };

  const renderPendingControls = () => {
    document.getElementById("netwaive-confirm-wrap")?.remove();
    document.getElementById("netwaive-plan-card")?.remove();
    if (!pendingWrite) return;
    const plan = pendingWrite.change_plan;
    if (plan) {
      const card = document.createElement("div");
      card.id = "netwaive-plan-card";
      card.className = "alert alert-warning mt-2 text-start";
      const title = document.createElement("strong");
      title.textContent = `${plan.summary || "Plan de changement"} · risque ${plan.risk === "low" ? "faible" : plan.risk === "high" ? "élevé" : "moyen"}`;
      card.appendChild(title);
      const list = document.createElement("ol");
      (plan.operations || []).forEach((operation, index) => {
        const item = document.createElement("li");
        item.textContent = formatOperation(operation, index);
        list.appendChild(item);
      });
      card.appendChild(list);
      messages.appendChild(card);
    }
    const wrap = document.createElement("div");
    wrap.id = "netwaive-confirm-wrap";
    wrap.className = "d-flex gap-2 mt-2 justify-content-end";
    const yes = document.createElement("button");
    yes.type = "button"; yes.className = "btn btn-sm btn-success"; yes.textContent = "Confirmer";
    const no = document.createElement("button");
    no.type = "button"; no.className = "btn btn-sm btn-outline-danger"; no.textContent = "Annuler";
    const sendQuick = async (message, approvePending = false) => {
      add("user", message);
      const regenerate = form.dataset.regenerate === "1";
      delete form.dataset.regenerate;
      const response = await fetch("/plugins/netwaive/api/chat/", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]")?.value || "" }, body: JSON.stringify({ message, tab_id: tabId, conversation_id: conversationId, approve_pending: approvePending, plan_id: approvePending ? pendingWrite?.change_plan?.id : undefined, regenerate }) });
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) { const raw = await response.text(); throw new Error(`Réponse HTTP ${response.status}: ${raw.slice(0, 300)}`); }
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Erreur LLM");
      conversationId = data.conversation_id || conversationId;
      pendingWrite = data.pending_write || null;
      add("assistant", data.message || data.answer || JSON.stringify(data), data.response_id || null, true);
      renderQuickReplies(data.quick_replies);
      renderQuestion(data.question);
      renderPendingControls();
      restoreNavigation();
    };
    yes.addEventListener("click", async () => { yes.disabled = true; no.disabled = true; try { await sendQuick("oui", true); } catch (error) { add("assistant", `Erreur : ${error.message}`); } finally { yes.disabled = false; no.disabled = false; } });
    no.addEventListener("click", async () => { yes.disabled = true; no.disabled = true; try { const response = await fetch("/plugins/netwaive/api/pending/cancel/", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]")?.value || "" }, body: JSON.stringify({ tab_id: tabId, conversation_id: conversationId }) }); const data = await response.json(); if (!response.ok) throw new Error(data.error || "Annulation impossible"); pendingWrite = null; add("assistant", data.message); renderPendingControls(); } catch (error) { add("assistant", `Erreur : ${error.message}`); } finally { yes.disabled = false; no.disabled = false; } });
    wrap.appendChild(yes); wrap.appendChild(no); messages.appendChild(wrap); messages.scrollTop = messages.scrollHeight;
  };

  clearButton?.addEventListener("click", async () => {
    resetEpoch += 1;
    activeChatController?.abort();
    activeChatController = null;
    const token = document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
    const response = await fetch("/plugins/netwaive/api/reset/", {
      method: "POST",
      headers: { "X-CSRFToken": token, "Content-Type": "application/json", "Cache-Control": "no-store" },
      body: JSON.stringify({ tab_id: tabId }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Impossible de réinitialiser la session");
    messages.replaceChildren();
    conversationId = data.active_session_id || null;
    pendingWrite = data.pending_write || null;
    renderPendingControls();
  });

  fetch("/plugins/netwaive/api/history/" + "?tab_id=" + encodeURIComponent(tabId), { credentials: "same-origin" })
    .then(r => r.json())
    .then(data => {
      const history = data.history || [];
      history.forEach((item, index) => {
        if (item.role === "user") lastUserMessage = item.text;
        add(item.role, item.text, item.response_id || null, item.role === "assistant" && index === history.length - 1);
      });
      conversationId = data.active_session_id || conversationId;
      pendingWrite = data.pending_write || null;
      renderPendingControls();
      restoreNavigation();
    })
    .catch(() => {});

  fetch("/plugins/netwaive/api/health/", { credentials: "same-origin" })
    .then(async r => {
      if (!r.ok || !(r.headers.get("content-type") || "").includes("application/json")) throw new Error(`HTTP ${r.status}`);
      return r.json();
    })
    .then(data => {
      status.textContent = data.configured ? `LLM connecté${data.model ? ` · ${data.model}` : ""} · pynetbox` : "LLM non configuré";
      status.className = `badge ${data.configured ? "text-bg-success" : "text-bg-warning"}`;
    })
    .catch(() => { status.textContent = "Erreur healthcheck"; });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message) return;
    lastUserMessage = message;
    input.value = "";
    add("user", message);
    const button = form.querySelector("button");
    button.disabled = true;
    const epoch = resetEpoch;
    activeChatController?.abort();
    activeChatController = new AbortController();
    try {
      const regenerate = form.dataset.regenerate === "1";
      delete form.dataset.regenerate;
      const response = await fetch("/plugins/netwaive/api/chat/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]")?.value || "" },
        body: JSON.stringify({ message, tab_id: tabId, conversation_id: conversationId, context: pageContext(), regenerate }),
        signal: activeChatController.signal,
      });
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        const raw = await response.text();
        throw new Error(`Réponse HTTP ${response.status}: ${raw.slice(0, 300)}`);
      }
      const data = await response.json();
      if (epoch !== resetEpoch) return;
      if (!response.ok) throw new Error(data.error || "Erreur LLM");
      conversationId = data.conversation_id || conversationId;
      pendingWrite = data.pending_write || null;
      add("assistant", data.message || data.answer || JSON.stringify(data), data.response_id || null, true);
      renderQuickReplies(data.quick_replies);
      renderQuestion(data.question);
      renderPendingControls();
      restoreNavigation();
    } catch (error) {
      add("assistant", `Erreur : ${error.message}`);
    } finally {
      button.disabled = false;
      input.focus();
    }
  });
})();
