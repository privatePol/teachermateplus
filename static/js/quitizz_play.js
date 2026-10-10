/* HTTP mutations and recovery, WebSocket invalidations, local countdown. */
(() => {
  "use strict";
  const root = document.getElementById("qt-play");
  if (!root) return;
  let fragment = window.location.hash.slice(1);
  window.history.replaceState(null, "", window.location.pathname);
  const byId = (id) => document.getElementById(id);
  const message = byId("qt-message"), error = byId("qt-error");
  const join = byId("qt-join"), question = byId("qt-question"), answers = byId("qt-answers");
  const feedback = byId("qt-feedback");
  let current = null, terminal = false, pending = false, canAnswer = false, transport = null, latest = 0, unavailable = false;
  let deadline = 0, serverOffset = 0;
  let celebrated = "";
  let finaleCelebrated = false;
  const csrf = join.querySelector("[name=csrfmiddlewaretoken]").value;
  function showError(value) { error.textContent = value; error.hidden = false; }
  async function request(url, data) {
    const options = {credentials: "same-origin", cache: "no-store", headers: {Accept: "application/json"}};
    if (data) {
      options.method = "POST";
      options.headers["X-CSRFToken"] = csrf;
      options.headers["Content-Type"] = "application/x-www-form-urlencoded";
      options.body = new URLSearchParams(data);
    }
    const response = await fetch(url, options);
    let value;
    try { value = await response.json(); } catch (_) { value = {error: "Request cannot be accepted. Refresh and try again."}; }
    if (!response.ok) { const failure = new Error(value.error); failure.status = response.status; throw failure; }
    return value;
  }
  function lockButtons() { answers.querySelectorAll("button").forEach((button) => { button.disabled = true; }); }
  function render(state) {
    if (unavailable || state.version < latest) return;
    latest = state.version;
    error.hidden = true;
    join.hidden = true;
    terminal = state.status === "COMPLETED" || state.status === "CANCELLED";
    root.dataset.phase = state.status;
    const timerBlock = byId("qt-timer-block"); if (timerBlock) timerBlock.hidden = state.status !== "QUESTION_OPEN";
    const result = byId("qt-result"); if (result) result.hidden = !state.summary;
    const title = byId("qt-game-title"); if (title) title.textContent = state.title || "";
    const labels = {READY: "Waiting for the host.", LOBBY: "You joined. Waiting for the host to start.", QUESTION_OPEN: "Choose one answer.", QUESTION_CLOSED: "Waiting for the host to open or reveal the question.", ANSWER_REVEALED: "Answer revealed. Waiting for the next question.", COMPLETED: "Session completed.", CANCELLED: "Session cancelled."};
    message.textContent = `${state.nickname} · ${labels[state.status] || "Waiting for the host."}`;
    feedback.hidden = true;
    if (!state.question) { question.hidden = true; current = null; lockButtons(); return; }
    question.hidden = false;
    const q = state.question;
    byId("qt-prompt").textContent = `${q.position}. ${q.prompt}`;
    serverOffset = Date.parse(state.server_now) - Date.now();
    deadline = Date.parse(q.deadline);
    if (current !== q.id) {
      current = q.id;
      answers.replaceChildren();
      Object.entries(q.choices).forEach(([letter, text]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn qt-answer";
        button.textContent = `${letter}. ${text}`;
        button.addEventListener("click", async () => {
          if (pending || !canAnswer) return;
          pending = true; lockButtons();
          try {
            await request(root.dataset.answerUrl, {question: q.id, choice: letter});
            canAnswer = false;
            message.textContent = "Answer submitted and locked. Waiting for reveal.";
          } catch (failure) { showError(failure.message); }
          finally { pending = false; }
          if (transport) await transport.refresh(); else await refresh();
        });
        answers.append(button);
      });
    }
    canAnswer = state.can_answer && !pending;
    answers.querySelectorAll("button").forEach((button) => { button.disabled = !canAnswer; });
    if (state.accepted && !state.feedback) message.textContent = "Answer submitted and locked. Waiting for reveal.";
    if (!state.can_answer && !state.accepted && state.status === "QUESTION_OPEN") message.textContent = "Time ended. Waiting for the host to reveal.";
    if (state.feedback) {
      feedback.className = `qt-feedback mt-3 ${state.feedback.is_correct ? "qt-success" : "qt-neutral"}`;
      feedback.textContent = `${state.feedback.answered ? (state.feedback.is_correct ? "Correct!" : "Incorrect.") : "No accepted answer."} Correct answer: ${state.feedback.correct_choice}. +${state.feedback.points} points.`;
      feedback.hidden = false;
      if (state.feedback.is_correct && celebrated !== q.id) {
        celebrated = q.id; window.QuiTizzPresentation?.celebrate(root);
      }
    }
    if (state.summary) {
      feedback.textContent += ` Final score: ${state.summary.total_score}. Correct: ${state.summary.correct_count}. Your rank: ${state.summary.rank || "—"}. Thanks for playing!`;
      feedback.hidden = false;
      const score = byId("qt-result-score");
      if (score) score.textContent = `${state.summary.rank === 1 ? "Champion! " : ""}${state.summary.total_score} points · Your rank: ${state.summary.rank || "—"}`;
      if (state.summary.rank === 1 && !finaleCelebrated) {
        finaleCelebrated = true; window.QuiTizzPresentation?.celebrate(root, true);
      }
    }
  }
  async function refresh() {
    try {
      const state = await request(root.dataset.stateUrl); render(state);
      if (terminal && transport) { transport.stop(); byId("qt-connection").textContent = "Session ended."; }
      return state;
    }
    catch (failure) {
      if ([401, 403, 404].includes(failure.status)) {
        unavailable = true; terminal = true; canAnswer = false; current = null;
        question.hidden = true; feedback.hidden = true; join.hidden = true; lockButtons();
        const result = byId("qt-result"); if (result) result.hidden = true;
        window.QuiTizzPresentation?.cleanup();
        message.textContent = "Session unavailable.";
        if (transport) transport.stop();
        byId("qt-connection").textContent = "Session unavailable.";
      }
      showError(failure.message);
    }
  }
  function schedule() {
    if (terminal || transport) return;
    transport = window.QuiTizzRealtime({url: root.dataset.socketUrl,
      prepare: () => request(root.dataset.socketIdentityUrl, {}), recover: refresh,
      indicator: byId("qt-connection")});
  }
  join.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = join.querySelector("button"); button.disabled = true;
    try {
      await request(root.dataset.joinUrl, {nickname: byId("qt-nickname").value});
      terminal = false; await refresh(); schedule();
    } catch (failure) { showError(failure.message); }
    finally { button.disabled = false; }
  });
  const countdownTimer = window.setInterval(() => {
    if (!current) return;
    const remaining = Math.max(0, Math.ceil((deadline - Date.now() - serverOffset) / 1000));
    byId("qt-countdown").textContent = String(remaining);
    if (!remaining) lockButtons();
  }, 250);
  window.addEventListener?.("pagehide", () => { window.clearInterval(countdownTimer); });
  (async () => {
    try {
      const state = await request(root.dataset.stateUrl);
      fragment = ""; render(state); schedule(); return;
    } catch (_) { /* A new browser needs the fragment exchange. */ }
    if (!fragment) { message.textContent = "Scan the current session QR code to join."; return; }
    try {
      await request(root.dataset.exchangeUrl, {capability: fragment});
      fragment = "";
      message.textContent = "Enter your nickname to join.";
      join.hidden = false; byId("qt-nickname").focus();
    } catch (failure) { fragment = ""; showError(failure.message); }
  })();
})();
