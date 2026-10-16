(function () {
  "use strict";

  const palette = document.querySelector("[data-command-palette]");
  const commandButton = document.querySelector("[data-open-command]");
  const commandInput = document.querySelector("[data-command-input]");
  const commandItems = Array.from(document.querySelectorAll("[data-command-item]"));
  const notificationButton = document.querySelector("[data-notification-button]");
  const notificationPanel = document.querySelector("[data-notification-panel]");
  const taskQueueSearch = document.querySelector("[data-task-queue-search]");
  const taskStatusFilter = document.querySelector("[data-task-status-filter]");
  const taskQueueItems = Array.from(document.querySelectorAll("[data-task-queue-item]"));

  function openPalette() {
    if (!palette) return;
    palette.hidden = false;
    document.body.classList.add("modal-open");
    commandInput.value = "";
    filterCommands();
    window.setTimeout(function () { commandInput.focus(); }, 0);
  }

  function closePalette() {
    if (!palette) return;
    palette.hidden = true;
    document.body.classList.remove("modal-open");
  }

  function filterCommands() {
    const query = (commandInput.value || "").trim().toLowerCase();
    let visible = 0;
    commandItems.forEach(function (item) {
      const matches = !query || (item.dataset.commandQuery || item.textContent).toLowerCase().includes(query);
      item.hidden = !matches;
      if (matches) visible += 1;
    });
    const empty = document.querySelector("[data-command-empty]");
    if (empty) empty.hidden = visible !== 0;
  }

  function closeNotifications() {
    if (!notificationPanel || !notificationButton) return;
    notificationPanel.hidden = true;
    notificationButton.setAttribute("aria-expanded", "false");
  }

  function filterTaskQueue() {
    const query = (taskQueueSearch && taskQueueSearch.value || "").trim().toLowerCase();
    const status = taskStatusFilter && taskStatusFilter.value || "";
    let visible = 0;
    taskQueueItems.forEach(function (item) {
      const matchesQuery = !query || (item.dataset.query || "").includes(query);
      const matchesStatus = !status || item.dataset.status === status;
      item.hidden = !(matchesQuery && matchesStatus);
      if (!item.hidden) visible += 1;
    });
    const empty = document.querySelector("[data-task-queue-empty]");
    if (empty) empty.classList.toggle("visible", visible === 0);
  }

  if (commandButton) commandButton.addEventListener("click", openPalette);
  if (commandInput) commandInput.addEventListener("input", filterCommands);
  if (taskQueueSearch) taskQueueSearch.addEventListener("input", filterTaskQueue);
  if (taskStatusFilter) taskStatusFilter.addEventListener("change", filterTaskQueue);
  if (palette) {
    palette.addEventListener("click", function (event) {
      if (event.target === palette || event.target.closest("[data-close-command]")) closePalette();
    });
  }
  if (notificationButton && notificationPanel) {
    notificationButton.addEventListener("click", function (event) {
      event.stopPropagation();
      const willOpen = notificationPanel.hidden;
      notificationPanel.hidden = !willOpen;
      notificationButton.setAttribute("aria-expanded", String(willOpen));
    });
    notificationPanel.addEventListener("click", function (event) { event.stopPropagation(); });
    document.addEventListener("click", closeNotifications);
  }
  document.querySelectorAll("form.lab-form").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (form.dataset.submitted === "true") {
        event.preventDefault();
        return;
      }
      form.dataset.submitted = "true";
      const button = form.querySelector("button[type='submit'], button:not([type])");
      if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
        button.textContent = "Evaluating...";
      }
    });
  });
  document.addEventListener("keydown", function (event) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      palette && !palette.hidden ? closePalette() : openPalette();
    } else if (event.key === "Escape") {
      closePalette();
      closeNotifications();
    }
  });
}());
