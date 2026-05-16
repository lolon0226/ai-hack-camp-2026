/**
 * 진료내역 카카오 인증 모달 — 폼 제출 시에만 단계 전환(자동 polling 없음).
 */
(function () {
  var modal = document.getElementById("hira-auth-modal");
  if (!modal) return;

  var dialog = modal.querySelector(".hira-modal__dialog");
  var resultPanel = document.getElementById("hira-modal-result-panel");
  var initialStep = modal.getAttribute("data-initial-step") || "intro";
  var openOnLoad = modal.getAttribute("data-open-on-load") === "true";

  function resetMedicalResultPanel() {
    if (!resultPanel) return;
    resultPanel.hidden = true;
    resultPanel.classList.remove("hira-modal-result-panel--open");
    if (dialog) {
      dialog.classList.remove("hira-modal__dialog--expanded");
    }
    var showBtn = modal.querySelector('[data-action="show-medical-result"]');
    if (showBtn) {
      showBtn.disabled = false;
      showBtn.textContent = "진료내역 결과 보기";
      showBtn.setAttribute("aria-expanded", "false");
    }
  }

  function showMedicalResultPanel() {
    if (!resultPanel) return;
    resultPanel.hidden = false;
    resultPanel.classList.add("hira-modal-result-panel--open");
    if (dialog) {
      dialog.classList.add("hira-modal__dialog--expanded");
    }
    var showBtn = modal.querySelector('[data-action="show-medical-result"]');
    if (showBtn) {
      showBtn.disabled = true;
      showBtn.textContent = "결과 표시 중";
      showBtn.setAttribute("aria-expanded", "true");
    }
    resultPanel.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function showStep(step) {
    var panels = modal.querySelectorAll("[data-hira-step]");
    for (var i = 0; i < panels.length; i++) {
      var panel = panels[i];
      var active = panel.getAttribute("data-hira-step") === step;
      panel.hidden = !active;
    }
    if (step !== "done") {
      resetMedicalResultPanel();
    }
    modal.classList.add("hira-modal--open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("hira-modal-body-lock");
  }

  function closeModal() {
    modal.classList.remove("hira-modal--open");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("hira-modal-body-lock");
    resetMedicalResultPanel();
  }

  window.openHiraModal = showStep;
  window.closeHiraModal = closeModal;
  window.showHiraMedicalResult = showMedicalResultPanel;

  var openButtons = document.querySelectorAll("[data-open-hira-modal]");
  for (var j = 0; j < openButtons.length; j++) {
    openButtons[j].addEventListener("click", function (e) {
      e.preventDefault();
      showStep("intro");
    });
  }

  var closeButtons = modal.querySelectorAll("[data-close-hira-modal]");
  for (var k = 0; k < closeButtons.length; k++) {
    closeButtons[k].addEventListener("click", function (e) {
      e.preventDefault();
      closeModal();
    });
  }

  var showResultButtons = modal.querySelectorAll('[data-action="show-medical-result"]');
  for (var m = 0; m < showResultButtons.length; m++) {
    showResultButtons[m].addEventListener("click", function (e) {
      e.preventDefault();
      showMedicalResultPanel();
    });
  }

  var backdrop = modal.querySelector(".hira-modal__backdrop");
  if (backdrop) {
    backdrop.addEventListener("click", closeModal);
  }

  var completeForm = document.getElementById("hira-complete-form");
  if (completeForm) {
    completeForm.addEventListener("submit", function () {
      showStep("fetching");
    });
  }

  if (openOnLoad) {
    showStep(initialStep);
  }
})();
