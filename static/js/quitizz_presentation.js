/* Presentation only: every input is an authorized HTTP snapshot. */
(() => {
  "use strict";
  const make = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const effects = new Set();
  function celebrate(root, finale = false) {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    // One effect at a time per surface; at most 36 local particles, one timer.
    for (const effect of effects) if (effect.root === root) effect.clear();
    const layer = make("div", undefined, "qt-effects");
    layer.setAttribute("aria-hidden", "true");
    const rocket = icon("rocket"); rocket.setAttribute("class", "qt-icon qt-rocket"); layer.append(rocket);
    for (let i = 0; i < (finale ? 36 : 12); i++) {
      const spark = make("i", undefined, finale && i >= 12 ? "qt-confetti" : "qt-spark");
      spark.style.setProperty("--angle", `${i * 137.5}deg`);
      spark.style.setProperty("--distance", `${45 + (i % 6) * 22}px`);
      spark.style.setProperty("--delay", `${(i % 4) * 70}ms`);
      spark.style.setProperty("--hue", `${36 + (i % 5) * 52}`);
      layer.append(spark);
    }
    root.append(layer);
    const effect = {root, clear() { window.clearTimeout(effect.timer); layer.remove(); effects.delete(effect); }};
    effect.timer = window.setTimeout(effect.clear, finale ? 2400 : 1600); effects.add(effect);
  }
  function cleanup() { for (const effect of [...effects]) effect.clear(); }
  window.addEventListener("pagehide", cleanup);
  window.matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", cleanup);
  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 48 48"); svg.setAttribute("aria-hidden", "true"); svg.setAttribute("class", "qt-icon");
    svg.setAttribute("fill", "none"); svg.setAttribute("stroke", "currentColor"); svg.setAttribute("stroke-width", "2.5");
    svg.setAttribute("stroke-linecap", "round"); svg.setAttribute("stroke-linejoin", "round");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `${window.QuiTizzIcons || "/static/quitizz/icons.svg"}#${name}`); svg.append(use); return svg;
  }
  function create(root, {projector = false, qrUrl} = {}) {
    let lastKey = "", deadline = 0, offset = 0, status = "", finaleKey = "";
    const timer = make("strong", "0", "qt-timer"); timer.setAttribute("role", "timer"); timer.setAttribute("aria-live", "off");
    function render(state) {
      status = state.status;
      deadline = Date.parse(state.question?.deadline); offset = Date.parse(state.server_now) - Date.now();
      const key = JSON.stringify(state, (key, value) => key === "server_now" ? undefined : value);
      if (key === lastKey) return;
      lastKey = key; root.replaceChildren(); root.dataset.phase = status;
      root.append(make("p", `${state.participant_count} joined · ${state.answered_count} answered · Question ${state.position} / ${state.question_count}`, "qt-metrics"));
      if (["READY", "LOBBY"].includes(status)) {
        root.append(make("h2", state.title, "qt-welcome-title"), make("p", "Ready to spark? Waiting for the host.", "qt-waiting"));
        if (state.joining_open && qrUrl) {
          const image = make("img", undefined, "qt-qr"); image.src = `${qrUrl}?v=${state.version}`;
          image.alt = "Scan this QR code, enter your nickname and join QuiTizz"; root.append(image);
          root.append(make("p", "Scan. Enter your nickname. Join the lobby."));
        } else root.append(make("p", "Joining is closed."));
        return;
      }
      if (status === "CANCELLED") { root.append(make("h2", "Session cancelled.")); return; }
      if (status === "COMPLETED") {
        root.append(make("h2", "Final Top 3", "qt-section-title"));
        const podium = make("ol", undefined, "qt-podium");
        (state.leaderboard || []).slice(0, 3).forEach((player, index) => {
          const item = make("li", undefined, `qt-place qt-place-${index + 1}`);
          item.append(icon(index === 0 ? "trophy" : "medal"), make("span", ["Gold · Champion", "Silver", "Bronze"][index], "qt-place-label"),
            make("h3", player.nickname), make("strong", `${player.score} points`), make("span", `Rank ${player.rank}`));
          if (index === 0) item.append(icon("crown"));
          podium.append(item);
        });
        root.append(podium);
        if (!podium.children.length) root.append(make("p", "No participants. Thanks for playing!"));
        if (state.leaderboard?.length && finaleKey !== String(state.version)) { finaleKey = String(state.version); celebrate(root, true); }
        return;
      }
      if (!state.question) { root.append(make("h2", "Get ready for the next question.")); return; }
      root.append(make("h2", state.question.prompt, "qt-presentation-prompt"));
      const choices = make("div", undefined, "qt-choices qt-presentation-choices");
      Object.entries(state.question.choices).forEach(([letter, value]) => {
        const choice = make("div", `${letter}. ${value}`);
        if (state.reveal?.correct_choice === letter) { choice.className = "qt-correct"; choice.append(make("strong", " ✓ Correct answer")); }
        choices.append(choice);
      }); root.append(choices);
      if (status === "QUESTION_OPEN") { root.append(make("p", "Seconds remaining", "qt-timer-label"), timer); tick(); }
      else root.append(make("p", status === "ANSWER_REVEALED" ? `Correct answer: ${state.reveal?.correct_choice || ""}` : "Answers locked. Waiting for reveal.", "qt-state-label"));
      if (state.reveal) {
        const distribution = make("section", undefined, "qt-distribution"); distribution.setAttribute("aria-label", "Answer distribution");
        distribution.append(make("h3", "Answer distribution"));
        const total = Object.values(state.reveal.distribution).reduce((sum, value) => sum + value, 0);
        Object.entries(state.reveal.distribution).forEach(([letter, count]) => {
          const row = make("div", undefined, "qt-distribution-row");
          row.append(make("span", `${letter} · ${count} responses${letter === state.reveal.correct_choice ? " · Correct" : ""}`));
          const bar = make("div", undefined, "qt-bar"); bar.style.setProperty("--percent", `${total ? count / total * 100 : 0}%`); bar.setAttribute("aria-hidden", "true");
          row.append(bar); distribution.append(row);
        });
        if (!total) distribution.append(make("p", "No responses received."));
        root.append(distribution);
        const leaderboard = make("section", undefined, "qt-leaderboard"); leaderboard.append(make("h3", "Top 5"));
        const list = make("ol");
        (state.leaderboard || []).forEach((player) => {
          const item = make("li"); item.append(make("span", `${player.rank}. ${player.nickname}`), make("strong", `${player.score} points`)); list.append(item);
        }); leaderboard.append(list); root.append(leaderboard);
      }
      if (projector) root.setAttribute("aria-label", "QuiTizz presentation");
    }
    function tick() { if (status === "QUESTION_OPEN") timer.textContent = String(Math.max(0, Math.ceil((deadline - Date.now() - offset) / 1000))); }
    const interval = window.setInterval(tick, 250);
    function clear() { window.clearInterval(interval); cleanup(); }
    window.addEventListener("pagehide", clear);
    return {render, clear};
  }
  window.QuiTizzPresentation = {create, celebrate, cleanup};
})();
