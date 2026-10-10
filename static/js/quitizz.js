"use strict";
(() => {
  const list = document.getElementById("qt-order-list");
  if (!list) return;
  const refresh = () => {
    const rows = [...list.querySelectorAll(".qt-question")];
    rows.forEach((row, index) => {
      row.querySelector(".qt-position").textContent = index + 1;
      const up = row.querySelector('[data-direction="up"]');
      const down = row.querySelector('[data-direction="down"]');
      if (up) up.disabled = index === 0;
      if (down) down.disabled = index === rows.length - 1;
    });
  };
  list.addEventListener("click", (event) => {
    const button = event.target.closest(".qt-move");
    if (!button) return;
    const row = button.closest(".qt-question");
    if (button.dataset.direction === "up" && row.previousElementSibling) {
      list.insertBefore(row, row.previousElementSibling);
    } else if (button.dataset.direction === "down" && row.nextElementSibling) {
      list.insertBefore(row.nextElementSibling, row);
    }
    refresh();
    const status = document.getElementById("qt-order-status");
    if (status) status.textContent = "Order changed. Save question order to keep it.";
    button.focus();
  });
  refresh();
})();
