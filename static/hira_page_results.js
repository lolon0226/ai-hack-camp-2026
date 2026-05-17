(function () {
  "use strict";

  function initMedicalRecordsTabs(panel) {
    if (!panel) return;
    try {
      var tabsRoot = panel.querySelector("[data-medical-records-tabs]");
      if (tabsRoot && typeof window.initMedicalRecordsTabs === "function") {
        window.initMedicalRecordsTabs(tabsRoot);
      }
    } catch (err) {
      console.warn("medical records tabs init skipped", err);
    }
  }

  function togglePanel(btn, panel) {
    var openText = btn.getAttribute("data-open-text") || btn.textContent || "";
    var closeText = btn.getAttribute("data-close-text") || openText;
    var opened = !panel.hasAttribute("hidden");
    if (opened) {
      panel.setAttribute("hidden", "");
      btn.textContent = openText;
      btn.setAttribute("aria-expanded", "false");
    } else {
      panel.removeAttribute("hidden");
      btn.textContent = closeText;
      btn.setAttribute("aria-expanded", "true");
      initMedicalRecordsTabs(panel);
    }
  }

  function bindMedicalResultToggle() {
    var btn = document.getElementById("medical-result-toggle-btn");
    var panel = document.getElementById("medical-result-panel");
    if (!btn || !panel) return;
    if (btn.dataset.medicalToggleBound === "1") return;
    btn.dataset.medicalToggleBound = "1";
    btn.addEventListener("click", function (ev) {
      ev.preventDefault();
      try {
        togglePanel(btn, panel);
      } catch (err) {
        console.warn("medical result toggle failed", err);
      }
    });
  }

  function initTogglePanels() {
    document.querySelectorAll("[data-toggle-target]").forEach(function (btn) {
      if (btn.id === "medical-result-toggle-btn") return;
      if (btn.dataset.toggleBound === "1") return;
      btn.dataset.toggleBound = "1";
      btn.addEventListener("click", function () {
        try {
          var targetId = btn.dataset.toggleTarget;
          if (!targetId) return;
          var panel = document.getElementById(targetId);
          if (!panel) return;
          togglePanel(btn, panel);
        } catch (err) {
          console.warn("toggle panel failed", err);
        }
      });
    });
  }

  function init() {
    try {
      bindMedicalResultToggle();
      initTogglePanels();
    } catch (err) {
      console.warn("hira page results init failed", err);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
