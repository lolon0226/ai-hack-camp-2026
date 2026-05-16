(function () {
  function activateTab(tabId) {
    if (!tabId) return;
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

  function initCategoryTabs() {
    var tabs = document.querySelectorAll("[data-category-tab]");
    if (!tabs.length) return;
    tabs.forEach(function (btn) {
      btn.addEventListener("click", function () {
        activateTab(btn.getAttribute("data-category-tab"));
      });
    });
    var initial =
      document.querySelector(".ai-category-tab.active") ||
      document.querySelector("[data-category-tab]");
    if (initial) {
      activateTab(initial.getAttribute("data-category-tab"));
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCategoryTabs);
  } else {
    initCategoryTabs();
  }
})();
