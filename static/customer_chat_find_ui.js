/**
 * 고객 채팅 — 지난 보험금 찾기 진행·결과 UI (customer_chat.js에서 로드)
 */
(function (root) {
  "use strict";
  var api = root.RedRibbonCustomerChatApi;
  if (!api) return;

  var PROGRESS_STEPS = [
    { key: "saving", label: "고객정보 저장 중" },
    { key: "medical_prepare", label: "진료내역 조회 준비 중" },
    { key: "auth_request", label: "본인인증 요청 중" },
    { key: "auth_waiting", label: "인증 확인 중" },
    { key: "medical_fetching", label: "진료내역 가져오는 중" },
    { key: "insurance_prepare", label: "보험가입이력 조회 준비 중" },
    { key: "insurance_fetching", label: "보험가입이력 가져오는 중" },
    { key: "ai_preparing", label: "결과 정리 중" },
    { key: "complete", label: "완료" },
  ];

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
    return order.indexOf(key) < order.indexOf(active);
  }

  function showProgressView(message, phaseKey) {
    if (api.logEl) api.logEl.hidden = true;
    if (api.footerEl) api.footerEl.hidden = true;
    if (api.progressEl) {
      api.progressEl.hidden = false;
      if (api.progressMessageEl) {
        api.progressMessageEl.textContent = message || "진행 중";
      }
      renderProgressSteps(phaseKey || "saving");
    }
    if (api.resultsEl) api.resultsEl.hidden = true;
  }

  function showResultsView() {
    if (api.progressEl) api.progressEl.hidden = true;
    if (api.resultsEl) api.resultsEl.hidden = false;
    if (api.logEl) api.logEl.hidden = true;
    if (api.footerEl) api.footerEl.hidden = true;
  }

  function pollAdvance(confirmAuth) {
    if (!api.state.flowId) return Promise.resolve();
    return fetch("/api/customer/find/advance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        flow_id: api.state.flowId,
        confirm_auth: !!confirmAuth,
      }),
    })
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (!data || !data.ok || !data.status) return data;
        var st = data.status;
        api.state.findStatus = st;
        api.saveState();
        showProgressView(st.message, st.phase);
        if (api.authConfirmBtn) {
          api.authConfirmBtn.hidden = !st.needs_auth_confirm;
        }
        if (st.failed) {
          if (api.progressMessageEl) {
            api.progressMessageEl.textContent =
              st.message || "조회 중 오류가 발생했습니다.";
          }
          return data;
        }
        if (st.done) {
          return fetchResults();
        }
        return new Promise(function (resolve) {
          setTimeout(function () {
            pollAdvance(false).then(resolve);
          }, 900);
        });
      });
  }

  function fetchResults() {
    return fetch(
      "/api/customer/find/results?flow_id=" +
        encodeURIComponent(api.state.flowId)
    )
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (data && data.ok) {
          api.state.findResults = data;
          api.state.finished = true;
          api.saveState();
          bindResultsView(data);
          showResultsView();
        }
        return data;
      });
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
      var visits = (data.medical && data.medical.visits) || [];
      if (!visits.length) {
        html = "<p>표시할 진료내역이 없습니다.</p>";
      } else {
        html =
          "<ul class=\"customer-view-list\">" +
          visits
            .map(function (v) {
              return (
                "<li><strong>" +
                api.escapeHtml(v.visit_date) +
                "</strong> " +
                api.escapeHtml(v.hospital_name) +
                "<br><span>" +
                api.escapeHtml(v.department) +
                " · " +
                api.escapeHtml(v.diagnosis) +
                "</span></li>"
              );
            })
            .join("") +
          "</ul>";
      }
    } else if (kind === "insurance") {
      title = "보험가입이력";
      var companies = (data.insurance && data.insurance.companies) || [];
      html =
        "<p>가입 상품 " +
        api.escapeHtml(String(data.insurance.product_count || 0)) +
        "건</p><ul class=\"customer-view-list\">" +
        companies
          .map(function (c) {
            return (
              "<li>" +
              api.escapeHtml(c.company_name) +
              " <span>(" +
              api.escapeHtml(String(c.product_count)) +
              "건)</span></li>"
            );
          })
          .join("") +
        "</ul>";
    } else if (kind === "ai") {
      title = "AI 분석";
      var ai = data.ai || {};
      html =
        "<div class=\"customer-ai-card\">" +
        "<p class=\"customer-ai-card__metric\">청구 검토 후보 <strong>" +
        api.escapeHtml(String(ai.candidate_count || 0)) +
        "건</strong></p>" +
        "<p class=\"customer-ai-card__metric\">검토 금액 <strong>" +
        api.escapeHtml(ai.estimated_display || "—") +
        "</strong></p>" +
        "<p class=\"customer-ai-card__note\">" +
        api.escapeHtml(ai.disclaimer || "") +
        "</p>" +
        "<h3>우선 확인할 진료</h3><ul class=\"customer-view-list\">" +
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
        "<h3>필요한 서류</h3><ul class=\"customer-view-list\">" +
        (ai.documents_needed || [])
          .map(function (d) {
            return "<li>" + api.escapeHtml(d) + "</li>";
          })
          .join("") +
        "</ul>";
      if (ai.detail_collapsed) {
        html +=
          "<details class=\"customer-ai-card__details\"><summary>상세 설명</summary><p>" +
          api.escapeHtml(ai.detail_collapsed) +
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
    var payload = api.buildAnswersPayload();
    showProgressView("고객정보 저장 중", "saving");
    return fetch("/api/customer/find/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload: payload }),
    })
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (!data || !data.ok) {
          showProgressView("시작할 수 없습니다. 입력 정보를 확인해 주세요.", "failed");
          return;
        }
        api.state.flowId = data.flow_id;
        api.state.findStarted = true;
        api.saveState();
        return pollAdvance(false);
      })
      .catch(function () {
        showProgressView("네트워크 오류가 발생했습니다.", "failed");
      });
  }

  if (api.authConfirmBtn) {
    api.authConfirmBtn.addEventListener("click", function () {
      pollAdvance(true);
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

  root.RedRibbonCustomerFindUi = {
    startFindFlow: startFindFlow,
    showProgressView: showProgressView,
    showResultsView: showResultsView,
    pollAdvance: pollAdvance,
    fetchResults: fetchResults,
  };
})(typeof window !== "undefined" ? window : this);
