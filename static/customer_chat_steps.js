/**
 * 고객용 채팅형 접수 step 정의
 * type: consent | text | tel | email | choice | yesno | summary
 */
window.RedRibbonCustomerChatSteps = [
  {
    key: "consent",
    type: "consent",
    messages: [
      "안녕하세요. 래드리본입니다.",
      "「지난 보험금 찾기」 서비스를 안내해 드립니다. 놓친 보험금·청구 가능 여부를 함께 확인할 수 있습니다.",
      "진료내역·보험가입이력 조회를 위해 개인정보 동의가 필요합니다. 동의서 전문을 확인한 뒤 선택해 주세요.",
    ],
  },
  {
    key: "name",
    type: "text",
    prompt: "고객님 성함을 입력해 주세요.",
    field: "name",
    placeholder: "이름",
    validate: function (value) {
      if (String(value || "").trim().length < 2) {
        return "이름을 2글자 이상 입력해 주세요.";
      }
      return "";
    },
  },
  {
    key: "phone",
    type: "tel",
    prompt: "휴대폰 번호를 입력해 주세요. (숫자만, 하이픈 없이)",
    field: "phone",
    placeholder: "01012345678",
    inputMode: "numeric",
    validate: function (value) {
      var d = String(value || "").replace(/\D/g, "");
      if (!/^010/.test(d)) return "010으로 시작하는 번호를 입력해 주세요.";
      if (d.length < 10 || d.length > 11) return "휴대폰 번호는 10~11자리여야 합니다.";
      return "";
    },
    formatAnswer: function (value) {
      return String(value || "").replace(/\D/g, "");
    },
    displayAnswer: function (value) {
      var m = window.RedRibbonCustomerChatMask;
      return m && m.phone ? m.phone(value) : "****";
    },
  },
  {
    key: "telecom",
    type: "choice",
    prompt: "통신사를 선택해 주세요.",
    field: "telecom",
    choices: [
      { label: "SKT", value: "SKT" },
      { label: "KT", value: "KT" },
      { label: "LG U+", value: "LG U+" },
      { label: "알뜰폰 SKT", value: "알뜰폰 SKT" },
      { label: "알뜰폰 KT", value: "알뜰폰 KT" },
      { label: "알뜰폰 LG U+", value: "알뜰폰 LG U+" },
    ],
    validate: function (value) {
      if (!String(value || "").trim()) return "통신사를 선택해 주세요.";
      return "";
    },
  },
  {
    key: "identity",
    type: "text",
    prompt: "주민등록번호 13자리를 입력해 주세요. (숫자만, 하이픈 없이)",
    field: "identity",
    placeholder: "13자리 숫자",
    inputMode: "numeric",
    maxLength: 13,
    sensitive: true,
    sensitiveLabel: "민감정보 · 주민등록번호",
    inputType: "password",
    validate: function (value) {
      var d = String(value || "").replace(/\D/g, "");
      if (d.length !== 13) return "주민등록번호 13자리 숫자를 입력해 주세요.";
      return "";
    },
    formatAnswer: function (value) {
      return String(value || "").replace(/\D/g, "").slice(0, 13);
    },
    displayAnswer: function (value) {
      var m = window.RedRibbonCustomerChatMask;
      return m && m.identity ? m.identity(value) : "******";
    },
  },
  {
    key: "email",
    type: "email",
    prompt: "안내·결과 수신용 이메일을 입력해 주세요.",
    introMessages: [
      "신용정보원 인증 과정에서 이메일 인증이 필요할 수 있습니다. 실제 수신 가능한 이메일을 입력해 주세요.",
    ],
    field: "email",
    placeholder: "example@email.com",
    inputType: "email",
    validate: function (value) {
      var v = String(value || "").trim();
      if (!v) return "이메일을 입력해 주세요.";
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) {
        return "올바른 이메일 형식을 입력해 주세요.";
      }
      return "";
    },
    formatAnswer: function (value) {
      return String(value || "").trim().toLowerCase();
    },
  },
  {
    key: "bank",
    type: "text",
    prompt: "보험금 수령 은행명을 선택하거나 직접 입력해 주세요.",
    field: "bankName",
    placeholder: "은행명 입력",
    quickRepliesFillOnly: true,
    quickReplies: [
      { label: "국민은행", value: "국민은행" },
      { label: "신한은행", value: "신한은행" },
      { label: "우리은행", value: "우리은행" },
      { label: "하나은행", value: "하나은행" },
      { label: "농협은행", value: "농협은행" },
      { label: "기업은행", value: "기업은행" },
      { label: "카카오뱅크", value: "카카오뱅크" },
      { label: "토스뱅크", value: "토스뱅크" },
    ],
    validate: function (value) {
      if (!String(value || "").trim()) return "은행명을 입력해 주세요.";
      return "";
    },
  },
  {
    key: "account",
    type: "text",
    prompt: "계좌번호를 입력해 주세요. (숫자만)",
    field: "accountNumber",
    placeholder: "계좌번호",
    inputMode: "numeric",
    sensitive: true,
    sensitiveLabel: "민감정보 · 계좌번호",
    inputType: "password",
    validate: function (value) {
      var d = String(value || "").replace(/\D/g, "");
      if (d.length < 10 || d.length > 16) return "계좌번호 자릿수를 확인해 주세요.";
      return "";
    },
    formatAnswer: function (value) {
      return String(value || "").replace(/\D/g, "");
    },
    displayAnswer: function (value) {
      var m = window.RedRibbonCustomerChatMask;
      return m && m.account ? m.account(value) : "****";
    },
  },
  {
    key: "account_holder_insured",
    type: "yesno",
    prompt: "보험금 받을 계좌의 예금주가 피보험자 본인인가요?",
    field: "accountHolderIsInsured",
    yesLabel: "예, 본인입니다",
    noLabel: "아니오",
    noFollowUp:
      "보험금 청구 접수는 가능하지만, 보험회사 심사 과정에서 본인 명의 계좌로 정정이 필요할 수 있습니다.",
  },
  {
    key: "summary",
    type: "summary",
    prompt: "입력하신 정보를 최종 확인해 주세요.",
  },
];

window.RedRibbonCustomerChatKeywords = [
  "래드리본",
  "지난 보험금 찾기",
  "진료내역",
  "보험가입이력",
  "개인정보 동의",
  "본인확인",
  "보험금 청구 준비",
];

window.RedRibbonCustomerChatMask = {
  phone: function (digits) {
    var d = String(digits || "").replace(/\D/g, "");
    if (d.length === 11) return d.slice(0, 3) + "-****-" + d.slice(-4);
    if (d.length >= 10) return d.slice(0, 3) + "-****-" + d.slice(-3);
    return "****";
  },
  identity: function (digits) {
    var d = String(digits || "").replace(/\D/g, "");
    if (d.length >= 6) return d.slice(0, 6) + "-*******";
    return "*******";
  },
  rrn: function (front, back) {
    var id = String(front || "") + String(back || "");
    return window.RedRibbonCustomerChatMask.identity(id);
  },
  account: function (num) {
    var d = String(num || "").replace(/\D/g, "");
    if (!d) return "****";
    return "****" + d.slice(-4);
  },
};
