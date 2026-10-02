(function () {
  "use strict";
  const weekdayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  document.querySelectorAll("[data-checklist-days]").forEach(function (picker) {
    const summary = picker.querySelector("summary");
    const add = picker.querySelector("[data-add-checklist-days]");
    const message = picker.parentElement.querySelector("[data-checklist-days-message]");
    function selectedLabels() {
      return Array.from(picker.querySelectorAll("input:checked")).map(function (input) { return weekdayNames[Number(input.value)]; });
    }
    const initial = selectedLabels();
    summary.textContent = initial.length ? initial.join(" / ") : "Choose checklist days";
    add.hidden = false;
    add.addEventListener("click", function (event) {
      event.preventDefault();
      const labels = selectedLabels();
      if (!labels.length) {
        message.textContent = "Select at least one day.";
        message.setAttribute("role", "alert");
        picker.querySelector("input").focus();
        return;
      }
      summary.textContent = labels.join(" / ");
      message.removeAttribute("role");
      message.textContent = "Days selected. Click Show Checklist to load schedules.";
      picker.open = false;
      summary.focus();
    });
    picker.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { picker.open = false; summary.focus(); }
    });
    document.addEventListener("click", function (event) {
      if (!picker.contains(event.target)) { picker.open = false; }
    });
  });
  const dirty = document.getElementById("route-dirty");
  function changed() { if (dirty) dirty.classList.remove("d-none"); }
  function installGroup(body) {
    let dragged = null;
    function move(row, direction) {
      const sibling = direction < 0 ? row.previousElementSibling : row.nextElementSibling;
      if (!sibling) return;
      if (direction < 0) body.insertBefore(row, sibling); else body.insertBefore(sibling, row);
      changed();
      const button = row.querySelector(direction < 0 ? ".move-up" : ".move-down");
      if (button) button.focus();
    }
    body.addEventListener("click", function (event) {
      const row = event.target.closest("tr"); if (!row) return;
      if (event.target.closest(".move-up")) move(row, -1);
      if (event.target.closest(".move-down")) move(row, 1);
    });
    body.addEventListener("dragstart", function (event) {
      dragged = event.target.closest("tr");
      if (dragged) event.dataTransfer.effectAllowed = "move";
    });
    body.addEventListener("dragover", function (event) { event.preventDefault(); });
    body.addEventListener("drop", function (event) {
      event.preventDefault(); const target = event.target.closest("tr");
      if (dragged && target && dragged !== target && target.parentElement === body) {
        body.insertBefore(dragged, target); changed();
      }
    });
  }
  const monthlyGroups = Array.from(document.querySelectorAll(".monthly-group"));
  monthlyGroups.forEach(installGroup);
  document.getElementById("arrangement-form")?.addEventListener("submit", function () {
    document.getElementById("row-tokens").value = JSON.stringify(monthlyGroups.flatMap(function (body) {
      return Array.from(body.querySelectorAll("tr")).map(function (row) { return row.dataset.rowToken; });
    }));
  });

  const legacyBody = document.getElementById("route-rows");
  if (!legacyBody) return;
  installGroup(legacyBody);
  document.getElementById("round-form")?.addEventListener("submit", function () {
    document.getElementById("meeting-ids").value = Array.from(legacyBody.querySelectorAll("tr")).filter(function (row) {
      return row.querySelector(".meeting-select").checked;
    }).map(function (row) { return row.dataset.meetingId; }).join(",");
  });
  document.getElementById("route-form")?.addEventListener("submit", function () {
    document.getElementById("schedule-slot-ids").value = Array.from(legacyBody.querySelectorAll("tr")).map(function (row) {
      return row.dataset.slotId;
    }).filter(function (id, index, all) { return all.indexOf(id) === index; }).join(",");
  });
})();
