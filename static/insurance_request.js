(function () {
  function findPasswordInput(root) {
    return (
      root.querySelector("[data-credit4u-password-input]") ||
      root.querySelector('input[type="password"][id*="password"]')
    );
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest('[data-action="toggle-credit4u-password"]');
    if (!button) {
      return;
    }
    var root =
      button.closest(".hospital-credit4u-credentials") ||
      button.closest(".hospital-register-signup") ||
      button.closest(".hospital-insurance-state");
    if (!root) {
      return;
    }
    var input = findPasswordInput(root);
    if (!input) {
      return;
    }
    var reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    button.textContent = reveal ? "숨기기" : "보기";
    button.setAttribute("aria-pressed", reveal ? "true" : "false");
  });

  function ensureLoadingMessage(form) {
    var existing = form.querySelector(".hospital-form-loading");
    if (existing) {
      return existing;
    }
    var message = document.createElement("p");
    message.className = "hospital-form-loading";
    message.setAttribute("role", "status");
    message.setAttribute("aria-live", "polite");
    message.hidden = true;
    form.appendChild(message);
    return message;
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    if (!form.closest(".hospital-insurance-main")) {
      return;
    }
    if (form.getAttribute("data-loading-skip") === "1") {
      return;
    }

    var loadingText =
      form.getAttribute("data-loading-message") ||
      "신용정보원에 요청 중입니다. 잠시만 기다려 주세요.";
    var loadingDetail = form.getAttribute("data-loading-detail") || "";
    var submitButtons = form.querySelectorAll(
      'button[type="submit"], input[type="submit"]'
    );

    submitButtons.forEach(function (button) {
      if (button.disabled) {
        return;
      }
      if (!button.getAttribute("data-original-label")) {
        button.setAttribute(
          "data-original-label",
          button.tagName === "INPUT"
            ? button.value
            : button.textContent.trim()
        );
      }
      button.disabled = true;
      if (button.tagName === "INPUT") {
        button.value = "처리 중...";
      } else {
        button.textContent = "처리 중...";
      }
    });

    var loadingEl = ensureLoadingMessage(form);
    loadingEl.textContent = loadingDetail
      ? loadingText + " " + loadingDetail
      : loadingText;
    loadingEl.hidden = false;
  });
})();
