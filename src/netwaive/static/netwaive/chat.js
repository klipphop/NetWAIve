(() => {
  const messages = document.getElementById("netwaive-messages");
  const form = document.getElementById("netwaive-form");
  const input = document.getElementById("netwaive-input");
  const status = document.getElementById("netwaive-status");
  const clearButton = document.getElementById("netwaive-clear");
  let conversationId = null;
  let pendingWrite = null;

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

  const add = (role, text) => {
    const el = document.createElement("div");
    el.className = `mb-2 ${role === "user" ? "text-end" : ""}`;
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
    messages.appendChild(el);
    messages.scrollTop = messages.scrollHeight;
  };

  const renderPendingControls = () => {
    document.getElementById("netwaive-confirm-wrap")?.remove();
    if (!pendingWrite) return;
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
      add("user", message);
      const response = await fetch("/plugins/netwaive/api/chat/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]")?.value || "" },
        body: JSON.stringify({ message, conversation_id: conversationId, approve_pending: approvePending }),
      });
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) throw new Error(`Réponse HTTP ${response.status} non JSON`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Erreur LLM");
      conversationId = data.conversation_id || conversationId;
      pendingWrite = data.pending_write || null;
      add("assistant", data.message || data.answer || JSON.stringify(data));
      renderPendingControls();
    };

    yes.addEventListener("click", async () => {
      yes.disabled = true; no.disabled = true;
      try { await sendQuick("oui", true); } catch (error) { add("assistant", `Erreur : ${error.message}`); }
      finally { yes.disabled = false; no.disabled = false; }
    });
    no.addEventListener("click", async () => {
      yes.disabled = true; no.disabled = true;
      try { await sendQuick("non"); } catch (error) { add("assistant", `Erreur : ${error.message}`); }
      finally { yes.disabled = false; no.disabled = false; }
    });

    wrap.appendChild(yes);
    wrap.appendChild(no);
    messages.appendChild(wrap);
    messages.scrollTop = messages.scrollHeight;
  };

  clearButton?.addEventListener("click", async () => {
    const token = document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
    messages.replaceChildren();
    conversationId = null;
    pendingWrite = null;
    renderPendingControls();
    const response = await fetch("/plugins/netwaive/api/history/clear/", {
      method: "POST",
      headers: { "X-CSRFToken": token, "Cache-Control": "no-store" },
    });
    const data = await response.json();
    conversationId = data.active_session_id || null;
  });

  fetch("/plugins/netwaive/api/history/", { credentials: "same-origin" })
    .then(r => r.json())
    .then(data => {
      (data.history || []).forEach(item => add(item.role, item.text));
      conversationId = data.active_session_id || conversationId;
      pendingWrite = data.pending_write || null;
      renderPendingControls();
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
    input.value = "";
    add("user", message);
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      const response = await fetch("/plugins/netwaive/api/chat/", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]")?.value || "" },
        body: JSON.stringify({ message, conversation_id: conversationId }),
      });
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) throw new Error(`Réponse HTTP ${response.status} non JSON`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Erreur LLM");
      conversationId = data.conversation_id || conversationId;
      pendingWrite = data.pending_write || null;
      add("assistant", data.message || data.answer || JSON.stringify(data));
      renderPendingControls();
    } catch (error) {
      add("assistant", `Erreur : ${error.message}`);
    } finally {
      button.disabled = false;
      input.focus();
    }
  });
})();
