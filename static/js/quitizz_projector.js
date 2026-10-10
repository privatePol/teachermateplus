(() => {
  "use strict";
  const root = document.getElementById("qt-projector"); if (!root) return;
  const screen = document.getElementById("qt-presentation"), indicator = document.getElementById("qt-connection");
  const presentation = window.QuiTizzPresentation.create(screen, {projector: true, qrUrl: root.dataset.qrUrl});
  let version = 0, requested = 0, rendered = 0, unavailable = false;
  async function refresh() {
    const sequence = ++requested;
    try {
      const response = await fetch(root.dataset.stateUrl, {credentials: "same-origin", cache: "no-store"});
      if (!response.ok || !response.headers.get("Content-Type")?.includes("application/json")) {
        if ([401, 403, 404].includes(response.status) || response.redirected || response.ok) {
          unavailable = true; screen.replaceChildren(); screen.textContent = "Session unavailable.";
          presentation.clear(); transport.stop(); indicator.textContent = "Session unavailable.";
        }
        return;
      }
      const state = await response.json();
      if (unavailable || state.version < version || sequence < rendered) return;
      version = state.version; rendered = sequence; presentation.render(state); return state;
    } catch (_) { /* Existing transport recovers; no gameplay authority here. */ }
  }
  const transport = window.QuiTizzRealtime({url: root.dataset.socketUrl, recover: refresh, indicator});
})();
