/**

 * 고객 채팅 — 지난 보험금 찾기 진행·결과 UI (customer_chat.js에서 로드)

 * 버튼 클릭 시에만 advance 1회 호출(자동 poll 없음).

 */

(function (root) {

  "use strict";

  var api = root.RedRibbonCustomerChatApi;

  if (!api) return;



  var ADVANCE_TIMEOUT_MS = 20000;

  var pendingAuthAction = null;

  var advanceInFlight = false;

  var advanceAbortController = null;

  var advanceTimeoutId = null;



  var MEDICAL_LOADING_STEPS = [

    "인증 확인 중입니다.",

    "진료내역을 가져오고 있습니다.",

    "진료내역을 정리하고 있습니다.",

    "진료내역 조회가 완료되었습니다.",

  ];

  var INSURANCE_LOADING_STEPS = [

    "인증 확인 중입니다.",

    "보험가입이력을 가져오고 있습니다.",

    "보험계약 정보를 정리하고 있습니다.",

    "보험가입이력 조회가 완료되었습니다.",

  ];

  var ACTION_LOADING = {

    confirm_medical_auth: {

      phase: "medical_loading",

      buttonLabel: "진료내역을 가져오는 중입니다...",

      line1: MEDICAL_LOADING_STEPS[0],

      line2: "",

    },

    confirm_insurance_auth: {

      phase: "insurance_loading",

      buttonLabel: "보험가입이력을 가져오는 중입니다...",

      line1: INSURANCE_LOADING_STEPS[0],

      line2: "",

    },

  };



  var PROGRESS_STEPS = [

    { key: "saving", label: "고객정보 저장 중" },

    { key: "medical_auth_waiting", label: "진료내역 카카오 인증" },

    { key: "medical_loading", label: "진료내역 확인 중" },

    { key: "insurance_auth_waiting", label: "보험가입이력 카카오 인증" },

    { key: "insurance_loading", label: "보험가입이력 확인 중" },

    { key: "ai_preparing", label: "AI 분석 준비 중" },

    { key: "complete", label: "완료" },

  ];



  function normalizeStatus(data) {

    if (!data) return null;

    var st = data.status && typeof data.status === "object" ? data.status : data;

    if (!st || typeof st !== "object") return null;

    st.phase = st.stage || st.phase || "saving";

    st.stage = st.phase;

    if (st.show_button == null) st.show_button = st.show_auth_button;

    if (st.button_label == null) st.button_label = st.auth_button_label;

    if (st.next_action == null) st.next_action = st.auth_action;

    return st;

  }



  function renderProgressSteps(activeKey) {

    var list = api.progressStepsEl;

    if (!list) return;

    list.innerHTML = "";

    PROGRESS_STEPS.forEach(function (item) {

      var li = document.createElement("li");

      li.className = "customer-find-progress__step";

      if (item.key === activeKey) li.classList.add("is-active");

      if (_stepDoneBefore(activeKey, item.key)) {

        li.classList.add("is-done");

      }

      li.textContent = item.label;

      list.appendChild(li);

    });

  }



  function _stepDoneBefore(active, key) {

    var order = PROGRESS_STEPS.map(function (s) {

      return s.key;

    });

    var activeIdx = order.indexOf(active);

    var keyIdx = order.indexOf(key);

    if (activeIdx < 0 || keyIdx < 0) return false;

    return keyIdx < activeIdx;

  }



  function renderProgressBadges(badges) {

    var list = api.progressBadgesEl;

    if (!list || !badges || !badges.length) {

      if (list) list.innerHTML = "";

      return;

    }

    list.innerHTML = badges

      .map(function (badge) {

        var cls = "customer-find-progress__badge";

        if (badge.active) cls += " is-active";

        if (badge.done) cls += " is-done";

        return (

          '<li class="' +

          cls +

          '"><span>' +

          api.escapeHtml(badge.label || "") +

          "</span></li>"

        );

      })

      .join("");

  }



  function runStagedLoading(action, steps, onComplete) {

    var cfg = ACTION_LOADING[action];

    if (!cfg || !steps || !steps.length) {

      if (onComplete) onComplete();

      return;

    }

    advanceInFlight = true;

    if (api.kakaoHighlightEl) api.kakaoHighlightEl.hidden = true;

    var index = 0;

    function tick() {

      if (index >= steps.length) {

        if (onComplete) onComplete();

        return;

      }

      setLoadingUi({

        phase: cfg.phase,

        line1: steps[index],

        line2: "",

        buttonLabel: cfg.buttonLabel,

      });

      index += 1;

      setTimeout(tick, 850 + Math.floor(Math.random() * 350));

    }

    setLoadingUi(cfg);

    setTimeout(tick, 400);

  }



  function setLoadingUi(cfg) {

    if (!cfg) return;

    if (api.progressEl) api.progressEl.classList.add("is-loading");

    if (api.progressLoadingEl) api.progressLoadingEl.hidden = false;

    if (api.progressLoadingLineEl) {

      api.progressLoadingLineEl.textContent = cfg.line1;

    }

    if (api.progressLoadingSubEl) {

      api.progressLoadingSubEl.textContent = cfg.line2;

    }

    if (api.progressSubtitleEl) {

      api.progressSubtitleEl.textContent = cfg.line1;

      api.progressSubtitleEl.hidden = false;

    }

    if (api.progressMessageEl) {

      api.progressMessageEl.textContent = cfg.line2;

    }

    if (api.progressHintEl) api.progressHintEl.hidden = true;

    if (api.authConfirmBtn) {

      api.authConfirmBtn.hidden = false;

      api.authConfirmBtn.disabled = true;

      api.authConfirmBtn.classList.add("is-busy");

      api.authConfirmBtn.textContent = cfg.buttonLabel;

    }

    renderProgressSteps(cfg.phase);

  }



  function clearLoadingUi() {

    advanceInFlight = false;

    if (advanceTimeoutId) {

      clearTimeout(advanceTimeoutId);

      advanceTimeoutId = null;

    }

    if (advanceAbortController) {

      try {

        advanceAbortController.abort();

      } catch (e) {

        /* ignore */

      }

      advanceAbortController = null;

    }

    if (api.progressEl) api.progressEl.classList.remove("is-loading");

    if (api.progressLoadingEl) api.progressLoadingEl.hidden = true;

    if (api.authConfirmBtn) {

      api.authConfirmBtn.classList.remove("is-busy");

    }

  }



  function showAdvanceError(message, phaseKey, retryAction, retryLabel) {

    clearLoadingUi();

    showProgressView(message, phaseKey || "failed");

    if (api.progressSubtitleEl) api.progressSubtitleEl.hidden = true;

    if (api.progressMessageEl) api.progressMessageEl.textContent = message;

    if (api.progressHintEl) {

      api.progressHintEl.textContent = "아래 버튼으로 다시 시도해 주세요.";

      api.progressHintEl.hidden = false;

    }

    pendingAuthAction = retryAction || "retry";

    if (api.authConfirmBtn) {

      api.authConfirmBtn.hidden = false;

      api.authConfirmBtn.disabled = false;

      api.authConfirmBtn.textContent = retryLabel || "다시 시도";

    }

  }



  function applyStatus(st) {

    if (!st) return;

    clearLoadingUi();

    var phase = st.stage || st.phase || "saving";

    var lines = [];

    if (st.subtitle) lines.push(st.subtitle);

    if (st.message) lines.push(st.message);

    showProgressView(lines.join("\n"), phase);



    if (api.progressSubtitleEl) {

      if (st.subtitle) {

        api.progressSubtitleEl.textContent = st.subtitle;

        api.progressSubtitleEl.hidden = false;

      } else {

        api.progressSubtitleEl.hidden = true;

      }

    }

    if (api.progressMessageEl && st.message) {

      api.progressMessageEl.textContent = st.message;

    }

    if (api.progressStageTitleEl) {

      if (st.progress_title) {

        api.progressStageTitleEl.textContent = st.progress_title;

        api.progressStageTitleEl.hidden = false;

      } else {

        api.progressStageTitleEl.hidden = true;

      }

    }

    if (api.kakaoHighlightEl) {

      var showBanner = !!(st.show_kakao_banner || st.kakao_highlight);

      api.kakaoHighlightEl.hidden = !showBanner;

      if (api.kakaoHighlightTextEl && st.kakao_highlight) {

        api.kakaoHighlightTextEl.textContent = st.kakao_highlight;

      }

    }

    renderProgressBadges(st.progress_badges || []);

    if (api.progressHintEl) {

      var showHint =

        (st.show_button || st.show_auth_button) &&

        (st.kakao_hint || st.awaiting_user_action);

      if (showHint) {

        api.progressHintEl.textContent =

          st.kakao_hint || "휴대폰에서 인증을 완료한 뒤 아래 버튼을 눌러 주세요.";

        api.progressHintEl.hidden = false;

      } else if (st.failed && (st.show_retry || st.show_button)) {

        api.progressHintEl.textContent = "아래 버튼으로 다시 시도해 주세요.";

        api.progressHintEl.hidden = false;

      } else {

        api.progressHintEl.hidden = true;

      }

    }



    var nextAction = st.next_action || st.auth_action || null;

    var showBtn = !!(st.show_button || st.show_auth_button);

    var btnLabel = st.button_label || st.auth_button_label;



    pendingAuthAction = null;

    if (api.authConfirmBtn) {

      if (showBtn && btnLabel) {

        api.authConfirmBtn.hidden = false;

        api.authConfirmBtn.textContent = btnLabel;

        api.authConfirmBtn.disabled = false;

        pendingAuthAction = nextAction;

      } else if (st.failed && (st.show_retry || nextAction)) {

        api.authConfirmBtn.hidden = false;

        api.authConfirmBtn.textContent = btnLabel || "다시 시도";

        pendingAuthAction = nextAction || "retry";

        api.authConfirmBtn.disabled = false;

      } else {

        api.authConfirmBtn.hidden = true;

      }

    }

  }



  function showProgressView(message, phaseKey) {

    if (api.logEl) api.logEl.hidden = true;

    if (api.footerEl) api.footerEl.hidden = true;

    if (api.progressEl) {

      api.progressEl.hidden = false;

      if (api.progressMessageEl && message) {

        var parts = String(message).split("\n");

        if (parts.length > 1 && api.progressSubtitleEl) {

          api.progressSubtitleEl.textContent = parts[0];

          api.progressSubtitleEl.hidden = false;

          api.progressMessageEl.textContent = parts.slice(1).join(" ");

        } else if (api.progressMessageEl) {

          api.progressMessageEl.textContent = message || "진행 중";

        }

      }

      renderProgressSteps(phaseKey || "saving");

    }

    if (api.resultsEl) api.resultsEl.hidden = true;

  }



  function showResultsView() {

    clearLoadingUi();

    if (api.progressEl) api.progressEl.hidden = true;

    if (api.resultsEl) api.resultsEl.hidden = false;

    if (api.authConfirmBtn) api.authConfirmBtn.hidden = true;

    pendingAuthAction = null;

    if (api.logEl) {

      api.logEl.hidden = false;

      api.logEl.classList.add("customer-chat-log--after-results");

    }

    if (api.footerEl) api.footerEl.hidden = false;

    var autoBtn = document.getElementById("customer-auto-claim-cta");

    if (autoBtn) autoBtn.hidden = false;

  }



  function parseJsonResponse(res) {

    return res.text().then(function (text) {

      if (!text) return {};

      try {

        return JSON.parse(text);

      } catch (e) {

        return { ok: false, parse_error: true, raw: text };

      }

    });

  }



  function callAdvanceFetch(action) {

    if (!api.state.flowId) return Promise.resolve();



    advanceInFlight = true;

    advanceAbortController = new AbortController();

    var signal = advanceAbortController.signal;



    advanceTimeoutId = setTimeout(function () {

      if (!advanceInFlight) return;

      try {

        advanceAbortController.abort();

      } catch (e) {

        /* ignore */

      }

      showAdvanceError(

        "응답이 지연되고 있습니다. 다시 시도해 주세요.",

        "failed",

        action || "retry",

        "다시 시도"

      );

    }, ADVANCE_TIMEOUT_MS);



    return fetch("/api/customer/find/advance", {

      method: "POST",

      headers: { "Content-Type": "application/json" },

      body: JSON.stringify({

        flow_id: api.state.flowId,

        action: action || null,

        confirm_auth: false,

      }),

      signal: signal,

    })

      .then(function (res) {

        return parseJsonResponse(res).then(function (data) {

          if (!res.ok) {

            data = data || {};

            data.ok = false;

          }

          return data;

        });

      })

      .then(function (data) {

        if (!advanceInFlight && !data) return data;

        clearLoadingUi();



        if (!data || data.parse_error) {

          showAdvanceError(

            "서버 응답을 처리하지 못했습니다. 다시 시도해 주세요.",

            "failed",

            action || "retry"

          );

          return data;

        }



        if (!data.ok) {

          showAdvanceError(

            data.message ||

              data.error ||

              "요청을 처리하지 못했습니다. 다시 시도해 주세요.",

            data.stage || "failed",

            data.next_action || action || "retry",

            data.button_label || "다시 시도"

          );

          return data;

        }



        var st = normalizeStatus(data);

        if (!st) {

          showAdvanceError(

            "진행 상태를 받지 못했습니다. 다시 시도해 주세요.",

            "failed",

            action || "retry"

          );

          return data;

        }



        api.state.findStatus = st;

        api.saveState();

        applyStatus(st);



        if (st.done) {

          return fetchResults();

        }

        return data;

      })

      .catch(function (err) {

        if (err && err.name === "AbortError") {

          return;

        }

        showAdvanceError(

          "네트워크 오류가 발생했습니다. 다시 시도해 주세요.",

          "failed",

          action || "retry"

        );

      });

  }



  function callAdvance(action) {

    if (!api.state.flowId) return Promise.resolve();

    if (advanceInFlight) return Promise.resolve();



    var stCached = api.state.findStatus;

    if (stCached && stCached.done) {

      return fetchResults();

    }



    if (action === "retry" && api.authConfirmBtn) {

      api.authConfirmBtn.disabled = true;

      api.authConfirmBtn.classList.add("is-busy");

      return callAdvanceFetch(action);

    }



    if (action === "confirm_medical_auth" || action === "confirm_insurance_auth") {

      var steps =

        action === "confirm_medical_auth"

          ? MEDICAL_LOADING_STEPS

          : INSURANCE_LOADING_STEPS;

      return new Promise(function (resolve) {

        runStagedLoading(action, steps, function () {

          callAdvanceFetch(action).then(resolve);

        });

      });

    }



    return callAdvanceFetch(action);

  }



  function fetchResults() {

    return fetch(

      "/api/customer/find/results?flow_id=" +

        encodeURIComponent(api.state.flowId)

    )

      .then(function (res) {

        return parseJsonResponse(res);

      })

      .then(function (data) {

        if (data && data.ok) {

          api.state.findResults = data;

          api.state.finished = true;

          api.saveState();

          bindResultsView(data);

          showResultsView();

        } else if (data && data.status) {

          applyStatus(normalizeStatus(data));

        }

        return data;

      })

      .catch(function () {

        showAdvanceError(

          "결과를 불러오지 못했습니다. 다시 시도해 주세요.",

          "complete",

          "retry",

          "다시 시도"

        );

      });

  }



  function triggerAutoClaimFromResults() {

    if (api.resultsEl) api.resultsEl.hidden = true;

    var inline = document.getElementById("customer-ai-inline");

    if (inline) inline.hidden = true;

    if (api.logEl) {

      api.logEl.hidden = false;

      api.logEl.classList.remove("customer-chat-log--after-results");

    }

    if (api.state) {

      api.state.pendingAutoClaimOnReturn = true;

      api.saveState();

    }

    if (api.startAutoClaimPhase) api.startAutoClaimPhase();

  }



  function buildInlineAiHtml(aiBlock) {

    var ai = aiBlock || {};

    var reviewCount =

      ai.review_candidate_count != null

        ? ai.review_candidate_count

        : ai.high_count != null

          ? ai.high_count

          : ai.candidate_count || 0;

    var actualLossHtml = "";

    (ai.actual_loss_products || []).forEach(function (p) {

      actualLossHtml +=

        "<li>" +

        api.escapeHtml(p.company_name || "—") +

        " · " +

        api.escapeHtml(p.product_name || "—") +

        "</li>";

    });

    if (!actualLossHtml) {

      actualLossHtml =

        "<li>" + api.escapeHtml(ai.actual_loss_label || "—") + "</li>";

    }

    return (

      "<div class=\"customer-ai-inline-card\">" +

      "<div class=\"customer-ai-inline-card__head\">" +

      "<h3 class=\"customer-ai-inline-card__title\">AI 분석 요약</h3>" +

      "<button type=\"button\" class=\"customer-ai-inline-card__auto-claim\" data-action=\"auto-claim-inline\">자동청구를 이용하기</button>" +

      "</div>" +

      "<div class=\"customer-ai-inline-card__grid\">" +

      "<article class=\"customer-ai-inline-card__stat\">" +

      "<span class=\"customer-ai-inline-card__stat-label\">우선 검토 후보</span>" +

      "<strong class=\"customer-ai-inline-card__stat-value\">" +

      api.escapeHtml(String(reviewCount)) +

      "건</strong></article>" +

      "<article class=\"customer-ai-inline-card__stat\">" +

      "<span class=\"customer-ai-inline-card__stat-label\">검토 가능 금액</span>" +

      "<strong class=\"customer-ai-inline-card__stat-value\">" +

      api.escapeHtml(ai.review_amount_display || ai.priority_review_display || ai.estimated_display || "—") +

      "</strong></article></div>" +

      "<h4 class=\"customer-ai-inline-card__subtitle\">우선 확인 진료</h4>" +

      "<ul class=\"customer-view-list\">" +

      (ai.priority_visits || [])

        .map(function (v) {

          return (

            "<li><strong>" +

            api.escapeHtml(v.visit_date) +

            "</strong> " +

            api.escapeHtml(v.hospital_name) +

            " — " +

            api.escapeHtml(v.label) +

            "</li>"

          );

        })

        .join("") +

      "</ul>" +

      "<h4 class=\"customer-ai-inline-card__subtitle\">관련 실손보험</h4>" +

      "<ul class=\"customer-view-list customer-ai-inline-card__actual-loss\">" +

      actualLossHtml +

      "</ul>" +

      "<h4 class=\"customer-ai-inline-card__subtitle\">필요한 서류</h4>" +

      "<ul class=\"customer-view-list\">" +

      (ai.documents_needed || [])

        .map(function (d) {

          return "<li>" + api.escapeHtml(d) + "</li>";

        })

        .join("") +

      "</ul>" +

      "<p class=\"customer-ai-inline-card__disclaimer\">" +

      api.escapeHtml(ai.disclaimer || "보험금 지급을 확정하는 결과는 아닙니다.") +

      "</p>" +

      "</div>"

    );

  }



  function renderInlineAiPanel(data) {

    var panel = document.getElementById("customer-ai-inline");

    var body = document.getElementById("customer-ai-inline-body");

    if (!panel || !body || !data) return;

    body.innerHTML = buildInlineAiHtml(data.ai || {});

    body.querySelectorAll("[data-action=\"auto-claim-inline\"]").forEach(function (btn) {

      btn.addEventListener("click", triggerAutoClaimFromResults);

    });

    panel.hidden = false;

    if (api.state) {

      api.state.aiInlineOpened = true;

      api.saveState();

    }

    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });

  }



  function bindResultsView(data) {

    var med = data.medical || {};

    var ins = data.insurance || {};

    var ai = data.ai || {};

    if (api.resultMedicalStatus) {

      api.resultMedicalStatus.textContent = med.completed

        ? "진료내역 조회 완료"

        : "진료내역 조회 미완료";

    }

    if (api.resultInsuranceStatus) {

      api.resultInsuranceStatus.textContent = ins.completed

        ? "보험가입이력 조회 완료"

        : "보험가입이력 조회 미완료";

    }

    if (api.resultAiStatus) {

      api.resultAiStatus.textContent =

        ai.candidate_count != null ? "AI 분석 준비 완료" : "AI 분석 준비 중";

    }

  }



  function openViewSheet(kind) {

    var data = api.state.findResults;

    if (!data || !api.viewSheetBody) return;

    var title = "보기";

    var html = "";

    if (kind === "medical") {

      title = "진료내역";

      var medUrl = data.medical && data.medical.detail_url;

      if (medUrl) {

        html =

          '<iframe class="customer-view-sheet__iframe" title="진료내역 상세" src="' +

          medUrl.replace(/"/g, "&quot;") +

          '"></iframe>';

      } else {

        html = "<p>표시할 진료내역이 없습니다.</p>";

      }

    } else if (kind === "insurance") {

      title = "보험가입이력";

      var insUrl = data.insurance && data.insurance.detail_url;

      if (insUrl) {

        html =

          '<iframe class="customer-view-sheet__iframe" title="보험가입이력 상세" src="' +

          insUrl.replace(/"/g, "&quot;") +

          '"></iframe>';

      } else {

        html = "<p>표시할 보험가입이력이 없습니다.</p>";

      }

    } else if (kind === "ai") {

      renderInlineAiPanel(data);

      return;

    } else if (false && kind === "ai") {

      title = "AI 분석";

      var aiBlock = data.ai || {};

      html =

        "<div class=\"customer-ai-card\">" +

        "<p class=\"customer-ai-card__metric\">청구 검토 후보 <strong>" +

        api.escapeHtml(String(aiBlock.candidate_count || 0)) +

        "건</strong></p>" +

        "<p class=\"customer-ai-card__metric\">검토 금액 <strong>" +

        api.escapeHtml(aiBlock.estimated_display || "—") +

        "</strong></p>" +

        "<p class=\"customer-ai-card__note\">" +

        api.escapeHtml(aiBlock.disclaimer || "") +

        "</p>" +

        "<h3>우선 확인할 진료</h3><ul class=\"customer-view-list\">" +

        (aiBlock.priority_visits || [])

          .map(function (v) {

            return (

              "<li><strong>" +

              api.escapeHtml(v.visit_date) +

              "</strong> " +

              api.escapeHtml(v.hospital_name) +

              " — " +

              api.escapeHtml(v.label) +

              "</li>"

            );

          })

          .join("") +

        "</ul>" +

        "<h3>필요한 서류</h3><ul class=\"customer-view-list\">" +

        (aiBlock.documents_needed || [])

          .map(function (d) {

            return "<li>" + api.escapeHtml(d) + "</li>";

          })

          .join("") +

        "</ul>";

      if (aiBlock.detail_collapsed) {

        html +=

          "<details class=\"customer-ai-card__details\"><summary>상세 설명</summary><p>" +

          api.escapeHtml(aiBlock.detail_collapsed) +

          "</p></details>";

      }

      html += "</div>";

    }

    if (api.viewSheetTitle) api.viewSheetTitle.textContent = title;

    api.viewSheetBody.innerHTML = html;

    if (api.viewSheet) api.viewSheet.hidden = false;

    document.body.classList.add("customer-view-sheet-open");

  }



  function closeViewSheet() {

    if (api.viewSheet) api.viewSheet.hidden = true;

    document.body.classList.remove("customer-view-sheet-open");

  }



  function startFindFlow() {

    pendingAuthAction = null;

    clearLoadingUi();

    var payload = api.buildRegistrationPayload

      ? api.buildRegistrationPayload()

      : api.buildAnswersPayload();

    showProgressView("고객정보 저장 중", "saving");

    return fetch("/api/customer/find/start", {

      method: "POST",

      headers: { "Content-Type": "application/json" },

      body: JSON.stringify({ payload: payload }),

    })

      .then(function (res) {

        return parseJsonResponse(res).then(function (data) {

          if (!res.ok) data.ok = false;

          return data;

        });

      })

      .then(function (data) {

        if (!data || !data.ok) {

          showAdvanceError(

            (data && (data.message || data.error)) ||

              "시작할 수 없습니다. 입력 정보를 확인해 주세요.",

            "failed",

            "retry"

          );

          return;

        }

        api.state.flowId = data.flow_id;

        api.state.findStarted = true;

        api.state.findStatus = normalizeStatus(data);

        api.saveState();

        if (api.state.findStatus && api.state.findStatus.done) {

          return fetchResults();

        }

        applyStatus(api.state.findStatus);

      })

      .catch(function () {

        showAdvanceError(

          "네트워크 오류가 발생했습니다.",

          "failed",

          "retry"

        );

      });

  }



  if (api.authConfirmBtn) {

    api.authConfirmBtn.addEventListener("click", function () {

      var action = pendingAuthAction;

      if (!action || advanceInFlight) return;

      callAdvance(action);

    });

  }

  if (api.viewSheetClose) {

    api.viewSheetClose.addEventListener("click", closeViewSheet);

  }

  if (api.viewSheetBackdrop) {

    api.viewSheetBackdrop.addEventListener("click", closeViewSheet);

  }

  document.querySelectorAll("[data-view]").forEach(function (btn) {

    btn.addEventListener("click", function () {

      openViewSheet(btn.getAttribute("data-view"));

    });

  });



  document.querySelectorAll("[data-action=\"toggle-ai-inline\"]").forEach(function (btn) {

    btn.addEventListener("click", function () {

      var data = api.state.findResults;

      if (!data) return;

      var panel = document.getElementById("customer-ai-inline");

      if (!panel) return;

      if (panel.hidden) {

        renderInlineAiPanel(data);

        btn.textContent = "AI 분석 접기";

      } else {

        panel.hidden = true;

        btn.textContent = "AI 분석 보기";

      }

    });

  });



  var autoClaimCta = document.getElementById("customer-auto-claim-cta");

  if (autoClaimCta) {

    autoClaimCta.addEventListener("click", triggerAutoClaimFromResults);

  }



  root.RedRibbonCustomerFindUi = {

    startFindFlow: startFindFlow,

    showProgressView: showProgressView,

    showResultsView: showResultsView,

    applyStatus: applyStatus,

    callAdvance: callAdvance,

    fetchResults: fetchResults,

  };

})(typeof window !== "undefined" ? window : this);

