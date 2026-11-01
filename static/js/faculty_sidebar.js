(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    const layout = document.getElementById("faculty-layout");
    const sidebar = document.getElementById("faculty-sidebar");
    const toggle = document.getElementById("faculty-sidebar-toggle");
    const close = document.getElementById("faculty-sidebar-close");
    const backdrop = document.getElementById("faculty-sidebar-backdrop");
    if (!layout || !sidebar || !toggle || !close || !backdrop) return;
    const mobile = window.matchMedia("(max-width: 991.98px)");
    const storageKey = "TeacherMate+_faculty_sidebar_collapsed";
    const locked = layout.dataset.navigationLock === "1";
    let desktopCollapsed = locked;
    let drawerOpen = false;
    let previousFocus = null;
    let background = [];
    try { desktopCollapsed = locked || window.localStorage.getItem(storageKey) === "1"; } catch (_) { /* Storage can be disabled. */ }

    function restoreBackground() {
      background.forEach(function (entry) { entry.element.inert = entry.inert; });
      background = [];
    }
    function render() {
      layout.classList.toggle("sidebar-collapsed", !mobile.matches && desktopCollapsed);
      layout.classList.toggle("mobile-drawer-open", mobile.matches && drawerOpen);
      document.body.classList.toggle("faculty-drawer-open", mobile.matches && drawerOpen);
      backdrop.hidden = !mobile.matches || !drawerOpen;
      sidebar.inert = mobile.matches && !drawerOpen;
      sidebar.setAttribute("aria-hidden", mobile.matches && !drawerOpen ? "true" : "false");
      toggle.setAttribute("aria-expanded", String(mobile.matches ? drawerOpen : !desktopCollapsed));
      toggle.setAttribute("aria-label", mobile.matches ? "Open navigation" : "Toggle sidebar");
      if (mobile.matches && drawerOpen) {
        sidebar.setAttribute("role", "dialog");
        sidebar.setAttribute("aria-modal", "true");
      } else {
        sidebar.removeAttribute("role");
        sidebar.removeAttribute("aria-modal");
      }
    }
    function closeDrawer(restoreFocus) {
      drawerOpen = false;
      restoreBackground();
      render();
      if (restoreFocus) {
        const target = previousFocus && previousFocus !== document.body && previousFocus.isConnected ? previousFocus : toggle;
        target.focus();
        if (sidebar.contains(document.activeElement)) toggle.focus();
      }
      previousFocus = null;
    }
    function openDrawer() {
      if (locked) return;
      previousFocus = document.activeElement;
      drawerOpen = true;
      background = Array.from(document.querySelectorAll(".faculty-topbar, .faculty-main, .faculty-utility-stack"))
        .map(function (element) { return { element: element, inert: element.inert }; });
      background.forEach(function (entry) { entry.element.inert = true; });
      render();
      close.focus();
    }
    toggle.addEventListener("click", function () {
      if (locked) return;
      if (mobile.matches) {
        if (drawerOpen) closeDrawer(true); else openDrawer();
      } else {
        desktopCollapsed = !desktopCollapsed;
        try { window.localStorage.setItem(storageKey, desktopCollapsed ? "1" : "0"); } catch (_) { /* Optional preference. */ }
        render();
      }
    });
    close.addEventListener("click", function () { closeDrawer(true); });
    backdrop.addEventListener("click", function () { closeDrawer(true); });
    sidebar.addEventListener("click", function (event) {
      if (drawerOpen && event.target.closest("a[href]")) closeDrawer(true);
    });
    document.addEventListener("keydown", function (event) {
      if (!mobile.matches || !drawerOpen) return;
      if (event.key === "Escape") { event.preventDefault(); closeDrawer(true); return; }
      if (event.key !== "Tab") return;
      const focusable = Array.from(sidebar.querySelectorAll("a[href], button:not([disabled]), [tabindex='0']"))
        .filter(function (element) { return !element.hidden && element.getClientRects().length > 0; });
      const first = focusable[0] || sidebar;
      const last = focusable[focusable.length - 1] || sidebar;
      if (event.shiftKey && (document.activeElement === first || document.activeElement === sidebar)) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    });
    function viewportChanged() {
      const focusWasInside = sidebar.contains(document.activeElement);
      closeDrawer(false);
      if (focusWasInside && (mobile.matches || document.activeElement === close)) toggle.focus();
    }
    if (mobile.addEventListener) mobile.addEventListener("change", viewportChanged);
    else mobile.addListener(viewportChanged);
    // Mobile state is deliberately never restored from desktop preferences.
    render();
  });
}());
