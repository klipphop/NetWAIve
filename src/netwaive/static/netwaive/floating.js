(() => {
  document.querySelectorAll(".netwaive-widget").forEach((widget) => {
    if (widget.dataset.initialized === "1") return;
    widget.dataset.initialized = "1";

    const fab = widget.querySelector(".netwaive-fab");
    const drawer = widget.querySelector(".netwaive-drawer");
    const closeBtn = widget.querySelector(".netwaive-close");
    const clearBtn = widget.querySelector(".netwaive-clear");
    const dockBtn = widget.querySelector(".netwaive-dock");
    const tabAddBtn = widget.querySelector(".netwaive-tab-add");
    const tabsEl = widget.querySelector(".netwaive-tabs");
    const dragHandle = widget.querySelector("[data-netwaive-drag-handle]");
    const resizeHandle = widget.querySelector(".netwaive-resize-handle");
    const form = widget.querySelector(".netwaive-drawer-form");
    const input = widget.querySelector(".netwaive-drawer-input");
    const messages = widget.querySelector(".netwaive-drawer-messages");
    const status = widget.querySelector(".netwaive-drawer-status");

    const POS_KEY = "netwaive-window-pos-v3";
    const TAB_KEY = "netwaive-tab-id-v1";
    const tabId = (() => {
      let value = sessionStorage.getItem(TAB_KEY);
      if (!value) { value = crypto.randomUUID(); sessionStorage.setItem(TAB_KEY, value); }
      return value;
    })();

    let resetEpoch = 0;
    let activeChatController = null;
    let lastUserMessage = "";
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

    const LAYOUT_KEY = "netwaive-layout-v1";
    const OPEN_KEY = "netwaive-open-v1";
    const csrf = () => document.cookie.split(";").map(x => x.trim()).find(x => x.startsWith("csrftoken="))?.split("=").slice(1).join("=") || form.querySelector("input[name=csrfmiddlewaretoken]")?.value || "";

    const state = {
      sessions: [],
      activeSessionId: null,
      history: [],
      pendingWrite: null,
      layout: "floating",
      ui: { open: true, layout: "docked", width: 320 },
    };

    const pageContext = () => {
      const match = location.pathname.match(/^\/(?:plugins\/)?([^/]+)\/([^/]+)\/(\d+)\/?/);
      return { path: location.pathname.slice(0, 500), title: document.title.slice(0, 200), object: match ? { app: match[1], resource: match[2], id: Number(match[3]) } : null };
    };

    const api = {
      history: "/plugins/netwaive/api/history/",
      newSession: "/plugins/netwaive/api/sessions/new/",
      selectSession: "/plugins/netwaive/api/sessions/select/",
      deleteSession: "/plugins/netwaive/api/sessions/delete/",
      reset: "/plugins/netwaive/api/reset/",
      cancelPending: "/plugins/netwaive/api/pending/cancel/",
      chat: "/plugins/netwaive/api/chat/",
      health: "/plugins/netwaive/api/health/",
      ui: "/plugins/netwaive/api/ui/",
    };

    function loadPos() {
      try { return JSON.parse(localStorage.getItem(POS_KEY) || "null"); } catch { return null; }
    }

    function savePos() {
      if (state.layout === "docked") return;
      const rect = drawer.getBoundingClientRect();
      try {
        localStorage.setItem(POS_KEY, JSON.stringify({ left: rect.left, top: rect.top, width: rect.width, height: rect.height }));
      } catch {}
    }

    function loadLayout() {
      try { return localStorage.getItem(LAYOUT_KEY) === "docked" ? "docked" : "floating"; } catch { return "floating"; }
    }

    function loadOpen() {
      try {
        const value = localStorage.getItem(OPEN_KEY);
        return value === null ? true : value === "1";
      } catch {
        return true;
      }
    }

    function saveUi(uiPatch = {}) {
      state.ui = { ...state.ui, ...uiPatch };
      try {
        localStorage.setItem(OPEN_KEY, state.ui.open ? "1" : "0");
        localStorage.setItem(LAYOUT_KEY, state.ui.layout);
      } catch {}
      void fetch(api.ui, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify({ ...state.ui, tab_id: tabId }),
      }).catch(() => {});
    }

    function syncDockWidth() {
      const width = Math.round(drawer.getBoundingClientRect().width || state.ui.width || 320);
      document.body.style.setProperty("--netwaive-docked-width", `${width}px`);
      state.ui.width = width;
    }

    function applyLayout(layout, persist = true, visible = !drawer.hidden) {
      state.layout = layout === "docked" ? "docked" : "floating";
      drawer.dataset.layout = state.layout;
      drawer.classList.toggle("docked", state.layout === "docked");
      document.body.classList.toggle("netwaive-docked", visible && state.layout === "docked");
      if (!(visible && state.layout === "docked")) {
        document.body.style.removeProperty("--netwaive-docked-width");
      }
      if (dockBtn) {
        dockBtn.textContent = state.layout === "docked" ? "↔" : "▥";
        dockBtn.title = state.layout === "docked" ? "Détacher" : "Ancrer à droite";
        dockBtn.setAttribute("aria-label", dockBtn.title);
      }
      if (state.layout === "docked") {
        drawer.style.left = "auto";
        drawer.style.top = "0";
        drawer.style.right = "0";
        drawer.style.bottom = "0";
        drawer.style.width = `${state.ui.width || 320}px`;
        drawer.style.height = "100vh";
        drawer.style.borderRadius = "0";
        drawer.style.boxShadow = "none";
        if (!drawer.hidden) syncDockWidth();
      } else {
        document.body.style.removeProperty("--netwaive-docked-width");
        drawer.style.right = "24px";
        drawer.style.bottom = "88px";
        drawer.style.left = "auto";
        drawer.style.top = "auto";
        drawer.style.width = "";
        drawer.style.height = "";
        drawer.style.borderRadius = "";
        drawer.style.boxShadow = "0 12px 40px #0003";
        const pos = loadPos();
        if (pos) {
          if (typeof pos.left === "number") drawer.style.left = `${pos.left}px`;
          if (typeof pos.top === "number") drawer.style.top = `${pos.top}px`;
          if (typeof pos.width === "number") drawer.style.width = `${pos.width}px`;
          if (typeof pos.height === "number") drawer.style.height = `${pos.height}px`;
          drawer.style.right = "auto";
          drawer.style.bottom = "auto";
        }
      }
      if (persist) saveUi({ layout: state.layout, open: !drawer.hidden });
    }

    function escHtml(value) {
      return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\"/g, "&quot;");
    }

    function renderMarkdown(text) {
      const inline = (value) => escHtml(value)
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
          html += `${escHtml(line)}\n`;
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
          continue;
        }

        closeTable();
        const value = line.trim();
        if (!value) { html += "<div class=\"netwaive-spacer\"></div>"; continue; }
        if (value.startsWith("### ")) html += `<h4>${inline(value.slice(4))}</h4>`;
        else if (value.startsWith("## ")) html += `<h3>${inline(value.slice(3))}</h3>`;
        else if (value.startsWith("# ")) html += `<h2>${inline(value.slice(2))}</h2>`;
        else if (/^[-*] /.test(value)) html += `<div class=\"netwaive-list-item\">• ${inline(value.slice(2))}</div>`;
        else html += `<p>${inline(value)}</p>`;
      }
      closeTable();
      if (codeBlock) html += "</code></pre>";
      return html;
    }

    const sendFeedback = async (responseId, rating, reason = "", expected = "") => {
      const response = await fetch("/plugins/netwaive/api/feedback/", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() }, body: JSON.stringify({ tab_id: tabId, conversation_id: state.activeSessionId, response_id: responseId, rating, reason, expected_answer: expected }) });
      if (!response.ok) throw new Error((await response.json()).error || "Feedback impossible");
    };

    function addMessage(role, text, responseId = null, isLast = false) {
      const row = document.createElement("div");
      row.className = `netwaive-msg ${role}`;
      if (role === "assistant") { row.style.display = "flex"; row.style.flexDirection = "column"; row.style.alignItems = "stretch"; }
      const bubble = document.createElement("span");
      if (role === "assistant") {
        bubble.style.display = "block";
        bubble.style.maxHeight = "55vh";
        bubble.style.overflowY = "auto";
        bubble.style.whiteSpace = "pre-wrap";
        bubble.innerHTML = renderMarkdown(text);
      } else {
        bubble.textContent = text;
      }
      row.appendChild(bubble);
      if (role === "assistant" && responseId && isLast) {
        const controls = document.createElement("div"); controls.className = "netwaive-feedback";
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
              await sendFeedback(responseId, rating); controls.textContent = "Merci pour votre retour.";
            } catch (error) { controls.textContent = `Erreur : ${error.message}`; }
          });
          controls.appendChild(button);
        }
        const regenerate = document.createElement("button"); regenerate.type = "button"; regenerate.className = "btn btn-sm btn-outline-secondary"; regenerate.textContent = "↻ Régénérer";
        regenerate.addEventListener("click", () => { if (lastUserMessage) { input.value = lastUserMessage; form.dataset.regenerate = "1"; form.requestSubmit(); } });
        controls.appendChild(regenerate);
        row.appendChild(controls);
      }
      messages.appendChild(row);
      messages.scrollTop = messages.scrollHeight;
    }

      const renderQuickReplies = (replies) => {
        if (!Array.isArray(replies) || !replies.length) return;
        const wrap = document.createElement("div");
        wrap.className = "netwaive-quick-replies d-flex flex-wrap gap-2 mt-1";
        replies.slice(0, 6).forEach((reply) => {
          const button = document.createElement("button");
          button.type = "button"; button.className = "btn btn-sm btn-outline-primary"; button.textContent = reply;
          button.addEventListener("click", () => { input.value = reply; form.requestSubmit(); });
          wrap.appendChild(button);
        });
        messages.appendChild(wrap);
      };

      const renderConversation = () => {
        messages.replaceChildren();
        const intro = document.createElement("div");
        intro.className = "netwaive-intro";
        intro.textContent = "Assistant NetBox. Lecture/écriture selon la configuration globale. Les écritures demandent une confirmation.";
        messages.appendChild(intro);
        state.history.forEach((item, index) => {
          if (item.role === "user") lastUserMessage = item.text;
          addMessage(item.role, item.text, item.response_id || null, item.role === "assistant" && index === state.history.length - 1);
        });
        renderPendingControls();
      };

    function renderPendingControls() {
      messages.querySelector("#netwaive-confirm-wrap")?.remove();
      messages.querySelector("#netwaive-plan-card")?.remove();
      if (!state.pendingWrite) return;
      const plan = state.pendingWrite.change_plan;
      if (plan) {
        const card = document.createElement("div");
        card.id = "netwaive-plan-card";
        card.className = "alert alert-warning mt-2 text-start";
        const title = document.createElement("strong");
        title.textContent = `${plan.summary || "Plan de changement"} · risque ${plan.risk === "low" ? "faible" : plan.risk === "high" ? "élevé" : "moyen"}`;
        card.appendChild(title);
        const list = document.createElement("ol");
        (plan.operations || []).forEach((operation) => {
          const item = document.createElement("li");
          const identity = operation.data?.name || operation.data?.model || operation.data?.prefix || operation.data?.address || `opération ${operation.index}`;
        const verbs = { POST: "Créer", PATCH: "Modifier", DELETE: "Supprimer" };
          const details = Object.entries(operation.data || {}).filter(([key]) => key !== "slug").map(([key, value]) => {
            const clean = typeof value === "string" ? value.replace(/\$\{(\d+)\.id\}/g, (_, n) => `résultat étape ${Number(n) + 1}`) : JSON.stringify(value);
            return `${key}: ${clean}`;
          }).join(" · ");
          item.textContent = `${verbs[operation.method] || operation.method} ${identity}${details ? ` — ${details}` : ""}`;
          list.appendChild(item);
        });
        card.appendChild(list);
        messages.appendChild(card);
      }
      const wrap = document.createElement("div");
      wrap.id = "netwaive-confirm-wrap";
      wrap.className = "d-flex gap-2 mt-2 justify-content-end";
      const yes = document.createElement("button");
      yes.type = "button";
      yes.className = "btn btn-sm btn-success";
      yes.textContent = "Confirmer";
      const no = document.createElement("button");
      no.type = "button";
      no.className = "btn btn-sm btn-outline-danger";
      no.textContent = "Annuler";
      const sendQuick = async (message, approvePending = false) => {
        addMessage("user", message);
        const response = await fetch(api.chat, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          body: JSON.stringify({ message, tab_id: tabId, conversation_id: state.activeSessionId, approve_pending: approvePending, plan_id: approvePending ? state.pendingWrite?.change_plan?.id : undefined }),
        });
        const contentType = response.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) throw new Error(`Réponse HTTP ${response.status} non JSON`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Erreur LLM");
        state.sessions = data.sessions || state.sessions;
        state.activeSessionId = data.active_session_id || state.activeSessionId;
        state.history = data.history || state.history;
        state.pendingWrite = data.pending_write || null;
        state.ui = { ...state.ui, ...(data.ui || {}) };
        renderTabs();
        renderConversation();
        renderQuickReplies(data.quick_replies);
        restoreNavigation();
      };
      yes.addEventListener("click", async () => {
        yes.disabled = true; no.disabled = true;
        try { await sendQuick("oui", true); } catch (error) { addMessage("assistant", `Erreur : ${error.message}`); }
        finally { yes.disabled = false; no.disabled = false; }
      });
      no.addEventListener("click", async () => {
        yes.disabled = true; no.disabled = true;
        try {
          const response = await fetch(api.cancelPending, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
            body: JSON.stringify({ tab_id: tabId, conversation_id: state.activeSessionId }),
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Annulation impossible");
          state.pendingWrite = null;
          state.history = data.history || state.history;
          renderConversation();
        } catch (error) { addMessage("assistant", `Erreur : ${error.message}`); }
        finally { yes.disabled = false; no.disabled = false; }
      });
      wrap.appendChild(yes);
      wrap.appendChild(no);
      messages.appendChild(wrap);
      messages.scrollTop = messages.scrollHeight;
    }

    function renderTabs() {
      tabsEl.replaceChildren();
      state.sessions.forEach(session => {
        const tab = document.createElement("button");
        tab.type = "button";
        tab.className = `netwaive-tab${session.id === state.activeSessionId ? " active" : ""}`;
        tab.dataset.sessionId = session.id;

        const label = document.createElement("span");
        label.className = "netwaive-tab-label";
        label.textContent = session.title || "Session";

        const close = document.createElement("button");
        close.type = "button";
        close.className = "netwaive-tab-close";
        close.textContent = "×";
        close.title = "Supprimer cette session";

        close.addEventListener("click", async (event) => {
          event.stopPropagation();
          await deleteSession(session.id);
        });
        tab.addEventListener("click", async () => {
          if (session.id !== state.activeSessionId) await selectSession(session.id);
        });

        tab.appendChild(label);
        tab.appendChild(close);
        tabsEl.appendChild(tab);
      });
    }

    async function refreshHealth() {
      try {
        const r = await fetch(api.health, { credentials: "same-origin" });
        if (!r.ok || (r.headers.get("content-type") || "").includes("text/html")) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        status.textContent = data.configured ? `LLM OK${data.model ? ` · ${data.model}` : ""} · pynetbox` : "LLM absent";
      } catch {
        status.textContent = "LLM indisponible";
      }
    }

    async function loadState() {
      const r = await fetch(api.history + "?tab_id=" + encodeURIComponent(tabId), { credentials: "same-origin" });
      const data = await r.json();
      state.sessions = data.sessions || [];
      state.activeSessionId = data.active_session_id || null;
      state.history = data.history || [];
      state.pendingWrite = data.pending_write || null;
      state.ui = { ...state.ui, ...(data.ui || {}) };
      renderTabs();
      renderConversation();
      return data.ui || state.ui;
    }

    async function createSession() {
      const r = await fetch(api.newSession, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() }, body: JSON.stringify({ tab_id: tabId }) });
      const data = await r.json();
      state.sessions = data.sessions || state.sessions;
      state.activeSessionId = data.active_session_id || data.session?.id || state.activeSessionId;
      state.history = [];
      state.pendingWrite = null;
      state.ui = { ...state.ui, ...(data.ui || {}) };
      renderTabs();
      renderConversation();
      input.focus();
      saveUi({ open: true, layout: state.layout });
    }

    async function selectSession(sessionId) {
      const r = await fetch(api.selectSession, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify({ tab_id: tabId, session_id: sessionId }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Impossible de sélectionner la session");
      state.sessions = data.sessions || state.sessions;
      state.activeSessionId = data.active_session_id || sessionId;
      state.history = data.history || [];
      state.pendingWrite = data.pending_write || null;
      state.ui = { ...state.ui, ...(data.ui || {}) };
      renderTabs();
      renderConversation();
    }

    async function deleteSession(sessionId) {
      const r = await fetch(api.deleteSession, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify({ tab_id: tabId, session_id: sessionId }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Impossible de supprimer la session");
      state.sessions = data.sessions || [];
      state.activeSessionId = data.active_session_id || null;
      state.history = data.history || [];
      state.pendingWrite = data.pending_write || null;
      state.ui = { ...state.ui, ...(data.ui || {}) };
      renderTabs();
      renderConversation();
    }

    clearBtn?.addEventListener("click", async (event) => {
      resetEpoch += 1;
      activeChatController?.abort();
      activeChatController = null;
      event.stopPropagation();
      state.history = [];
      state.pendingWrite = null;
      saveUi({ open: true, layout: state.layout });
      try {
        const r = await fetch(api.reset, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          body: JSON.stringify({ tab_id: tabId, session_id: state.activeSessionId }),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || "Impossible d'effacer la session");
        state.sessions = data.sessions || state.sessions;
        state.activeSessionId = data.active_session_id || state.activeSessionId;
        state.history = data.history || [];
        state.pendingWrite = null;
        state.ui = { ...state.ui, ...(data.ui || {}) };
        renderTabs();
        renderConversation();
        renderQuickReplies(data.quick_replies);
        restoreNavigation();
      } catch (error) {
        state.history = [];
        renderConversation();
      }
    });

    dockBtn?.addEventListener("click", () => {
      const nextLayout = state.layout === "docked" ? "floating" : "docked";
      drawer.hidden = false;
      saveUi({ open: true, layout: nextLayout });
      applyLayout(nextLayout, true, true);
      renderConversation();
    });

    tabAddBtn?.addEventListener("click", async () => createSession());
    closeBtn?.addEventListener("click", () => {
      drawer.hidden = true;
      saveUi({ open: false, layout: state.layout });
      applyLayout(state.layout, false, false);
    });

    dragHandle?.addEventListener("pointerdown", (event) => {
      if (event.target.closest("button") || state.layout === "docked") return;
      event.preventDefault();
      const rect = drawer.getBoundingClientRect();
      const startX = event.clientX;
      const startY = event.clientY;
      const startLeft = rect.left;
      const startTop = rect.top;
      const move = (e) => {
        drawer.style.left = `${Math.max(4, startLeft + e.clientX - startX)}px`;
        drawer.style.top = `${Math.max(4, startTop + e.clientY - startY)}px`;
        drawer.style.right = "auto";
        drawer.style.bottom = "auto";
      };
      const up = () => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
        savePos();
      };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    });

    resizeHandle?.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const rect = drawer.getBoundingClientRect();
      const startX = event.clientX;
      const startY = event.clientY;
      const startWidth = rect.width;
      const startHeight = rect.height;
      const startLeft = rect.left;
      const startTop = rect.top;
      const docked = state.layout === "docked";
      const move = (e) => {
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        const width = Math.max(280, startWidth - dx);
        if (docked) {
          drawer.style.width = `${width}px`;
          drawer.style.height = "100vh";
          drawer.style.left = "auto";
          drawer.style.top = "0";
          drawer.style.right = "0";
          drawer.style.bottom = "0";
          syncDockWidth();
        } else {
          const height = Math.max(260, startHeight - dy);
          drawer.style.width = `${width}px`;
          drawer.style.height = `${height}px`;
          drawer.style.left = `${Math.max(4, startLeft + dx)}px`;
          drawer.style.top = `${Math.max(4, startTop + dy)}px`;
          drawer.style.right = "auto";
          drawer.style.bottom = "auto";
        }
      };
      const up = () => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", up);
        savePos();
        saveUi({ open: !drawer.hidden, layout: state.layout, width: state.ui.width });
      };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    });

    fab?.addEventListener("click", () => {
      drawer.hidden = false;
      if (state.layout === "docked") {
        document.body.classList.add("netwaive-docked");
        syncDockWidth();
      }
      applyLayout(state.layout, true, true);
      saveUi({ open: true, layout: state.layout, width: state.ui.width });
      input.focus();
    });

    form?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = input.value.trim();
      if (!message) return;
      lastUserMessage = message;
      input.value = "";
      addMessage("user", message);
      const button = form.querySelector("button[type='submit']");
      button.disabled = true;
      const epoch = resetEpoch;
      activeChatController?.abort();
      activeChatController = new AbortController();
      const regenerate = form.dataset.regenerate === "1";
      delete form.dataset.regenerate;
      try {
        const response = await fetch(api.chat, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          body: JSON.stringify({ message, tab_id: tabId, conversation_id: state.activeSessionId, context: pageContext(), regenerate }),
          signal: activeChatController.signal,
        });
        const contentType = response.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) throw new Error(`Réponse HTTP ${response.status} non JSON`);
        const data = await response.json();
        if (epoch !== resetEpoch) return;
        if (!response.ok) throw new Error(data.error || "Erreur LLM");
        state.sessions = data.sessions || state.sessions;
        state.activeSessionId = data.active_session_id || state.activeSessionId;
        state.history = data.history || state.history;
        state.pendingWrite = data.pending_write || null;
        state.ui = { ...state.ui, ...(data.ui || {}) };
        renderTabs();
        renderConversation();
        renderQuickReplies(data.quick_replies);
        restoreNavigation();
      } catch (error) {
        addMessage("assistant", `Erreur : ${error.message}`);
      } finally {
        button.disabled = false;
        input.focus();
      }
    });

    loadState().then((ui) => {
      const preferredLayout = ui?.layout || loadLayout();
      const preferredOpen = ui?.open ?? loadOpen();
      state.ui = { ...state.ui, ...(ui || {}) };
      drawer.hidden = !preferredOpen;
      applyLayout(preferredLayout, false, preferredOpen);
      if (!preferredOpen) {
        saveUi({ open: false, layout: preferredLayout });
        return;
      }
      saveUi({ open: true, layout: state.layout, width: state.ui.width });
    }).catch(() => {
      state.sessions = [{ id: "local-1", title: "Session 1" }];
      state.activeSessionId = "local-1";
      state.history = [];
      renderTabs();
      renderConversation();
      drawer.hidden = false;
      applyLayout(loadLayout(), false, true);
    });

    window.addEventListener("resize", () => {
      if (state.layout === "docked") syncDockWidth();
    });
    refreshHealth();
  });
})();
