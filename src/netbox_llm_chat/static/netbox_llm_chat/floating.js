(() => {
  document.querySelectorAll(".netbox-llm-chat-widget").forEach((widget) => {
    if (widget.dataset.initialized === "1") return;
    widget.dataset.initialized = "1";

    const fab = widget.querySelector(".netbox-llm-chat-fab");
    const drawer = widget.querySelector(".netbox-llm-chat-drawer");
    const closeBtn = widget.querySelector(".netbox-llm-chat-close");
    const clearBtn = widget.querySelector(".netbox-llm-chat-clear");
    const dockBtn = widget.querySelector(".netbox-llm-chat-dock");
    const tabAddBtn = widget.querySelector(".netbox-llm-chat-tab-add");
    const tabsEl = widget.querySelector(".netbox-llm-chat-tabs");
    const dragHandle = widget.querySelector("[data-netbox-llm-chat-drag-handle]");
    const resizeHandle = widget.querySelector(".netbox-llm-chat-resize-handle");
    const form = widget.querySelector(".netbox-llm-chat-drawer-form");
    const input = widget.querySelector(".netbox-llm-chat-drawer-input");
    const messages = widget.querySelector(".netbox-llm-chat-drawer-messages");
    const status = widget.querySelector(".netbox-llm-chat-drawer-status");

    const POS_KEY = "netbox-llm-chat-window-pos-v3";
    const LAYOUT_KEY = "netbox-llm-chat-layout-v1";
    const OPEN_KEY = "netbox-llm-chat-open-v1";
    const csrf = () => document.cookie.split(";").map(x => x.trim()).find(x => x.startsWith("csrftoken="))?.split("=").slice(1).join("=") || form.querySelector("input[name=csrfmiddlewaretoken]")?.value || "";

    const state = {
      sessions: [],
      activeSessionId: null,
      history: [],
      pendingWrite: null,
      layout: "floating",
      ui: { open: true, layout: "docked", width: 320 },
    };

    const api = {
      history: "/plugins/netbox-llm-chat/api/history/",
      newSession: "/plugins/netbox-llm-chat/api/sessions/new/",
      selectSession: "/plugins/netbox-llm-chat/api/sessions/select/",
      deleteSession: "/plugins/netbox-llm-chat/api/sessions/delete/",
      clearSession: "/plugins/netbox-llm-chat/api/history/clear/",
      chat: "/plugins/netbox-llm-chat/api/chat/",
      health: "/plugins/netbox-llm-chat/api/health/",
      ui: "/plugins/netbox-llm-chat/api/ui/",
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
        body: JSON.stringify(state.ui),
      }).catch(() => {});
    }

    function syncDockWidth() {
      const width = Math.round(drawer.getBoundingClientRect().width || state.ui.width || 320);
      document.body.style.setProperty("--netbox-llm-chat-docked-width", `${width}px`);
      state.ui.width = width;
    }

    function applyLayout(layout, persist = true, visible = !drawer.hidden) {
      state.layout = layout === "docked" ? "docked" : "floating";
      drawer.dataset.layout = state.layout;
      drawer.classList.toggle("docked", state.layout === "docked");
      document.body.classList.toggle("netbox-llm-chat-docked", visible && state.layout === "docked");
      if (!(visible && state.layout === "docked")) {
        document.body.style.removeProperty("--netbox-llm-chat-docked-width");
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
        document.body.style.removeProperty("--netbox-llm-chat-docked-width");
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
            html += "<pre class=\"netbox-llm-chat-code\"><code>";
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
            html += "<table class=\"netbox-llm-chat-table\"><thead><tr>" + cells.map(x => `<th>${inline(x)}</th>`).join("") + "</tr></thead><tbody>";
            table = true;
          } else {
            html += "<tr>" + cells.map(x => `<td>${inline(x)}</td>`).join("") + "</tr>";
          }
          continue;
        }

        closeTable();
        const value = line.trim();
        if (!value) { html += "<div class=\"netbox-llm-chat-spacer\"></div>"; continue; }
        if (value.startsWith("### ")) html += `<h4>${inline(value.slice(4))}</h4>`;
        else if (value.startsWith("## ")) html += `<h3>${inline(value.slice(3))}</h3>`;
        else if (value.startsWith("# ")) html += `<h2>${inline(value.slice(2))}</h2>`;
        else if (/^[-*] /.test(value)) html += `<div class=\"netbox-llm-chat-list-item\">• ${inline(value.slice(2))}</div>`;
        else html += `<p>${inline(value)}</p>`;
      }
      closeTable();
      if (codeBlock) html += "</code></pre>";
      return html;
    }

    function addMessage(role, text) {
      const row = document.createElement("div");
      row.className = `netbox-llm-chat-msg ${role}`;
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
      messages.appendChild(row);
      messages.scrollTop = messages.scrollHeight;
    }

    function renderConversation() {
      messages.replaceChildren();
      const intro = document.createElement("div");
      intro.className = "netbox-llm-chat-intro";
      intro.textContent = "Assistant NetBox. Lecture/écriture selon la configuration globale. Les écritures demandent une confirmation.";
      messages.appendChild(intro);
      state.history.forEach(item => addMessage(item.role, item.text));
      renderPendingControls();
    }

    function renderPendingControls() {
      messages.querySelector("#netbox-llm-chat-confirm-wrap")?.remove();
      if (!state.pendingWrite) return;
      const wrap = document.createElement("div");
      wrap.id = "netbox-llm-chat-confirm-wrap";
      wrap.className = "d-flex gap-2 mt-2 justify-content-end";
      const yes = document.createElement("button");
      yes.type = "button";
      yes.className = "btn btn-sm btn-success";
      yes.textContent = "Confirmer";
      const no = document.createElement("button");
      no.type = "button";
      no.className = "btn btn-sm btn-outline-danger";
      no.textContent = "Annuler";
      const sendQuick = async (message) => {
        addMessage("user", message);
        const response = await fetch(api.chat, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          body: JSON.stringify({ message, conversation_id: state.activeSessionId }),
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
      };
      yes.addEventListener("click", async () => {
        yes.disabled = true; no.disabled = true;
        try { await sendQuick("oui"); } catch (error) { addMessage("assistant", `Erreur : ${error.message}`); }
        finally { yes.disabled = false; no.disabled = false; }
      });
      no.addEventListener("click", async () => {
        yes.disabled = true; no.disabled = true;
        try { await sendQuick("non"); } catch (error) { addMessage("assistant", `Erreur : ${error.message}`); }
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
        tab.className = `netbox-llm-chat-tab${session.id === state.activeSessionId ? " active" : ""}`;
        tab.dataset.sessionId = session.id;

        const label = document.createElement("span");
        label.className = "netbox-llm-chat-tab-label";
        label.textContent = session.title || "Session";

        const close = document.createElement("button");
        close.type = "button";
        close.className = "netbox-llm-chat-tab-close";
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
      const r = await fetch(api.history, { credentials: "same-origin" });
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
      const r = await fetch(api.newSession, { method: "POST", headers: { "X-CSRFToken": csrf() } });
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
        body: JSON.stringify({ session_id: sessionId }),
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
        body: JSON.stringify({ session_id: sessionId }),
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
      event.stopPropagation();
      state.history = [];
      state.pendingWrite = null;
      renderConversation();
      saveUi({ open: true, layout: state.layout });
      try {
        const r = await fetch(api.clearSession, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          body: JSON.stringify({ session_id: state.activeSessionId }),
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
        document.body.classList.add("netbox-llm-chat-docked");
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
      input.value = "";
      addMessage("user", message);
      const button = form.querySelector("button[type='submit']");
      button.disabled = true;
      try {
        const response = await fetch(api.chat, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
          body: JSON.stringify({ message, conversation_id: state.activeSessionId }),
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
