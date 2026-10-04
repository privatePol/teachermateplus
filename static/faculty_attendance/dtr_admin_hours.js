(() => {
  const rows = form => [...form.querySelectorAll("[data-admin-hours-row]")];
  const deleted = row => row.querySelector("[name$='-DELETE']").checked;
  const total = form => {
    let cents = 0;
    rows(form).forEach(row => {
      row.hidden = deleted(row);
      if (!row.hidden) {
        const value = Number(row.querySelector("[name$='-hours']").value);
        if (Number.isFinite(value)) cents += Math.round(value * 100);
      }
    });
    const output = form.querySelector("[data-admin-hours-total]");
    const value = (cents / 100).toFixed(2);
    if (output.textContent !== value) output.textContent = value;
  };
  const add = form => {
    const count = form.querySelector("[name='admin_hours-TOTAL_FORMS']");
    if (Number(count.value) >= 366) {
      form.querySelector("[data-admin-hours-message]").textContent = "Up to 366 dated rows are supported.";
      return null;
    }
    const template = document.createElement("template");
    template.innerHTML = form.querySelector("[data-admin-hours-template]").innerHTML.replaceAll("__prefix__", count.value);
    const row = template.content.firstElementChild;
    form.querySelector("[data-admin-hours-rows]").appendChild(row);
    count.value = String(Number(count.value) + 1);
    return row;
  };
  document.addEventListener("input", event => {
    const form = event.target.closest("[data-admin-hours]");
    if (form) total(form);
  });
  document.addEventListener("click", event => {
    const form = event.target.closest("[data-admin-hours]");
    if (!form) return;
    if (event.target.closest("[data-admin-hours-add]")) {
      const row = add(form);
      if (row) row.querySelector("[name$='-entry_date']").focus();
    } else if (event.target.closest("[data-admin-hours-remove]")) {
      event.target.closest("[data-admin-hours-row]").querySelector("[name$='-DELETE']").checked = true;
    } else if (event.target.closest("[data-admin-hours-apply]")) {
      const input = form.querySelector("[data-admin-hours-same]");
      const dates = [...form.querySelectorAll("[data-admin-hours-date]:checked")];
      if (!input.value || !input.checkValidity() || !dates.length) {
        form.querySelector("[data-admin-hours-message]").textContent = "Choose dates and enter non-negative decimal hours.";
        return;
      }
      dates.forEach(date => {
        const row = rows(form).find(row => !deleted(row) && row.querySelector("[name$='-entry_date']").value === date.value) ||
          rows(form).find(row => !deleted(row) && !row.querySelector("[name$='-entry_date']").value) || add(form);
        if (row) {
          row.querySelector("[name$='-entry_date']").value = date.value;
          row.querySelector("[name$='-hours']").value = Number(input.value).toFixed(2);
        }
      });
      form.querySelector("[data-admin-hours-message]").textContent = "Hours applied to the selected dates. Save all entries to record them.";
    }
    total(form);
  });
  const refresh = () => document.querySelectorAll("[data-admin-hours]").forEach(total);
  refresh();
  const root = document.querySelector("[data-dtr-review]");
  if (root && typeof MutationObserver !== "undefined") new MutationObserver(refresh).observe(root, {childList:true, subtree:true});
})();
