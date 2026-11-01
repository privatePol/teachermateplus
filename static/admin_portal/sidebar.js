(() => {
  'use strict';
  const shell = document.querySelector('.admin-shell');
  const sidebar = document.querySelector('#admin-sidebar');
  const main = document.querySelector('.admin-main');
  const opener = document.querySelector('#admin-mobile-nav');
  if (!shell || !sidebar || !main || !opener) return;
  const closer = sidebar.querySelector('.admin-drawer-close');
  const backdrop = shell.querySelector('.admin-drawer-backdrop');
  const mobile = matchMedia('(max-width: 991.98px)');
  let open = false, previousFocus, previousOverflow;
  function setOpen(value, restore = true) {
    const wasOpen = open;
    open = Boolean(value && mobile.matches);
    shell.classList.toggle('admin-drawer-open', open);
    opener.setAttribute('aria-expanded', String(open));
    backdrop.hidden = !open;
    main.inert = open;
    sidebar.inert = mobile.matches && !open;
    if (open) {
      previousFocus = document.activeElement === document.body ? opener : document.activeElement;
      previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      sidebar.setAttribute('role', 'dialog'); sidebar.setAttribute('aria-modal', 'true');
      sidebar.setAttribute('aria-label', 'Admin navigation');
      closer.focus();
    } else {
      if (wasOpen) document.body.style.overflow = previousOverflow || '';
      sidebar.removeAttribute('role'); sidebar.removeAttribute('aria-modal');
      sidebar.removeAttribute('aria-label');
      if (restore && previousFocus?.isConnected) previousFocus.focus();
    }
  }
  shell.classList.add('admin-drawer-ready');
  sidebar.inert = mobile.matches;
  opener.addEventListener('click', () => setOpen(true));
  closer.addEventListener('click', () => setOpen(false));
  backdrop.addEventListener('click', () => setOpen(false));
  document.addEventListener('keydown', event => {
    if (!open) return;
    if (event.key === 'Escape') { event.preventDefault(); setOpen(false); }
    if (event.key === 'Tab') {
      const focusable = [...sidebar.querySelectorAll('a[href], button:not([disabled]), input, select')]
        .filter(element => element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden');
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !sidebar.contains(document.activeElement))) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !sidebar.contains(document.activeElement))) {
        event.preventDefault(); first?.focus();
      }
    }
  });
  mobile.addEventListener('change', () => {
    const focusWasInSidebar = sidebar.contains(document.activeElement);
    setOpen(false, false);
    if (focusWasInSidebar) {
      const target = mobile.matches ? opener : sidebar.querySelector('[data-sidebar-toggle]');
      target?.focus();
    }
  });
})();
