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
  let current = null, terminal = false, canAnswer = false, transport = null, latest = 0, unavailable = false;
  let submission = null, requested = 0, rendered = 0;
  const retry = byId("qt-retry-answer");
  let deadline = 0, serverOffset = 0, paused = false, frozenSeconds = 0;
  let celebrated = "";
  let finaleCelebrated = false;
  let previousPhase = "";
  let hasQuestion = false;
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
    const controller = window.AbortController ? new window.AbortController() : null;
    if (controller) options.signal = controller.signal;
    let timer;
    try {
      return await Promise.race([
        (async () => {
          const response = await fetch(url, options);
          let value;
          try { value = await response.json(); } catch (_) { value = null; }
          if (!response.ok || response.redirected || !value || typeof value !== "object" || Array.isArray(value)) {
            const failure = new Error(typeof value?.error === "string" ? value.error : "Could not confirm the request. Recover current state before retrying.");
            failure.status = response.status; throw failure;
          }
          return value;
        })(),
        new Promise((_, reject) => {
          timer = window.setTimeout(() => {
            controller?.abort(); reject(new Error("Confirmation timed out. Recovering your answer; it may already have been recorded."));
          }, 10000);
        }),
      ]);
    } finally { window.clearTimeout(timer); }
  }
  function lockButtons() { answers.querySelectorAll("button").forEach((button) => { button.disabled = true; }); }
  function showSubmission() {
    answers.querySelectorAll("button").forEach((button) => {
      const selected = Boolean(submission?.choice && submission.choice === button.dataset.choice);
      button.className = `btn qt-answer${selected ? " qt-selected-answer" : ""}`;
      button.setAttribute("aria-pressed", String(selected));
      button.textContent = `${button.dataset.label}${selected ? " ✓" : ""}`;
      button.disabled = !canAnswer || Boolean(submission);
    });
    question.setAttribute("aria-busy", String(submission?.status === "pending"));
    if (retry) { retry.hidden = !submission || ["pending", "confirmed"].includes(submission.status); retry.disabled = !canAnswer || submission?.status !== "retry"; }
    if (!submission) return;
    if (submission.status === "confirmed") message.textContent = submission.choice ? `Answer ${submission.choice} recorded ✓` : "Answer recorded. Waiting for reveal.";
    else if (submission.status === "pending") message.textContent = "Submitting...";
    else if (submission.status === "retry") message.textContent = `Answer ${submission.choice} is not confirmed. Retry the same answer; other choices remain locked.`;
    else message.textContent = paused ? "Answer not confirmed. Challenge paused; recover after resume." : "Answer not confirmed. Recovering current state; answering may be closed.";
  }
  async function submitAnswer(attempt) {
    if (unavailable || terminal || current !== attempt.question || submission !== attempt || attempt.status === "pending") return;
    attempt.status = "pending"; showSubmission();
    try {
      const ack = await request(root.dataset.answerUrl, {question: attempt.question, choice: attempt.choice});
      if (ack.accepted !== true || ack.question !== attempt.question || ack.selected_choice !== attempt.choice) throw new Error("Invalid answer confirmation. Recovering the recorded answer.");
      if (unavailable || terminal || current !== attempt.question || submission !== attempt) return;
      attempt.status = "confirmed";
    } catch (failure) {
      if (unavailable || terminal || current !== attempt.question || submission !== attempt) return;
      // A failed/lost ACK does not prove rejection. Only HTTP recovery may offer
      // a same-choice retry; a conflicting choice is never offered here.
      attempt.status = "recovering"; canAnswer = false; showError(failure.message);
    }
    if (submission === attempt && !unavailable && !terminal) { showSubmission(); await refresh(); }
  }
  function render(state) {
    if (!Number.isSafeInteger(state.version) || !["READY", "LOBBY", "QUESTION_OPEN", "QUESTION_CLOSED", "ANSWER_REVEALED", "COMPLETED", "CANCELLED"].includes(state.status)) throw new Error("Invalid recovery response. Keeping the last verified state; reconnect to recover.");
    if (unavailable || state.version < latest) return;
    latest = state.version;
    error.hidden = true;
    join.hidden = true;
    terminal = state.status === "COMPLETED" || state.status === "CANCELLED";
    paused = Boolean(state.paused); frozenSeconds = (state.remaining_us || 0) / 1000000;
    root.dataset.phase = paused ? "PAUSED" : state.show_phase || state.status;
    if (previousPhase !== root.dataset.phase) window.QuiTizzPresentation?.cleanup();
    previousPhase = root.dataset.phase;
    const interlude = state.playback_mode === "AUTOMATIC" && ["SUSPENSE", "PREPARING"].includes(state.show_phase);
    const timerBlock = byId("qt-timer-block"); if (timerBlock) timerBlock.hidden = !paused && state.status !== "QUESTION_OPEN" && !interlude;
    const result = byId("qt-result"); if (result) result.hidden = !state.summary;
    const title = byId("qt-game-title"); if (title) title.textContent = state.title || "";
    const labels = {READY: "Waiting for the host.", LOBBY: "You joined. Waiting for the host to start.", QUESTION_OPEN: "Choose one answer.", QUESTION_CLOSED: "Waiting for the host to open or reveal the question.", ANSWER_REVEALED: "Answer revealed. Waiting for the next question.", COMPLETED: "Session completed.", CANCELLED: "Session cancelled."};
    message.textContent = `${state.nickname} · ${labels[state.status] || "Waiting for the host."}`;
    if (paused) message.textContent = `${state.nickname} · Challenge paused. Waiting for the host.`;
    else if (state.show_phase === "SUSPENSE" && state.playback_mode === "AUTOMATIC") message.textContent = "AND THE CORRECT ANSWER IS...";
    else if (state.show_phase === "PREPARING" && state.playback_mode === "AUTOMATIC") message.textContent = "BE READY FOR THE NEXT CHALLENGE!";
    feedback.hidden = true;
    hasQuestion = Boolean(state.question);
    if (!state.question) { question.hidden = true; current = null; submission = null; canAnswer = false; if (retry) retry.hidden = true; lockButtons(); return; }
    question.hidden = false;
    const q = state.question;
    byId("qt-prompt").textContent = `${q.position}. ${q.prompt}`;
    serverOffset = Date.parse(state.server_now) - Date.now();
    deadline = Date.parse(state.phase_deadline || q.deadline);
    answers.hidden = paused || interlude;
    if (current !== q.id) {
      current = q.id;
      submission = null;
      answers.replaceChildren();
      Object.entries(q.choices).forEach(([letter, text]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn qt-answer";
        button.dataset.choice = letter; button.dataset.label = `${letter}. ${text}`;
        button.textContent = `${letter}. ${text}`;
        button.addEventListener("click", async () => {
          if (submission || !canAnswer) return;
          submission = {question: q.id, choice: letter, status: "new"};
          await submitAnswer(submission);
        });
        answers.append(button);
      });
    }
    if (paused || interlude) {
      byId("qt-prompt").textContent = paused ? "Challenge paused" : message.textContent;
      byId("qt-countdown").textContent = String(paused ? Math.ceil(frozenSeconds) : Math.max(0, Math.ceil((deadline - Date.now() - serverOffset) / 1000)));
      canAnswer = false; lockButtons(); if (retry) retry.hidden = true; return;
    }
    canAnswer = Boolean(state.can_answer && !paused && Date.now() + serverOffset <= deadline);
    if (state.accepted === true) {
      const choice = /^[ABCD]$/.test(state.selected_choice) ? state.selected_choice : submission?.status === "confirmed" ? submission.choice : null;
      submission = {question: q.id, choice, status: "confirmed"};
    } else if (submission && !["pending", "confirmed"].includes(submission.status)) submission.status = canAnswer ? "retry" : "recovering";
    showSubmission();
    if (!submission && !paused && !state.can_answer && !state.accepted && state.status === "QUESTION_OPEN") message.textContent = "Time ended. Waiting for the host to reveal.";
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
    const sequence = ++requested;
    try {
      const state = await request(root.dataset.stateUrl);
      if (unavailable || state.version < latest || sequence < rendered) return;
      rendered = sequence; render(state);
      if (terminal) window.clearInterval?.(countdownTimer);
      if (terminal && transport) { transport.stop(); byId("qt-connection").textContent = "Session ended."; }
      return state;
    }
    catch (failure) {
      if ([401, 403, 404].includes(failure.status)) {
        unavailable = true; terminal = true; canAnswer = false; current = null; hasQuestion = false;
        window.clearInterval?.(countdownTimer);
        question.hidden = true; feedback.hidden = true; join.hidden = true; lockButtons();
        submission = null; if (retry) retry.hidden = true;
        const result = byId("qt-result"); if (result) result.hidden = true;
        window.QuiTizzPresentation?.cleanup();
        message.textContent = "Session unavailable.";
        if (transport) transport.stop();
        byId("qt-connection").textContent = "Session unavailable.";
      }
      showError(failure.message);
    }
  }
  retry?.addEventListener("click", async () => {
    if (submission?.status === "retry" && canAnswer) await submitAnswer(submission);
  });
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
    if (!hasQuestion) return;
    const remaining = paused ? Math.ceil(frozenSeconds) : Math.max(0, Math.ceil((deadline - Date.now() - serverOffset) / 1000));
    byId("qt-countdown").textContent = String(remaining);
    if (!remaining) { lockButtons(); if (retry) retry.disabled = true; }
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
