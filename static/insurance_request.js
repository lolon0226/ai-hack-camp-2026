(function () {
  document.addEventListener("click", function (event) {
    var button = event.target.closest('[data-action="toggle-credit4u-password"]');
    if (!button) {
      return;
    }
    var card = button.closest(".hospital-credit4u-credentials");
    if (!card) {
      return;
    }
    var input = card.querySelector("[data-credit4u-password-input]");
    if (!input) {
      return;
    }
    var reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    button.textContent = reveal ? "숨기기" : "보기";
    button.setAttribute("aria-pressed", reveal ? "true" : "false");
  });
})();
