(() => {
  "use strict";
  const root = document.getElementById("qt-host");
  if (!root) return;
  async function refresh() {
    if (!document.hidden) {
      try {
        const response = await fetch(root.dataset.stateUrl, {credentials: "same-origin", cache: "no-store"});
        if (!response.ok) return;
        const state = await response.json();
        if (String(state.version) !== root.dataset.version) { window.location.reload(); return; }
        document.getElementById("qt-count").textContent = String(state.participant_count);
        const list = document.getElementById("qt-participants");
        const existingIds = Array.from(list.children, (item) => item.dataset.playerId || "").join(",");
        const nextIds = state.participants.map((player) => player.public_id).join(",");
        // Preserve keyboard focus and avoid rebuilding the same list every refresh.
        if (existingIds !== nextIds && !list.contains(document.activeElement)) {
          list.replaceChildren();
          state.participants.forEach((player) => {
          const item = document.createElement("li"); item.className = "list-group-item d-flex justify-content-between align-items-center gap-2";
          item.dataset.playerId = player.public_id;
          const name = document.createElement("span"); name.textContent = player.nickname; item.append(name);
          const commands = document.getElementById("qt-command");
          if (commands) {
            const form = document.createElement("form"); form.method = "post"; form.action = commands.action;
            const fields = {csrfmiddlewaretoken: commands.querySelector("[name=csrfmiddlewaretoken]").value, version: state.version, participant: player.public_id};
            Object.entries(fields).forEach(([key, value]) => { const input = document.createElement("input"); input.type = "hidden"; input.name = key; input.value = value; form.append(input); });
            const button = document.createElement("button"); button.name = "action"; button.value = "remove"; button.className = "btn btn-sm btn-outline-danger"; button.textContent = "Remove"; button.setAttribute("aria-label", `Remove ${player.nickname}`); form.append(button); item.append(form);
          }
          list.append(item);
          });
        }
        if (state.status === "COMPLETED" || state.status === "CANCELLED") return;
      } catch (_) { /* Retry at the modest transport interval. */ }
    }
    window.setTimeout(refresh, 5000);
  }
  window.setTimeout(refresh, 5000);
})();
