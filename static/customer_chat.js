(function () {
  "use strict";

  var STORAGE_KEY = "redribbon_customer_chat_v5";
  var CUSTOMER_CHAT_STORAGE_PREFIXES = [
    "redribbon_customer_chat",
    "customer_chat",
    "customer_find",
    "redribbon_customer_find",
  ];
  var RESET_CONFIRM_MESSAGE =
    "현재 입력한 고객용 대화 내용과 진행 상태를 초기화하시겠습니까?";
  var WITHDRAW_CONFIRM_MESSAGE_1 =
    "탈퇴하면 입력한 고객정보와 조회 진행정보가 삭제됩니다. 계속하시겠습니까?";
  var WITHDRAW_CONFIRM_MESSAGE_2 = "정말 탈퇴하시겠습니까?";
  var steps = window.RedRibbonCustomerChatSteps || [];
  var autoClaimSteps = window.RedRibbonCustomerChatAutoClaimSteps || [];
  var mask = window.RedRibbonCustomerChatMask || {};
  var keywords = window.RedRibbonCustomerChatKeywords || [];

  var logEl = document.getElementById("customer-chat-log");
  var quickEl = document.getElementById("customer-chat-quick");
  var formEl = document.getElementById("customer-chat-form");
  var inputEl = document.getElementById("customer-chat-input");
  var inputLabelEl = document.getElementById("customer-chat-input-label");
  var sendBtn = document.getElementById("customer-chat-send");
  var consentPanel = document.getElementById("customer-consent-panel");
  var consentToggle = document.getElementById("customer-consent-toggle");
  var consentBody = document.getElementById("customer-consent-body");
  var consentModal = document.getElementById("customer-consent-modal");
  var consentModalBody = document.getElementById("customer-consent-modal-body");
  var consentModalClose = document.getElementById("customer-consent-modal-close");
  var consentModalBackdrop = document.getElementById(
    "customer-consent-modal-backdrop"
  );
  var consentModalSynced = false;
  var consentDock = document.getElementById("customer-consent-dock");
  var consentDockView = document.getElementById("customer-consent-dock-view");
  var summaryCta = document.getElementById("customer-summary-cta");
  var summaryCtaStart = document.getElementById("customer-summary-cta-start");
  var summaryCtaReset = document.getElementById("customer-summary-cta-reset");
  var footerEl = document.querySelector(".customer-chat-footer");
  var progressEl = document.getElementById("customer-find-progress");
  var progressMessageEl = document.getElementById("customer-find-progress-message");
  var progressSubtitleEl = document.getElementById("customer-find-progress-subtitle");
  var progressStageTitleEl = document.getElementById("customer-find-progress-stage-title");
  var progressHeadingEl = document.getElementById("customer-find-progress-heading");
  var kakaoHighlightEl = document.getElementById("customer-find-kakao-highlight");
  var kakaoHighlightTextEl = document.getElementById("customer-find-kakao-highlight-text");
  var progressBadgesEl = document.getElementById("customer-find-progress-badges");
  var progressHintEl = document.getElementById("customer-find-progress-hint");
  var progressLoadingEl = document.getElementById("customer-find-progress-loading");
  var progressLoadingLineEl = document.getElementById(
    "customer-find-progress-loading-line"
  );
  var progressLoadingSubEl = document.getElementById(
    "customer-find-progress-loading-sub"
  );
  var progressStepsEl = document.getElementById("customer-find-progress-steps");
  var authConfirmBtn = document.getElementById("customer-find-auth-confirm");
  var resultsEl = document.getElementById("customer-find-results");
  var resultMedicalStatus = document.getElementById("customer-result-medical-status");
  var resultInsuranceStatus = document.getElementById("customer-result-insurance-status");
  var resultAiStatus = document.getElementById("customer-result-ai-status");
  var viewSheet = document.getElementById("customer-view-sheet");
  var viewSheetTitle = document.getElementById("customer-view-sheet-title");
  var viewSheetBody = document.getElementById("customer-view-sheet-body");
  var viewSheetClose = document.getElementById("customer-view-sheet-close");
  var viewSheetBackdrop = document.getElementById("customer-view-sheet-backdrop");
  var exitBtn = document.getElementById("customer-chat-exit");
  var resetBtn = document.getElementById("customer-chat-reset");
  var withdrawBtn = document.getElementById("customer-chat-withdraw");

  if (!logEl || !steps.length) {
    return;
  }

  function loadState() {
    try {
      var raw = sessionStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function saveState() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      /* ignore */
    }
  }

  /** 고객 채팅·find 관련 session/local 키만 수집(서버 DB·원부 미접촉). 탈퇴 시 전부 삭제. */
  function collectCustomerChatStorageKeys(storage) {
    var keys = [];
    if (!storage) return keys;
    for (var i = 0; i < storage.length; i++) {
      var key = storage.key(i);
      if (!key) continue;
      var lower = key.toLowerCase();
      var matched = CUSTOMER_CHAT_STORAGE_PREFIXES.some(function (prefix) {
        return lower.indexOf(prefix) === 0;
      });
      if (matched) keys.push(key);
    }
    return keys;
  }

  function clearCustomerChatBrowserStorage() {
    [window.sessionStorage, window.localStorage].forEach(function (storage) {
      if (!storage) return;
      collectCustomerChatStorageKeys(storage).forEach(function (key) {
        try {
          storage.removeItem(key);
        } catch (e) {
          /* ignore */
        }
      });
    });
  }

  function resetCustomerChatSession() {
    /* 초기화·탈퇴: 고객 탈퇴는 현재 고객 연결 데이터만 삭제하며, 준비된 원부 파일은 삭제하지 않는다. */
    clearCustomerChatBrowserStorage();
    document.body.classList.remove("customer-view-sheet-open");
    document.body.classList.remove("customer-consent-modal-open");
    window.location.assign("/customer/chat");
  }

  function clearCustomerUiAfterWithdraw() {
    clearCustomerChatBrowserStorage();
    state = defaultState();
    if (logEl) logEl.innerHTML = "";
    if (quickEl) {
      quickEl.innerHTML = "";
      quickEl.hidden = true;
    }
    if (progressEl) progressEl.hidden = true;
    if (resultsEl) resultsEl.hidden = true;
    var inlineAi = document.getElementById("customer-ai-inline");
    if (inlineAi) inlineAi.hidden = true;
    if (viewSheet) viewSheet.hidden = true;
    document.body.classList.remove("customer-view-sheet-open");
    document.body.classList.remove("customer-consent-modal-open");
    hideConsentPanel();
    hideConsentDock();
    hideSummaryCta();
    hideForm();
    clearQuickReplies();
    closeConsentModal();
    if (footerEl) footerEl.hidden = false;
    if (logEl) {
      logEl.hidden = false;
      logEl.classList.remove("customer-chat-log--after-results");
    }
  }

  function runCustomerWithdraw() {
    if (!window.confirm(WITHDRAW_CONFIRM_MESSAGE_1)) return;
    if (!window.confirm(WITHDRAW_CONFIRM_MESSAGE_2)) return;
    if (withdrawBtn) withdrawBtn.disabled = true;
    fetch("/api/customer/withdraw", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        flow_id: state.flowId || "",
        payload: buildRegistrationPayload(),
      }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok && data && data.ok === false) {
            return data;
          }
          if (!res.ok) {
            return { ok: false, message: "탈퇴 처리에 실패했습니다." };
          }
          return data;
        });
      })
      .catch(function () {
        return { ok: false, message: "탈퇴 처리에 실패했습니다." };
      })
      .then(function (data) {
        if (withdrawBtn) withdrawBtn.disabled = false;
        if (!data || !data.ok) {
          window.alert(
            (data && data.message) || "탈퇴 처리에 실패했습니다."
          );
          return;
        }
        clearCustomerUiAfterWithdraw();
        appendMessage("bot", data.message || "탈퇴가 완료되었습니다.", {
          highlight: true,
        });
        var redirect = (data && data.redirect_url) || "/";
        window.setTimeout(function () {
          window.location.href = redirect;
        }, 1000);
      });
  }

  function defaultState() {
    return {
      stepIndex: 0,
      answers: { consent: false },
      payload: null,
      messages: [],
      awaitingQuickReply: false,
      consentPanelOpen: false,
      finished: false,
      declined: false,
      findStarted: false,
      flowId: null,
      findResults: null,
      findStatus: null,
      autoClaimPromptDone: false,
      autoClaimDeclined: false,
      autoClaimCompleted: false,
      autoClaimStepIndex: 0,
      aiInlineOpened: false,
      pendingAutoClaimOnReturn: false,
    };
  }

  function splitIdentity(identity) {
    var id = String(identity || "").replace(/\D/g, "").slice(0, 13);
    return { identity: id, rrnFront: id.slice(0, 6), rrnBack: id.slice(6, 13) };
  }

  /** 1차 고객등록 payload (은행·계좌 제외) */
  function buildRegistrationPayload() {
    var a = state.answers;
    var idParts = splitIdentity(a.identity);
    var hasIdentity = idParts.identity.length === 13;
    return {
      consent: !!a.consent,
      name: a.name || "",
      phone: a.phone || "",
      telecom: a.telecom || "",
      identity: idParts.identity,
      rrn: idParts.identity,
      rrnFront: idParts.rrnFront,
      rrnBack: idParts.rrnBack,
      email: a.email || "",
      readyForMedicalHistory: !!(a.consent && hasIdentity && a.phone),
      readyForInsuranceHistory: !!(a.consent && hasIdentity),
    };
  }

  /** 2차 자동청구 신청 payload */
  function buildAutoClaimPayload() {
    var a = state.answers;
    var correctionNotice = a.accountHolderIsInsured === false;
    return {
      autoClaimConsent: state.autoClaimCompleted
        ? true
        : state.autoClaimDeclined
          ? false
          : null,
      bankName: a.bankName || "",
      accountNumber: a.accountNumber || "",
      accountHolderIsInsured: a.accountHolderIsInsured !== false,
      accountHolderCorrectionNoticeRequired: correctionNotice,
    };
  }

  /** API·저장용 통합 payload */
  function buildAnswersPayload() {
    var reg = buildRegistrationPayload();
    var auto = buildAutoClaimPayload();
    var phase = "intake";
    if (state.autoClaimCompleted) {
      phase = "auto_claim_done";
    } else if (state.autoClaimDeclined) {
      phase = "auto_claim_declined";
    } else if (state.autoClaimStepIndex > 0 || state.answers.autoClaimConsent === true) {
      phase = "auto_claim";
    } else if (state.findStarted) {
      phase = state.findResults ? "find_results" : "find_running";
    }
    var payload = Object.assign({}, reg, auto, { phase: phase });
    state.payload = payload;
    state.answers._payload = payload;
    return payload;
  }

  function hasChatProgress() {
    return (
      state.messages.length > 0 ||
      !!state.answers.name ||
      !!state.findStarted ||
      state.autoClaimStepIndex > 0
    );
  }

  function formatHighlightedText(text) {
    var safe = escapeHtml(String(text || ""));
    var terms = keywords.slice().sort(function (a, b) {
      return b.length - a.length;
    });
    terms.forEach(function (term) {
      if (!term) return;
      var re = new RegExp("(" + term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "g");
      safe = safe.replace(re, "<strong class=\"customer-chat-kw\">$1</strong>");
    });
    return safe;
  }

  function hideConsentDock() {
    if (consentDock) consentDock.hidden = true;
  }

  function showConsentDock() {
    if (consentDock) consentDock.hidden = false;
  }

  function hideSummaryCta() {
    if (summaryCta) summaryCta.hidden = true;
  }

  function showSummaryCta() {
    if (summaryCta) summaryCta.hidden = false;
  }

  var state = loadState() || defaultState();

  (function initFlowIdFromUrl() {
    try {
      var urlFlowId = new URLSearchParams(window.location.search).get("flow_id");
      if (urlFlowId && !state.flowId) {
        state.flowId = urlFlowId;
        state.findStarted = true;
        saveState();
      }
    } catch (e) {
      /* ignore */
    }
  })();

  function scrollToBottom() {
    requestAnimationFrame(function () {
      logEl.scrollTop = logEl.scrollHeight;
    });
  }

  function appendMessage(role, text, options) {
    options = options || {};
    var entry = {
      role: role,
      text: String(text || ""),
      sensitive: !!options.sensitive,
      kind: options.kind || "text",
      highlight: !!options.highlight,
    };
    state.messages.push(entry);
    renderMessage(entry);
    scrollToBottom();
    saveState();
  }

  function renderMessage(entry) {
    var row = document.createElement("div");
    row.className =
      "customer-chat-row customer-chat-row--" +
      (entry.role === "user" ? "user" : "bot");

    if (entry.role === "bot") {
      var avatar = document.createElement("div");
      avatar.className = "customer-chat-avatar";
      avatar.setAttribute("aria-hidden", "true");
      avatar.textContent = "RR";
      row.appendChild(avatar);
    }

    var wrap = document.createElement("div");
    if (entry.kind === "summary") {
      var card = document.createElement("div");
      card.className = "customer-chat-summary-card";
      card.innerHTML = entry.text;
      wrap.appendChild(card);
    } else {
      var bubble = document.createElement("div");
      bubble.className = "customer-chat-bubble";
      if (entry.role === "bot" && entry.highlight) {
        bubble.innerHTML = formatHighlightedText(entry.text);
      } else {
        bubble.textContent = entry.text;
      }
      wrap.appendChild(bubble);
      if (entry.sensitive) {
        var hint = document.createElement("div");
        hint.className = "customer-chat-bubble--sensitive-hint";
        hint.textContent = "민감정보는 마스킹되어 표시됩니다.";
        wrap.appendChild(hint);
      }
    }

    row.appendChild(wrap);
    logEl.appendChild(row);
  }

  function restoreMessages() {
    logEl.innerHTML = "";
    var divider = document.createElement("div");
    divider.className = "customer-chat-date-divider";
    divider.textContent = "오늘";
    logEl.appendChild(divider);
    state.messages.forEach(renderMessage);
    scrollToBottom();
  }

  function currentStep() {
    return steps[state.stepIndex] || null;
  }

  function clearQuickReplies() {
    quickEl.innerHTML = "";
    quickEl.hidden = true;
  }

  function showQuickReplies(replies, handler, options) {
    options = options || {};
    clearQuickReplies();
    if (!replies || !replies.length) {
      return;
    }
    quickEl.hidden = false;
    replies.forEach(function (item) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "customer-chat-quick__btn" +
        (item.primary ? " customer-chat-quick__btn--primary" : "") +
        (item.danger ? " customer-chat-quick__btn--danger" : "");
      btn.textContent = item.label;
      btn.addEventListener("click", function () {
        handler(item.value, item.label);
      });
      quickEl.appendChild(btn);
    });
  }

  function hideForm() {
    formEl.hidden = true;
    inputEl.value = "";
    inputLabelEl.hidden = true;
    sendBtn.disabled = true;
  }

  function hideConsentPanel() {
    if (consentPanel) {
      consentPanel.hidden = true;
    }
    state.consentPanelOpen = false;
    saveState();
  }

  function syncConsentModalBody() {
    if (!consentModalBody || !consentBody || consentModalSynced) {
      return;
    }
    consentModalBody.innerHTML = consentBody.innerHTML;
    consentModalSynced = true;
  }

  function openConsentModal() {
    if (!consentModal) {
      state.consentPanelOpen = true;
      showConsentPanel();
      return;
    }
    syncConsentModalBody();
    consentModal.hidden = false;
    document.body.classList.add("customer-consent-modal-open");
    if (consentModalClose) {
      consentModalClose.focus();
    }
  }

  function closeConsentModal() {
    if (!consentModal) {
      return;
    }
    consentModal.hidden = true;
    document.body.classList.remove("customer-consent-modal-open");
  }

  function showConsentPanel() {
    if (consentPanel) {
      consentPanel.hidden = false;
      consentPanel.classList.toggle(
        "customer-consent-panel--open",
        !!state.consentPanelOpen
      );
    }
    if (consentToggle) {
      consentToggle.textContent = state.consentPanelOpen
        ? "동의서 접기"
        : "동의서 전문 보기";
      consentToggle.setAttribute(
        "aria-expanded",
        state.consentPanelOpen ? "true" : "false"
      );
    }
  }

  function showForm(step) {
    hideConsentPanel();
    formEl.hidden = false;
    inputEl.value = "";
    var inputType = step.inputType || "text";
    if (step.type === "tel") {
      inputType = "tel";
    }
    if (step.type === "email") {
      inputType = "email";
    }
    inputEl.type = inputType;
    inputEl.placeholder = step.placeholder || "입력";
    inputEl.maxLength = step.maxLength || 524288;
    inputEl.inputMode = step.inputMode || "";
    inputEl.autocomplete = step.sensitive ? "off" : "on";
    inputEl.classList.toggle("customer-chat-input--sensitive", !!step.sensitive);
    if (step.sensitive && step.sensitiveLabel) {
      inputLabelEl.hidden = false;
      inputLabelEl.textContent = step.sensitiveLabel;
    } else {
      inputLabelEl.hidden = true;
    }
    sendBtn.disabled = false;
    inputEl.focus();
  }

  function advanceStep() {
    state.stepIndex += 1;
    state.awaitingQuickReply = false;
    saveState();
    runCurrentStep();
  }

  function resetFlow() {
    clearCustomerChatBrowserStorage();
    consentModalSynced = false;
    if (consentModalBody) {
      consentModalBody.innerHTML = "";
    }
    state = defaultState();
    saveState();
    logEl.innerHTML = "";
    logEl.classList.remove("customer-chat-log--after-results");
    hideConsentPanel();
    hideConsentDock();
    hideSummaryCta();
    clearQuickReplies();
    hideForm();
    closeConsentModal();
    if (progressEl) progressEl.hidden = true;
    if (resultsEl) resultsEl.hidden = true;
    if (logEl) logEl.hidden = false;
    if (footerEl) footerEl.hidden = false;
    runCurrentStep();
  }

  function showResultsFollowUpShell() {
    if (progressEl) progressEl.hidden = true;
    if (resultsEl) resultsEl.hidden = false;
    if (
      window.RedRibbonCustomerFindUi &&
      typeof window.RedRibbonCustomerFindUi.bindAiAnalysisButtons === "function"
    ) {
      window.RedRibbonCustomerFindUi.bindAiAnalysisButtons();
    }
    if (logEl) {
      logEl.hidden = false;
      logEl.classList.add("customer-chat-log--after-results");
    }
    var shell = logEl && logEl.parentElement;
    if (shell && resultsEl && logEl && resultsEl !== logEl.previousElementSibling) {
      shell.insertBefore(resultsEl, logEl);
    }
    if (footerEl) footerEl.hidden = false;
    hideSummaryCta();
    hideConsentPanel();
    hideConsentDock();
    scrollToBottom();
  }

  function saveAutoClaimToServer() {
    if (!window.fetch || !state.flowId) {
      return Promise.resolve();
    }
    buildAnswersPayload();
    return fetch("/api/customer/auto-claim/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        flow_id: state.flowId,
        payload: buildAutoClaimPayload(),
      }),
    }).catch(function () {
      /* 데모: 저장 실패해도 UI는 완료 */
    });
  }

  function currentAutoClaimStep() {
    return autoClaimSteps[state.autoClaimStepIndex] || null;
  }

  function advanceAutoClaimStep() {
    state.autoClaimStepIndex += 1;
    saveState();
    runAutoClaimStep();
  }

  function submitAutoClaimAnswer(rawValue, displayText) {
    var step = currentAutoClaimStep();
    if (
      !step ||
      (step.type !== "text" && step.type !== "tel" && step.type !== "email")
    ) {
      return;
    }
    var value = step.formatAnswer
      ? step.formatAnswer(rawValue, state.answers)
      : String(rawValue || "").trim();
    var err = step.validate ? step.validate(value, state.answers) : "";
    if (err) {
      appendMessage("bot", err);
      return;
    }
    state.answers[step.field] = value;
    buildAnswersPayload();
    var shown = "입력 완료";
    if (step.displayAnswer) {
      shown = step.displayAnswer(value, state.answers) || shown;
    } else if (!step.sensitive) {
      shown = displayText || value;
    }
    appendMessage("user", shown, { sensitive: !!step.sensitive });
    clearQuickReplies();
    hideForm();
    advanceAutoClaimStep();
  }

  function runAutoClaimInputStep(step) {
    hideConsentPanel();
    appendMessage("bot", step.prompt, { highlight: true });
    if (step.quickReplies && step.quickReplies.length) {
      showQuickReplies(step.quickReplies, function (value, label) {
        if (step.quickRepliesFillOnly) {
          inputEl.value = value;
          showForm(step);
          inputEl.focus();
          saveState();
          return;
        }
        submitAutoClaimAnswer(value, label || value);
      });
    }
    showForm(step);
    saveState();
  }

  function runAutoClaimYesNoStep(step) {
    hideForm();
    appendMessage("bot", step.prompt, { highlight: true });
    showQuickReplies(
      [
        { label: step.yesLabel || "예", value: "yes", primary: true },
        { label: step.noLabel || "아니오", value: "no" },
      ],
      function (value, label) {
        var isYes = value === "yes";
        state.answers[step.field] = isYes;
        if (!isYes) {
          state.answers.accountHolderCorrectionNoticeRequired = true;
        } else {
          state.answers.accountHolderCorrectionNoticeRequired = false;
        }
        buildAnswersPayload();
        appendMessage("user", label || value);
        if (!isYes && step.noFollowUp) {
          appendMessage("bot", step.noFollowUp, { highlight: true });
        }
        clearQuickReplies();
        state.autoClaimStepIndex += 1;
        state.autoClaimCompleted = true;
        saveState();
        saveAutoClaimToServer().finally(function () {
          appendMessage(
            "bot",
            "자동청구 신청 정보가 저장되었습니다. (데모: 보험사 전송은 하지 않습니다.)",
            { highlight: true }
          );
        });
      }
    );
    saveState();
  }

  function runAutoClaimStep() {
    var step = currentAutoClaimStep();
    if (!step) {
      hideForm();
      clearQuickReplies();
      return;
    }
    if (step.type === "text" || step.type === "tel" || step.type === "email") {
      runAutoClaimInputStep(step);
      return;
    }
    if (step.type === "yesno") {
      runAutoClaimYesNoStep(step);
      return;
    }
    advanceAutoClaimStep();
  }

  function startAutoClaimPhase() {
    if (state.autoClaimPromptDone) {
      showResultsFollowUpShell();
      return;
    }
    state.autoClaimPromptDone = true;
    showResultsFollowUpShell();
    runBotMessages(
      [
        "병원을 갔을 때 따로 보험금 청구를 할 필요 없이, 실손보험을 비롯한 보험금 자동청구를 이용하시겠습니까?",
      ],
      function () {
        showQuickReplies(
          [
            {
              label: "예, 자동청구를 이용하겠습니다",
              value: "yes",
              primary: true,
            },
            { label: "아니오, 나중에 하겠습니다", value: "no" },
          ],
          function (value, label) {
            appendMessage("user", label || value);
            clearQuickReplies();
            if (value === "no") {
              state.autoClaimDeclined = true;
              state.answers.autoClaimConsent = false;
              buildAnswersPayload();
              saveState();
              saveAutoClaimToServer();
              appendMessage(
                "bot",
                "알겠습니다. 지난 보험금 찾기 결과만 확인하실 수 있습니다.",
                { highlight: true }
              );
              hideForm();
              return;
            }
            state.answers.autoClaimConsent = true;
            state.autoClaimStepIndex = 0;
            buildAnswersPayload();
            saveState();
            appendMessage(
              "bot",
              "자동청구를 위해 수령 계좌 정보를 입력해 주세요.",
              { highlight: true }
            );
            runAutoClaimStep();
          }
        );
      }
    );
  }

  function runBotMessages(messages, onDone) {
    var msgs = messages || [];
    var index = 0;
    hideForm();
    clearQuickReplies();

    function nextMsg() {
      if (index >= msgs.length) {
        if (onDone) {
          onDone();
        }
        return;
      }
      appendMessage("bot", msgs[index], { highlight: true });
      index += 1;
      setTimeout(nextMsg, 360);
    }

    nextMsg();
  }

  function handleConsentAgree() {
    state.answers.consent = true;
    buildAnswersPayload();
    appendMessage("user", "동의합니다");
    hideConsentPanel();
    hideConsentDock();
    advanceStep();
  }

  function handleConsentDecline() {
    state.answers.consent = false;
    state.declined = true;
    appendMessage("user", "동의하지 않습니다");
    hideConsentPanel();
    hideConsentDock();
    clearQuickReplies();
    hideForm();
    runBotMessages(
      ["동의가 필요하여 접수를 진행할 수 없습니다."],
      function () {
        showQuickReplies(
          [{ label: "처음으로", value: "reset", primary: true }],
          function () {
            resetFlow();
          }
        );
        state.awaitingQuickReply = true;
        saveState();
      }
    );
  }

  function runConsentStep(step) {
    runBotMessages(step.messages, function () {
      hideConsentPanel();
      showConsentDock();
      state.awaitingQuickReply = true;
      saveState();
    });
  }

  function submitAnswer(rawValue, displayText) {
    var step = currentStep();
    if (
      !step ||
      (step.type !== "text" && step.type !== "tel" && step.type !== "email")
    ) {
      return;
    }

    var value = step.formatAnswer
      ? step.formatAnswer(rawValue, state.answers)
      : String(rawValue || "").trim();

    var err = step.validate ? step.validate(value, state.answers) : "";
    if (err) {
      appendMessage("bot", err);
      return;
    }

    state.answers[step.field] = value;
    buildAnswersPayload();
    var shown = "입력 완료";
    if (step.displayAnswer) {
      shown = step.displayAnswer(value, state.answers) || shown;
    } else if (!step.sensitive) {
      shown = displayText || value;
    }
    appendMessage("user", shown, { sensitive: !!step.sensitive });
    clearQuickReplies();
    hideForm();
    advanceStep();
  }

  function submitChoice(value, label) {
    var step = currentStep();
    if (!step || step.type !== "choice") {
      return;
    }
    var err = step.validate ? step.validate(value, state.answers) : "";
    if (err) {
      appendMessage("bot", err);
      return;
    }
    state.answers[step.field] = value;
    buildAnswersPayload();
    appendMessage("user", label || value);
    clearQuickReplies();
    hideForm();
    advanceStep();
  }

  function buildSummaryHtml() {
    var a = state.answers;
    var consentLabel = a.consent ? "동의 완료" : "미동의";
    return (
      "<ul class=\"customer-chat-summary-list\">" +
      "<li><span>고객명</span><strong>" +
      escapeHtml(a.name || "—") +
      "</strong></li>" +
      "<li><span>휴대폰번호</span><strong>" +
      escapeHtml(mask.phone(a.phone)) +
      "</strong></li>" +
      "<li><span>통신사</span><strong>" +
      escapeHtml(a.telecom || "—") +
      "</strong></li>" +
      "<li><span>주민번호</span><strong>" +
      escapeHtml(mask.identity(a.identity)) +
      "</strong></li>" +
      "<li><span>이메일</span><strong>" +
      escapeHtml(a.email || "—") +
      "</strong></li>" +
      "<li><span>동의</span><strong>" +
      consentLabel +
      "</strong></li>" +
      "</ul>"
    );
  }

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function saveDraftToServer() {
    if (!window.fetch) {
      return Promise.resolve();
    }
    var payload = buildRegistrationPayload();
    return fetch("/api/customer/chat/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        draft_id: state.draftId || null,
        consent: !!state.answers.consent,
        phase: "intake",
        answers: {
          name: state.answers.name,
          phone_masked: mask.phone(state.answers.phone),
          telecom: state.answers.telecom,
          rrn_masked: mask.identity(state.answers.identity),
          email: state.answers.email,
          readyForMedicalHistory: payload.readyForMedicalHistory,
          readyForInsuranceHistory: payload.readyForInsuranceHistory,
        },
      }),
    })
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (data && data.draft_id) {
          state.draftId = data.draft_id;
          saveState();
        }
      })
      .catch(function () {
        /* 데모: 서버 저장 실패해도 UI는 완료 */
      });
  }

  function handleSummarySubmit() {
    buildAnswersPayload();
    appendMessage("user", "지난 보험금 찾기 시작");
    hideSummaryCta();
    hideForm();
    state.stepIndex += 1;
    state.finished = true;
    saveState();
    saveDraftToServer().finally(function () {
      if (window.RedRibbonCustomerFindUi) {
        window.RedRibbonCustomerFindUi.startFindFlow();
      }
    });
  }

  function runSummaryStep(step) {
    hideConsentPanel();
    hideConsentDock();
    hideForm();
    clearQuickReplies();
    buildAnswersPayload();
    appendMessage("bot", step.prompt, { highlight: true });
    appendMessage("bot", buildSummaryHtml(), { kind: "summary" });
    showSummaryCta();
    saveState();
  }

  function runYesNoStep(step) {
    hideConsentPanel();
    hideForm();
    appendMessage("bot", step.prompt, { highlight: true });
    showQuickReplies(
      [
        { label: step.yesLabel || "예", value: "yes", primary: true },
        { label: step.noLabel || "아니오", value: "no" },
      ],
      function (value, label) {
        var isYes = value === "yes";
        state.answers[step.field] = isYes;
        if (!isYes) {
          state.answers.accountHolderCorrectionNoticeRequired = true;
        } else {
          state.answers.accountHolderCorrectionNoticeRequired = false;
        }
        buildAnswersPayload();
        appendMessage("user", label || value);
        if (!isYes && step.noFollowUp) {
          appendMessage("bot", step.noFollowUp, { highlight: true });
        }
        clearQuickReplies();
        advanceStep();
      }
    );
    saveState();
  }

  function runInputStep(step) {
    hideConsentPanel();
    if (step.introMessages && step.introMessages.length) {
      step.introMessages.forEach(function (msg) {
        appendMessage("bot", msg, { highlight: true });
      });
    }
    appendMessage("bot", step.prompt, { highlight: true });
    if (step.quickReplies && step.quickReplies.length) {
      showQuickReplies(step.quickReplies, function (value, label) {
        if (step.quickRepliesFillOnly) {
          inputEl.value = value;
          showForm(step);
          inputEl.focus();
          saveState();
          return;
        }
        submitAnswer(value, label || value);
      });
    }
    showForm(step);
    saveState();
  }

  function runChoiceStep(step) {
    hideConsentPanel();
    hideForm();
    appendMessage("bot", step.prompt);
    var choices = (step.choices || []).map(function (c) {
      return { label: c.label, value: c.value };
    });
    showQuickReplies(choices, function (value, label) {
      submitChoice(value, label);
    });
    saveState();
  }

  function runCurrentStep() {
    if (state.declined) {
      return;
    }
    var step = currentStep();
    if (!step) {
      hideForm();
      hideConsentPanel();
      clearQuickReplies();
      state.finished = true;
      saveState();
      return;
    }

    if (step.type === "consent") {
      runConsentStep(step);
      return;
    }
    if (step.type === "text" || step.type === "tel" || step.type === "email") {
      runInputStep(step);
      return;
    }
    if (step.type === "choice") {
      runChoiceStep(step);
      return;
    }
    if (step.type === "summary") {
      runSummaryStep(step);
      return;
    }
    if (step.type === "yesno") {
      runYesNoStep(step);
      return;
    }
    advanceStep();
  }

  if (consentToggle) {
    consentToggle.addEventListener("click", function () {
      state.consentPanelOpen = !state.consentPanelOpen;
      showConsentPanel();
      saveState();
    });
  }

  if (consentModalClose) {
    consentModalClose.addEventListener("click", closeConsentModal);
  }
  if (consentModalBackdrop) {
    consentModalBackdrop.addEventListener("click", closeConsentModal);
  }
  document.addEventListener("keydown", function (event) {
    if (
      event.key === "Escape" &&
      consentModal &&
      !consentModal.hidden
    ) {
      closeConsentModal();
    }
  });

  formEl.addEventListener("submit", function (event) {
    event.preventDefault();
    if (
      state.autoClaimPromptDone &&
      !state.autoClaimDeclined &&
      !state.autoClaimCompleted &&
      currentAutoClaimStep()
    ) {
      var acStep = currentAutoClaimStep();
      if (
        acStep &&
        (acStep.type === "text" || acStep.type === "tel" || acStep.type === "email")
      ) {
        submitAutoClaimAnswer(inputEl.value, inputEl.value);
      }
      return;
    }
    var step = currentStep();
    if (
      !step ||
      (step.type !== "text" && step.type !== "tel" && step.type !== "email")
    ) {
      return;
    }
    submitAnswer(inputEl.value, inputEl.value);
  });

  function resumeUi() {
    restoreMessages();
    var promptAutoClaim =
      document.body &&
      document.body.getAttribute("data-prompt-auto-claim") === "1";
    if (promptAutoClaim || state.pendingAutoClaimOnReturn) {
      state.pendingAutoClaimOnReturn = false;
      saveState();
    }
    var step = currentStep();
    if (state.declined) {
      hideForm();
      hideConsentPanel();
      showQuickReplies(
        [{ label: "처음으로", value: "reset", primary: true }],
        function () {
          resetFlow();
        }
      );
      return;
    }
    if (state.finished || !step) {
      hideForm();
      hideConsentPanel();
      return;
    }
    if (state.findStarted && state.flowId && progressEl) {
      if (state.findResults) {
        if (window.RedRibbonCustomerFindUi) {
          window.RedRibbonCustomerFindUi.showResultsView();
        }
        if (state.autoClaimPromptDone) {
          showResultsFollowUpShell();
          if (state.autoClaimDeclined || state.autoClaimCompleted) {
            hideForm();
            clearQuickReplies();
          } else if (state.autoClaimStepIndex > 0) {
            runAutoClaimStep();
          }
        } else if (promptAutoClaim && !state.autoClaimPromptDone) {
          showResultsFollowUpShell();
          startAutoClaimPhase();
        }
      } else if (window.RedRibbonCustomerFindUi) {
        if (state.findStatus && state.findStatus.done) {
          window.RedRibbonCustomerFindUi.fetchResults();
        } else if (window.RedRibbonCustomerFindUi.applyStatus) {
          window.RedRibbonCustomerFindUi.applyStatus(state.findStatus);
        } else {
          window.RedRibbonCustomerFindUi.showProgressView(
            (state.findStatus && state.findStatus.message) || "진행 중",
            state.findStatus && state.findStatus.phase
          );
        }
      }
      return;
    }
    if (step.type === "consent") {
      if (state.awaitingQuickReply) showConsentDock();
      else runCurrentStep();
      hideForm();
      return;
    }
    if (step.type === "summary") {
      hideForm();
      showSummaryCta();
      return;
    }
    if (step.type === "yesno") {
      hideForm();
      var ynStep = currentStep();
      if (ynStep) {
        showQuickReplies(
          [
            { label: ynStep.yesLabel || "예", value: "yes", primary: true },
            { label: ynStep.noLabel || "아니오", value: "no" },
          ],
          function (value, label) {
            var isYes = value === "yes";
            state.answers[ynStep.field] = isYes;
            buildAnswersPayload();
            appendMessage("user", label);
            if (!isYes && ynStep.noFollowUp) {
              appendMessage("bot", ynStep.noFollowUp, { highlight: true });
            }
            advanceStep();
          }
        );
      }
      return;
    }
    if (step.type === "choice") {
      hideForm();
      var choices = (step.choices || []).map(function (c) {
        return { label: c.label, value: c.value };
      });
      showQuickReplies(choices, function (value, label) {
        submitChoice(value, label);
      });
      return;
    }
    if (step.type === "text" || step.type === "tel" || step.type === "email") {
      if (step.quickReplies) {
        showQuickReplies(step.quickReplies, function (value, label) {
          if (step.quickRepliesFillOnly) {
            inputEl.value = value;
            showForm(step);
            return;
          }
          submitAnswer(value, label || value);
        });
      }
      showForm(step);
    }
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      if (!window.confirm(RESET_CONFIRM_MESSAGE)) return;
      resetCustomerChatSession();
    });
  }

  if (withdrawBtn) {
    withdrawBtn.addEventListener("click", runCustomerWithdraw);
  }

  if (exitBtn) {
    exitBtn.addEventListener("click", function (event) {
      event.preventDefault();
      var induceAutoClaim =
        state.findResults &&
        state.aiInlineOpened &&
        !state.autoClaimCompleted &&
        !state.autoClaimDeclined &&
        !state.autoClaimPromptDone;
      if (induceAutoClaim) {
        state.pendingAutoClaimOnReturn = true;
        saveState();
        window.location.href = "/customer/chat?auto_claim=1";
        return;
      }
      if (hasChatProgress()) {
        var ok = window.confirm(
          "입력 중인 내용이 사라질 수 있습니다. 나가시겠습니까?"
        );
        if (!ok) return;
      }
      window.location.href = "/";
    });
  }

  if (consentDockView) {
    consentDockView.addEventListener("click", openConsentModal);
  }
  if (consentDock) {
    consentDock.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-consent]");
      if (!btn) return;
      if (btn.getAttribute("data-consent") === "agree") handleConsentAgree();
      if (btn.getAttribute("data-consent") === "decline") handleConsentDecline();
    });
  }
  if (summaryCtaStart) {
    summaryCtaStart.addEventListener("click", handleSummarySubmit);
  }
  if (summaryCtaReset) {
    summaryCtaReset.addEventListener("click", resetFlow);
  }

  window.RedRibbonCustomerChatApi = {
    state: state,
    buildRegistrationPayload: buildRegistrationPayload,
    buildAutoClaimPayload: buildAutoClaimPayload,
    buildAnswersPayload: buildAnswersPayload,
    startAutoClaimPhase: startAutoClaimPhase,
    showResultsFollowUpShell: showResultsFollowUpShell,
    logEl: logEl,
    footerEl: footerEl,
    progressEl: progressEl,
    progressMessageEl: progressMessageEl,
    progressSubtitleEl: progressSubtitleEl,
    progressStageTitleEl: progressStageTitleEl,
    progressHeadingEl: progressHeadingEl,
    kakaoHighlightEl: kakaoHighlightEl,
    kakaoHighlightTextEl: kakaoHighlightTextEl,
    progressBadgesEl: progressBadgesEl,
    progressHintEl: progressHintEl,
    progressLoadingEl: progressLoadingEl,
    progressLoadingLineEl: progressLoadingLineEl,
    progressLoadingSubEl: progressLoadingSubEl,
    progressStepsEl: progressStepsEl,
    authConfirmBtn: authConfirmBtn,
    resultsEl: resultsEl,
    resultMedicalStatus: resultMedicalStatus,
    resultInsuranceStatus: resultInsuranceStatus,
    resultAiStatus: resultAiStatus,
    viewSheet: viewSheet,
    viewSheetTitle: viewSheetTitle,
    viewSheetBody: viewSheetBody,
    viewSheetClose: viewSheetClose,
    viewSheetBackdrop: viewSheetBackdrop,
    buildAnswersPayload: buildAnswersPayload,
    saveState: saveState,
    clearCustomerChatBrowserStorage: clearCustomerChatBrowserStorage,
    clearCustomerUiAfterWithdraw: clearCustomerUiAfterWithdraw,
    escapeHtml: escapeHtml,
  };

  if (state.messages.length) {
    buildAnswersPayload();
    resumeUi();
  } else {
    runCurrentStep();
  }
})();
