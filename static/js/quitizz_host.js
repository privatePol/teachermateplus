(() => {
  "use strict";
  const root = document.getElementById("qt-host");
  if (!root) return;
  const byId = (id) => document.getElementById(id);
  let latest = 0, pending = false, available = false;
  let requested = 0, rendered = 0, unavailable = false;
  function lockControls() {
    root.querySelectorAll("button").forEach((button) => { button.disabled = pending || !available; });
  }
  lockControls();
  function render(state, sequence) {
    if (unavailable || state.version < latest || sequence < rendered) return;
    // HTTP request ordering preserves counts within a question while newer
    // lifecycle versions reset them. Socket signals never supply a count.
    rendered = sequence;
    latest = state.version;
    const changed = String(state.version) !== root.dataset.version;
    root.dataset.version = String(state.version);
    byId("qt-count").textContent = String(state.participant_count);
    byId("qt-answered").textContent = String(state.answered_count);
    byId("qt-session-status").textContent = `${state.status.replaceAll("_", " ")} · Current question: ${state.position}`;
    const commands = byId("qt-command");
    if (commands && changed) {
      commands.querySelectorAll("button").forEach((button) => button.remove());
      commands.querySelector("[name=version]").value = state.version;
      const add = (action, text) => {
        const button = document.createElement("button"); button.name = "action"; button.value = action;
        button.className = "btn btn-outline-success"; button.textContent = text; commands.append(button);
      };
      const terminal = ["COMPLETED", "CANCELLED"].includes(state.status);
      commands.hidden = terminal;
      if (!terminal) {
        add(state.joining_open ? "close_joining" : "open_joining", state.joining_open ? "Close joining" : "Open joining");
        if (state.status === "LOBBY") add("start", "Start");
        if (state.status === "QUESTION_CLOSED") add(state.question_opened ? "reveal" : "open_question", state.question_opened ? "Reveal answer" : "Open Question");
        if (state.status === "QUESTION_OPEN") add("close_question", "Close Question");
        if (state.status === "ANSWER_REVEALED") {
          if (state.position < state.question_count) add("next", "Next Question");
          add("complete", "End / Complete");
        }
        add("cancel", "Cancel session");
      }
      const joining = byId("qt-joining"); joining.replaceChildren();
      if (state.joining_open) {
        const image = document.createElement("img"); image.className = "qt-qr";
        image.src = `${root.dataset.qrUrl}?v=${state.version}`; image.alt = "Scan this QR code to join this QuiTizz session";
        joining.append(image);
      }
      const label = document.createElement("p"); label.textContent = state.joining_open ? "Joining is open. Scan, enter a nickname, and wait in the lobby." : "Joining is closed."; joining.append(label);
    }
    const list = byId("qt-participants");
    const existingIds = Array.from(list.children, (item) => item.dataset.playerId || "").join(",");
    const nextIds = state.participants.map((player) => player.public_id).join(",");
    if (existingIds !== nextIds) {
      const focusedId = list.contains(document.activeElement) ? document.activeElement.closest("[data-player-id]")?.dataset.playerId : null;
      list.replaceChildren();
      state.participants.forEach((player) => {
        const item = document.createElement("li"); item.className = "list-group-item d-flex justify-content-between align-items-center gap-2";
        item.dataset.playerId = player.public_id;
        const name = document.createElement("span"); name.textContent = player.nickname; item.append(name);
        if (commands && !["COMPLETED", "CANCELLED"].includes(state.status)) {
          const form = document.createElement("form"); form.method = "post"; form.action = commands.action;
          const fields = {csrfmiddlewaretoken: commands.querySelector("[name=csrfmiddlewaretoken]").value, version: state.version, participant: player.public_id};
          Object.entries(fields).forEach(([key, value]) => { const input = document.createElement("input"); input.type = "hidden"; input.name = key; input.value = value; form.append(input); });
          const button = document.createElement("button"); button.name = "action"; button.value = "remove"; button.className = "btn btn-sm btn-outline-danger";
          button.textContent = "Remove"; button.setAttribute("aria-label", `Remove ${player.nickname}`); form.append(button); item.append(form);
        }
        list.append(item);
      });
      if (focusedId) {
        const replacement = Array.from(list.children).find((item) => item.dataset.playerId === focusedId);
        (replacement?.querySelector("button") || commands?.querySelector("button"))?.focus();
      }
    }
    root.querySelectorAll("[name=version]").forEach((input) => { input.value = state.version; });
    if (["COMPLETED", "CANCELLED"].includes(state.status)) list.querySelectorAll("form").forEach((form) => form.remove());
    available = true; lockControls();
  }
  async function refresh() {
    const sequence = ++requested;
    try {
      const response = await fetch(root.dataset.stateUrl, {credentials: "same-origin", cache: "no-store"});
      if (!response.ok || !response.headers.get("Content-Type")?.includes("application/json")) {
        if ([401, 403, 404].includes(response.status) || response.redirected || response.ok) {
          unavailable = true; available = false; lockControls();
          root.querySelectorAll("#qt-joining, #qt-participants, details").forEach((element) => { element.hidden = true; });
          transport.stop(); byId("qt-connection").textContent = "Session unavailable.";
        }
        return;
      }
      const state = await response.json(); render(state, sequence); return state;
    } catch (_) { /* Transport will recover. */ }
  }
  const transport = window.QuiTizzRealtime({url: root.dataset.socketUrl, recover: refresh, indicator: byId("qt-connection")});
  root.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!form.matches("form") || !event.submitter) return;
    event.preventDefault();
    if (pending || !available) return;
    const data = new FormData(form, event.submitter);
    pending = true; root.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    try {
      const response = await fetch(form.action, {method: "POST", credentials: "same-origin", body: data});
      byId("qt-host-error").hidden = response.ok;
      if (!response.ok) byId("qt-host-error").textContent = "Control could not be accepted. The current session has been recovered; try the available control again.";
    } catch (_) { byId("qt-host-error").hidden = false; byId("qt-host-error").textContent = "Connection interrupted. Recovering current session."; }
    finally { pending = false; lockControls(); }
    await transport.refresh();
  });
})();
