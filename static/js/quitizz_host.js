(() => {
  "use strict";
  const root = document.getElementById("qt-host");
  if (!root) return;
  const byId = (id) => document.getElementById(id);
  const stage = byId("qt-presentation");
  const presentation = stage && window.QuiTizzPresentation?.create(stage);
  const audio = window.QuiTizzAudio?.(byId("qt-audio"), {speaker: false, sessionId: root.dataset.sessionId});
  let joiningOpen = false;
  let latest = 0, pending = false, available = false;
  let requested = 0, rendered = 0, unavailable = false;
  let liveState, reviewRequest = 0;
  function lockControls() {
    root.querySelectorAll("button").forEach((button) => { button.disabled = pending || !available; });
    const select = byId("qt-next-question"), state = liveState;
    if (select && state) {
      select.disabled = pending || !available;
      const row = state.navigator?.find((row) => row.id === select.value), terminal = ["COMPLETED", "CANCELLED"].includes(state.status);
      byId("qt-select-next").disabled = pending || !available || terminal || !row?.can_select;
      byId("qt-open-selected").disabled = pending || !available || terminal || !row?.can_select || state.playback_mode !== "MANUAL" || state.paused || !["LOBBY", "ANSWER_REVEALED", "QUESTION_CLOSED"].includes(state.status) || (state.status === "QUESTION_CLOSED" && state.question_opened);
      byId("qt-review-question").disabled = pending || !available || !row?.can_review;
    }
  }
  lockControls();
  function render(state, sequence) {
    if (unavailable || state.version < latest || sequence < rendered) return;
    // HTTP request ordering preserves counts within a question while newer
    // lifecycle versions reset them. Socket signals never supply a count.
    rendered = sequence;
    latest = state.version;
    liveState = state;
    const changed = !available || String(state.version) !== root.dataset.version;
    root.dataset.version = String(state.version);
    presentation?.render(state);
    const select = byId("qt-next-question");
    if (select && state.navigator) {
      const previous = select.value; select.replaceChildren();
      state.navigator.forEach((row) => { const option = document.createElement("option"); option.value = row.id; option.textContent = `${row.position}. ${row.preview} · ${row.status}`; select.append(option); });
      select.value = state.navigator.some((row) => row.id === previous) ? previous : state.upcoming_question || state.navigator.find((row) => row.can_select)?.id || state.navigator[0]?.id || "";
      byId("qt-navigation-summary").textContent = `${state.completed_count} completed · ${state.pending_count} pending. Original question numbers remain unchanged.`;
    }
    joiningOpen = Boolean(state.joining_open);
    const mode = byId("qt-playback-mode");
    if (mode) { mode.value = state.playback_mode || "MANUAL"; const auto = mode.querySelector('[value="AUTOMATIC"]'); if (auto) auto.disabled = !state.automatic_available; }
    byId("qt-count").textContent = String(state.participant_count);
    byId("qt-answered").textContent = String(state.answered_count);
    byId("qt-session-status").textContent = `${state.status.replaceAll("_", " ")} · Current question: ${state.position}`;
    const cancelledNext = byId("qt-cancelled-next");
    if (cancelledNext) cancelledNext.hidden = state.status !== "CANCELLED";
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
        if (!state.paused) {
          if (state.status === "LOBBY") add("start", "Start");
          if (state.status === "QUESTION_CLOSED") add(state.question_opened ? "reveal" : "open_question", state.question_opened ? "Reveal answer" : "Open Question");
          if (state.status === "QUESTION_OPEN") add("close_question", "Close Question");
          if (state.status === "ANSWER_REVEALED") {
            if (!state.results_screen && (state.pending_count ?? state.question_count - state.position) > 0) add("next", "Next Question");
            if (!state.results_screen || state.pending_count === 0) add("complete", "End / Complete");
            if (state.results_screen) {
              for (const [screen, label] of [["ANSWER", "Show Answer"], ["FASTEST_FIVE", "Show Fastest Five"], ["HIGHEST_SCORES", "Show Highest Scores"]]) {
                if (state.playback_mode !== "MANUAL" || (screen === "FASTEST_FIVE" && !state.presentation_options.show_fastest) || (screen === "HIGHEST_SCORES" && !state.presentation_options.show_scores)) continue;
                add("show_screen", label); commands.lastChild.dataset.screen = screen;
              }
            }
          }
        }
        if (state.paused) add("resume", "Resume");
        else if (state.playback_mode === "AUTOMATIC" && state.show_phase !== "NONE") add("pause", "Pause");
        if (state.playback_mode === "AUTOMATIC" && !state.paused) {
          if (["QUESTION_OPEN", "QUESTION_CLOSED"].includes(state.status) && state.question_opened) add("reveal_now", "Reveal Now");
          if (state.status === "ANSWER_REVEALED" && (state.pending_count ?? state.question_count - state.position) > 0) add("next_now", "Next Now");
        }
        add("set_mode", "Apply playback mode");
        add("end_challenge", "End Challenge");
        add("cancel", "Cancel session");
      }
      const joining = byId("qt-joining"); joining.replaceChildren();
      if (state.joining_open) {
        const image = document.createElement("img"); image.className = "qt-qr";
        image.src = `${root.dataset.qrUrl}?v=${state.version}`; image.alt = "Scan this QR code to join this QuiTizz session";
        joining.append(image);
        const copy = document.createElement("button"); copy.type = "button"; copy.id = "qt-copy-link"; copy.className = "btn btn-outline-success"; copy.textContent = "Copy Join Link"; joining.append(copy);
        const confirmation = document.createElement("p"); confirmation.id = "qt-copy-status"; confirmation.setAttribute("role", "status"); joining.append(confirmation);
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
          const form = document.createElement("form"); form.method = "post"; form.setAttribute("action", commands.getAttribute("action"));
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
          root.querySelectorAll("#qt-joining, #qt-participants, #qt-presentation, #qt-navigator, details").forEach((element) => { element.hidden = true; });
          presentation?.clear(); audio?.clear();
          transport.stop(); byId("qt-connection").textContent = "Session unavailable.";
        }
        return;
      }
      const state = await response.json(); render(state, sequence); return state;
    } catch (_) { /* Transport will recover. */ }
  }
  root.addEventListener("click", async (event) => {
    if (event.target.id === "qt-review-question") {
      const select = byId("qt-next-question"), id = select.value, sequence = ++reviewRequest, panel = byId("qt-review");
      if (!available || !liveState.navigator?.some((row) => row.id === id && row.can_review)) return;
      panel.hidden = false; panel.textContent = "Loading historical review…";
      try {
        const target = root.dataset.reviewUrl.replace(/review\/[^/]+\/$/, `review/${encodeURIComponent(id)}/`);
        const response = await fetch(target, {credentials: "same-origin", cache: "no-store"});
        if (!response.ok) throw new Error();
        const review = await response.json();
        if (unavailable || sequence !== reviewRequest || select.value !== id) return;
        panel.replaceChildren();
        const title = document.createElement("h3"); title.textContent = `Historical review only · Question ${review.question.position}`; panel.append(title);
        const content = document.createElement("p"); content.textContent = review.question.prompt; panel.append(content);
        Object.entries(review.question.choices).forEach(([letter, choice]) => { const p = document.createElement("p"); p.textContent = `${letter}. ${choice}`; panel.append(p); });
        const result = document.createElement("p"); result.textContent = `Correct: ${review.correct_choice} · ${review.fastest_five.map((row) => `${row.rank}. ${row.nickname}: ${row.elapsed_ms} ms`).join(" · ") || "No correct responses"}`; panel.append(result);
      } catch (_) { if (!unavailable && sequence === reviewRequest && select.value === id) panel.textContent = "Historical review unavailable. Recover the live session before retrying."; }
      return;
    }
    if (event.target.id !== "qt-copy-link" || !available || !joiningOpen) return;
    const version = latest, status = byId("qt-copy-status");
    try {
      const response = await fetch(root.dataset.joinLinkUrl, {credentials: "same-origin", cache: "no-store"});
      if (!response.ok) throw new Error();
      const {join_url: url} = await response.json();
      if (!joiningOpen || version !== latest || unavailable) return;
      const target = new URL(url, window.location.href);
      if (!target.hash || target.search || target.origin !== window.location.origin) throw new Error();
      try {
        if (!window.navigator?.clipboard?.writeText) throw new Error();
        await window.navigator.clipboard.writeText(url);
        if (joiningOpen && version === latest && status) status.textContent = "Join link copied";
      } catch (_) {
        if (!joiningOpen || version !== latest || !status) return;
        status.textContent = "Copy unavailable. Select and copy the complete link below.";
        const field = document.createElement("textarea"); field.readOnly = true; field.value = url; field.setAttribute("aria-label", "Complete join link including capability fragment"); status.append(field); field.focus(); field.select();
      }
    } catch (_) { if (status) status.textContent = "Join link unavailable. Recover the current joining state."; }
  });
  const transport = window.QuiTizzRealtime({url: root.dataset.socketUrl, recover: refresh, indicator: byId("qt-connection")});
  root.addEventListener("change", (event) => { if (event.target.id === "qt-next-question") { reviewRequest++; byId("qt-review").hidden = true; lockControls(); } });
  root.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!form.matches("form") || !event.submitter) return;
    event.preventDefault();
    if (pending || !available) return;
    const cancelling = event.submitter.value === "cancel";
    if (cancelling && !window.confirm("Cancel this session and stop gameplay? Saved answers and audit history will be kept. A new session requires players to rejoin using its new QR/link.")) return;
    const data = new FormData(form, event.submitter);
    if (cancelling) data.append("confirm_end", "1");
    if (event.submitter.dataset?.screen) data.append("screen", event.submitter.dataset.screen);
    pending = true; root.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    try {
      const response = await fetch(form.getAttribute("action"), {method: "POST", credentials: "same-origin", headers: {Accept: "application/json"}, body: data});
      byId("qt-host-error").hidden = response.ok;
      if (!response.ok) {
        let detail;
        try { detail = (await response.json()).error; } catch (_) { /* Standard CSRF/auth responses may be HTML. */ }
        byId("qt-host-error").textContent = typeof detail === "string" ? `${detail} Recovering current controls; review them before retrying.` : "Control could not be accepted. Recovering current controls; review them before retrying.";
      }
    } catch (_) { byId("qt-host-error").hidden = false; byId("qt-host-error").textContent = "Connection interrupted. Recovering current session."; }
    finally { pending = false; lockControls(); }
    await transport.refresh();
  });
})();
