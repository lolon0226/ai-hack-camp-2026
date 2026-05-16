(function () {
  function activateTab(tabId) {
    document.querySelectorAll("[data-category-tab]").forEach(function (btn) {
      var active = btn.getAttribute("data-category-tab") === tabId;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll("[data-category-panel]").forEach(function (panel) {
      var show = panel.getAttribute("data-category-panel") === tabId;
      panel.classList.toggle("active", show);
      panel.hidden = !show;
    });
  }

  document.querySelectorAll("[data-category-tab]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      activateTab(btn.getAttribute("data-category-tab"));
    });
  });
})();
