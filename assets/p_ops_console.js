(function () {
  "use strict";

  const palette = document.querySelector("[data-command-palette]");
  const commandButton = document.querySelector("[data-open-command]");
  const commandInput = document.querySelector("[data-command-input]");
  const commandItems = Array.from(document.querySelectorAll("[data-command-item]"));
  const notificationButton = document.querySelector("[data-notification-button]");
  const notificationPanel = document.querySelector("[data-notification-panel]");

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

  if (commandButton) commandButton.addEventListener("click", openPalette);
  if (commandInput) commandInput.addEventListener("input", filterCommands);
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
