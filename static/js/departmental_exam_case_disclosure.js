(() => {
  for (const toggle of document.querySelectorAll("[data-case-collapse-label]")) {
    const button = toggle.closest("button[aria-controls]");
    const panel = button && document.getElementById(button.getAttribute("aria-controls"));
    if (!button || !panel) continue;

    const update = expanded => {
      button.setAttribute("aria-expanded", String(expanded));
      toggle.textContent = expanded ? "Hide Case" : "Show Case";
    };
    panel.addEventListener("show.bs.collapse", () => update(true));
    panel.addEventListener("hide.bs.collapse", () => update(false));
    update(panel.classList.contains("show"));
  }
})();
