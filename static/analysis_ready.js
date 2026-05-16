(function () {
  document.querySelectorAll("[data-toggle-target]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var targetId = btn.dataset.toggleTarget;
      if (!targetId) return;
      var panel = document.getElementById(targetId);
      if (!panel) return;
      var openText = btn.dataset.openText || btn.textContent;
      var closeText = btn.dataset.closeText || openText;
      var opened = !panel.hasAttribute("hidden");
      if (opened) {
        panel.setAttribute("hidden", "");
        btn.textContent = openText;
        btn.setAttribute("aria-expanded", "false");
      } else {
        panel.removeAttribute("hidden");
        btn.textContent = closeText;
        btn.setAttribute("aria-expanded", "true");
      }
    });
  });
})();
