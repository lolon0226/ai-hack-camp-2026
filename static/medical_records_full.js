(function () {
  function bindMedicalTabs(root) {
    if (!root || root.getAttribute("data-tabs-bound") === "true") return;
    root.setAttribute("data-tabs-bound", "true");
    var tabs = root.querySelectorAll("[data-medical-tab]");
    var panels = root.querySelectorAll("[data-medical-tab-panel]");
    tabs.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var tabId = btn.getAttribute("data-medical-tab");
        tabs.forEach(function (t) {
          var active = t.getAttribute("data-medical-tab") === tabId;
          t.classList.toggle("is-active", active);
          t.setAttribute("aria-selected", active ? "true" : "false");
        });
        panels.forEach(function (panel) {
          var show = panel.getAttribute("data-medical-tab-panel") === tabId;
          panel.classList.toggle("is-active", show);
          panel.hidden = !show;
        });
      });
    });
  }

  window.initMedicalRecordsTabs = bindMedicalTabs;

  function initAll() {
    document.querySelectorAll("[data-medical-records-tabs]").forEach(bindMedicalTabs);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();
