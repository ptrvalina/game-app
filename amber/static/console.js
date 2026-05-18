/**
 * Amber enterprise compliance workspace.
 * Rendering stays deterministic-first, text-only, and privacy-safe.
 */
(function () {
  const SAMPLE = {
    mode: "fiat",
    jurisdiction: "BY",
    alert_id: "DEMO-2026-001",
    client_profile: { declared_monthly_income: 1000, declared_occupation: "Инженер" },
    historical_transactions: [
      { id: "hist-1", ts: "2026-04-01T10:00:00", amount: 1200, direction: "in", counterparty: "ООО Ромашка", asset_type: "fiat" },
      { id: "hist-2", ts: "2026-04-15T14:00:00", amount: 800, direction: "out", counterparty: "ИП Иванов", asset_type: "fiat" },
    ],
    focus_transactions: [
      {
        id: "focus-1",
        ts: "2026-05-10T23:15:00",
        amount: 5800,
        direction: "in",
        counterparty: "ООО Технопром",
        channel: "cash",
        asset_type: "fiat",
        narrative: "Внесение наличных",
      },
      {
        id: "focus-2",
        ts: "2026-05-11T09:05:00",
        amount: 5900,
        direction: "in",
        counterparty: "ООО Технопром",
        channel: "cash",
        asset_type: "fiat",
        narrative: "Внесение наличных",
      },
    ],
    aml_system_flags: ["velocity_alert"],
  };

  const DEMO_CASES = [
    { id: "fiat-structuring", title: "Фиатное дробление", mode: "fiat", jurisdiction: "EU", file: "/demo/fiat_structuring.csv", description: "Повторяющиеся близкие суммы ниже пороговых диапазонов с explainable transaction evidence." },
    { id: "crypto-layering", title: "Крипто-layering", mode: "crypto", jurisdiction: "EU", file: "/demo/crypto_layering.csv", description: "Крипто-маршрутизация через exchange-like контрагентов с детерминированной связностью." },
    { id: "cross-transition", title: "Переход fiat-to-crypto", mode: "cross", jurisdiction: "EU", file: "/demo/cross_border_case.csv", description: "Сжатый временной кластер между фиатным входом и крипто-выводом." },
    { id: "dormant-reactivation", title: "Реактивация спящего профиля", mode: "fiat", jurisdiction: "BY", file: "/demo/dormant_reactivation.csv", description: "Длительная пауза и затем резкое восстановление активности." },
    { id: "salary-mismatch", title: "Несоответствие профилю дохода", mode: "fiat", jurisdiction: "BY", file: "/demo/salary_mismatch.csv", description: "Наблюдаемые входящие потоки не совпадают с заявленным доходным профилем." },
    { id: "exchange-hopping", title: "Exchange hopping", mode: "crypto", jurisdiction: "EU", file: "/demo/exchange_hopping.csv", description: "Несколько exchange-like контрагентов за короткий интервал наблюдения." },
  ];

  const STORAGE_KEY = "amber_console_api_key";
  const SORT_DEFAULT = { key: "contribution", dir: "desc" };
  const SORTABLE_COLUMNS = ["timestamp", "counterparty", "direction", "amount", "typology", "contribution", "source", "rule"];
  const EVIDENCE_PAGE_SIZE = 50;
  const WIZARD_STEPS = ["upload", "preview", "mapping", "normalize", "analyze"];
  const workspaceRight = document.querySelector(".workspace-right");

  const $ = (id) => document.getElementById(id);
  const loading = $("loading");
  const payloadEl = $("payload");
  const apiKeyEl = $("apiKey");
  const errorBox = $("errorBox");
  const emptyHint = $("emptyHint");
  const scorePill = $("scorePill");
  const severityPill = $("severityPill");
  const chips = $("chips");
  const reviewBanner = $("reviewBanner");
  const demoBanner = $("demoBanner");
  const demoModeBadge = $("demoModeBadge");
  const copySarStatus = $("copySarStatus");
  const bundleStatus = $("bundleStatus");
  const pilotClientCopyStatus = $("pilotClientCopyStatus");
  const replaySummary = $("replaySummary");
  const telemetryPanel = $("telemetryPanel");
  const diagnosticReplay = $("diagnosticReplay");
  const caseReviewRequired = $("caseReviewRequired");
  const caseReplayBadge = $("caseReplayBadge");
  const caseSafeModeBadge = $("caseSafeModeBadge");
  const evidenceFilterEl = $("evidenceFilter");
  const evidenceGroupEl = $("evidenceGroup");

  const mappingInputs = {
    timestamp: $("mapTimestamp"),
    amount: $("mapAmount"),
    currency: $("mapCurrency"),
    direction: $("mapDirection"),
    counterparty: $("mapCounterparty"),
    channel: $("mapChannel"),
    narrative: $("mapNarrative"),
    asset_type: $("mapAssetType"),
  };

  apiKeyEl.value = sessionStorage.getItem(STORAGE_KEY) || "";
  apiKeyEl.addEventListener("change", () => {
    sessionStorage.setItem(STORAGE_KEY, apiKeyEl.value.trim());
    renderSessionStatus();
  });
  apiKeyEl.addEventListener("input", renderSessionStatus);
  payloadEl.value = JSON.stringify(SAMPLE, null, 2);

  let lastResponse = null;
  let lastReplay = null;
  let lastIngest = null;
  let lastSourceRequest = SAMPLE;
  let selectedEvidenceKey = null;
  let evidenceSort = { key: SORT_DEFAULT.key, dir: SORT_DEFAULT.dir };
  let evidencePage = 0;
  let environmentState = { ready: null, telemetry: null };

  function setLoading(on) {
    loading.classList.toggle("show", on);
    loading.setAttribute("aria-hidden", on ? "false" : "true");
    ["btnSend", "btnCsvPreview", "btnAnalyzePreview", "btnExportBundle", "btnReplayBundle", "btnEvidenceCsv"].forEach((id) => {
      const el = $(id);
      if (el) el.disabled = on;
    });
  }

  function showError(msg) {
    errorBox.textContent = msg || "";
  }

  function uuid() {
    return crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
  }

  const QUEUE_STORAGE_KEY = "amber_case_queue_v1";

  function authHeaders() {
    const headers = { "X-Request-ID": uuid() };
    const key = apiKeyEl.value.trim();
    if (key) headers["X-Api-Key"] = key;
    const roleEl = $("amberRole");
    if (roleEl && roleEl.value) headers["X-Amber-Role"] = roleEl.value;
    return headers;
  }

  function loadQueue() {
    try {
      return JSON.parse(sessionStorage.getItem(QUEUE_STORAGE_KEY) || "[]");
    } catch {
      return [];
    }
  }

  function saveQueue(items) {
    sessionStorage.setItem(QUEUE_STORAGE_KEY, JSON.stringify(items));
  }

  function upsertQueueCase(data, sourceRequest) {
    if (!data || !data.meta || !data.meta.workflow) return;
    const wf = data.meta.workflow;
    const entry = {
      case_id: wf.case_id,
      review_status: wf.review_status,
      severity: wf.severity,
      queue_priority: wf.queue_priority,
      assigned_to: wf.assigned_to,
      updated_at: wf.updated_at,
      mode: wf.mode,
      jurisdiction: wf.jurisdiction,
      score: data.anomaly ? data.anomaly.anomaly_score : null,
    };
    const queue = loadQueue().filter((item) => item.case_id !== entry.case_id);
    queue.unshift(entry);
    saveQueue(queue.slice(0, 200));
    renderQueuePanel();
  }

  async function refreshQueueCounters() {
    const host = $("queueCounters");
    if (!host) return;
    try {
      const res = await fetch("/api/v1/case/queue/summary", {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
        body: JSON.stringify({ cases: loadQueue() }),
      });
      const json = await parseJsonResponse(res);
      host.textContent = Object.entries(json)
        .map(([k, v]) => `${k}: ${v}`)
        .join(" · ");
    } catch {
      host.textContent = "Queue counters unavailable";
    }
  }

  function renderQueuePanel() {
    const list = $("caseQueueList");
    if (!list) return;
    clearNode(list);
    const filter = ($("queueFilter") && $("queueFilter").value) || "all";
    const items = loadQueue().filter((item) => filter === "all" || item.review_status === filter);
    if (!items.length) {
      list.textContent = "Очередь пуста. Запустите анализ или демо-кейс.";
      refreshQueueCounters();
      return;
    }
    items.forEach((item) => {
      const card = makeEl("div", "demo-card queue-card");
      card.appendChild(makeEl("strong", "", `${item.case_id} · ${item.review_status}`));
      card.appendChild(
        makeEl(
          "p",
          "hint",
          `severity=${item.severity} · priority=${item.queue_priority} · score=${item.score != null ? item.score : "—"}`
        )
      );
      card.appendChild(makeEl("p", "hint", `assigned=${item.assigned_to || "—"} · ${formatDateTime(item.updated_at)}`));
      list.appendChild(card);
    });
    refreshQueueCounters();
  }

  function renderGovernancePanels(data) {
    const gov = $("governanceSummary");
    const audit = $("auditTimeline");
    const exports = $("exportHistory");
    const lifecycle = $("lifecycleTimeline");
    if (!data || !data.meta) return;
    if (gov) {
      gov.textContent = data.meta.governance
        ? fmt(data.meta.governance)
        : "Governance metadata will appear after analyze.";
    }
    if (audit) {
      audit.textContent = (data.meta.audit_events || []).length
        ? (data.meta.audit_events || []).map((e) => `${e.sequence}. ${e.event_type} · ${e.event_hash.slice(0, 8)}`).join("\n")
        : "Audit stream empty.";
    }
    if (exports) {
      exports.textContent = (data.meta.export_access_log || []).length
        ? (data.meta.export_access_log || []).map((e) => `${e.export_type} · ${e.actor_id || "—"} · ${e.occurred_at}`).join("\n")
        : "Export access log empty.";
    }
    if (lifecycle) {
      clearNode(lifecycle);
      (data.meta.lifecycle_events || []).forEach((ev) => {
        const row = makeEl("div", "tx-timeline-item");
        row.appendChild(makeEl("div", "tx-timeline-ts", formatDateTime(ev.occurred_at)));
        row.appendChild(makeEl("div", "tx-timeline-body", `${ev.event} · ${ev.actor_id || "—"} · ${ev.note || ""}`));
        lifecycle.appendChild(row);
      });
    }
  }

  function activateWorkspaceTab(name) {
    document.querySelectorAll(".workspace-tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.wtab === name);
    });
    document.querySelectorAll(".workspace-tab-panel[data-wpanel]").forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.wpanel === name);
    });
  }

  document.querySelectorAll(".workspace-tab").forEach((btn) => {
    btn.addEventListener("click", () => activateWorkspaceTab(btn.dataset.wtab));
  });

  function activateTab(name) {
    const root = workspaceRight || document;
    root.querySelectorAll(".tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === name);
    });
    root.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.panel === name);
    });
  }

  if (workspaceRight) {
    workspaceRight.querySelectorAll(".tab").forEach((btn) => {
      btn.addEventListener("click", () => activateTab(btn.dataset.tab));
    });
  }

  function makeEl(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text != null) el.textContent = String(text);
    return el;
  }

  function clearNode(node) {
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function maskText(value) {
    if (value == null) return value;
    return String(value)
      .replace(/\b([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b/g, (_, name, host) => `${name.slice(0, 1)}***${name.slice(-1)}@${host}`)
      .replace(/(?<!\w)(\+?\d[\d\-\s()]{7,}\d)(?!\w)/g, (m) => (m.length > 6 ? `${m.slice(0, 2)}***${m.slice(-2)}` : "***"))
      .replace(/\b\d{10,20}\b/g, (m) => `${m.slice(0, 2)}***${m.slice(-2)}`)
      .replace(/\b(?:0x[a-fA-F0-9]{10,}|[13][a-km-zA-HJ-NP-Z1-9]{10,}|[A-Za-z0-9]{18,64})\b/g, (m) => (m.length > 8 ? `${m.slice(0, 4)}***${m.slice(-4)}` : "***"));
  }

  function maskDeep(value) {
    if (Array.isArray(value)) return value.map(maskDeep);
    if (value && typeof value === "object") {
      const out = {};
      Object.keys(value).forEach((key) => {
        out[key] = maskDeep(value[key]);
      });
      return out;
    }
    if (typeof value === "string") return maskText(value);
    return value;
  }

  function fmt(value) {
    return JSON.stringify(maskDeep(value), null, 2);
  }

  function parsePayload() {
    return JSON.parse(payloadEl.value);
  }

  function collectOverrides() {
    const overrides = {};
    Object.keys(mappingInputs).forEach((key) => {
      const value = mappingInputs[key].value.trim();
      if (value) overrides[key] = value;
    });
    return overrides;
  }

  function formatDateTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("ru-RU", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatAmount(value) {
    if (value == null || value === "") return "—";
    if (typeof value === "number") {
      return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(value);
    }
    const asNumber = Number(value);
    return Number.isFinite(asNumber) ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(asNumber) : String(value);
  }

  function severityClass(sev) {
    if (sev === "critical") return "sev-critical";
    if (sev === "high") return "sev-high";
    if (sev === "medium") return "sev-medium";
    return "sev-low";
  }

  function severityLabel(sev) {
    if (sev === "critical") return "ESCALATE";
    if (sev === "high") return "HIGH";
    if (sev === "medium") return "MEDIUM";
    return "LOW";
  }

  function narrativeMode(meta) {
    if (!meta) return "Standard narrative mode";
    if (meta.emergency_mode) return "Controlled Safe Mode";
    if (meta.degraded_mode) return "Restricted Narrative Mode";
    return "Standard narrative mode";
  }

  function reliabilityText(data) {
    const validation = data && data.meta ? data.meta.confidence_validation : null;
    if (validation) return `${validation.effective_score}/100`;
    if (data && data.anomaly && data.anomaly.confidence_score != null) return `${data.anomaly.confidence_score}/100`;
    return "—";
  }

  function safeModeText(meta) {
    if (!meta) return "Детерминированный стандартный режим активен.";
    if (meta.emergency_mode) {
      return "Controlled Safe Mode активирован: сервис сохранил детерминированную обработку и безопасный fallback narrative вместо отказа.";
    }
    if (meta.degraded_mode) {
      return "Restricted Narrative Mode активирован: часть narrative-возможностей ограничена, но детерминированное evidence остаётся доступным.";
    }
    return meta.operating_reason || "Стандартный режим active: детерминированный анализ и policy-validated narrative доступны.";
  }

  function applyReviewState(payload) {
    if (!payload || !payload.meta) return payload;
    payload.meta.review_status = $("reviewStatus").value;
    payload.meta.review_notes = $("reviewNotes").value.trim() || null;
    payload.meta.reviewed_by = $("reviewedBy").value.trim() || null;
    payload.meta.reviewed_at = payload.meta.reviewed_by || payload.meta.review_notes ? new Date().toISOString() : null;
    return payload;
  }

  function setReviewControls(meta) {
    $("reviewStatus").value = meta.review_status || "pending";
    $("reviewNotes").value = meta.review_notes || "";
    $("reviewedBy").value = meta.reviewed_by || "";
  }

  async function parseJsonResponse(res) {
    const text = await res.text();
    let json;
    try {
      json = JSON.parse(text);
    } catch {
      throw new Error("Ответ не JSON: " + text.slice(0, 200));
    }
    if (!res.ok) {
      const msg = json.error?.message || json.detail || res.statusText;
      throw new Error(res.status + ": " + msg);
    }
    return json;
  }

  async function refreshOperationalPanels() {
    try {
      const [readyRes, telemetryRes] = await Promise.all([
        fetch("/ready", { headers: authHeaders() }),
        fetch("/telemetry", { headers: authHeaders() }),
      ]);
      environmentState.ready = await parseJsonResponse(readyRes);
      environmentState.telemetry = await parseJsonResponse(telemetryRes);
      renderEnvironmentStatus();
    } catch {
      // Keep workspace usable even if operational endpoints are unavailable.
    }
  }

  function setWizardStep(step) {
    const idx = WIZARD_STEPS.indexOf(step);
    document.querySelectorAll("#wizardSteps .wizard-step").forEach((el) => {
      const stepIdx = WIZARD_STEPS.indexOf(el.dataset.step);
      el.classList.toggle("active", el.dataset.step === step);
      el.classList.toggle("done", stepIdx >= 0 && stepIdx < idx);
    });
  }

  function renderSessionStatus() {
    const dot = $("sessionDot");
    const status = $("sessionStatus");
    const mask = $("sessionKeyMask");
    const key = apiKeyEl.value.trim();
    const ready = environmentState.ready;
    if (mask) mask.textContent = key ? `${key.slice(0, 4)}••••${key.slice(-2)}` : "not set";
    if (!ready) {
      if (dot) dot.className = "session-dot";
      if (status) status.textContent = "offline";
      return;
    }
    if (dot) dot.className = ready.status === "ready" ? "session-dot ok" : "session-dot warn";
    if (status) status.textContent = ready.status || "unknown";
  }

  function renderNormalizationViz(data) {
    const host = $("normalizationViz");
    if (!host) return;
    clearNode(host);
    const report = (data && data.normalization_report) || {};
    const metrics = [
      ["Encoding", report.encoding || "—"],
      ["Delimiter", report.delimiter || "—"],
      ["Decimal comma", report.decimal_comma ? "yes" : "no"],
      ["Malformed ratio", report.malformed_ratio != null ? report.malformed_ratio : "—"],
      ["Rejected rows", report.rejected_rows != null ? report.rejected_rows : "—"],
      ["Parsed rows", report.parsed_rows != null ? report.parsed_rows : "—"],
    ];
    metrics.forEach(([label, value]) => {
      const tile = makeEl("div", "norm-metric");
      tile.appendChild(makeEl("span", "norm-label", label));
      tile.appendChild(makeEl("strong", "", value));
      host.appendChild(tile);
    });
  }

  function renderReliabilityRing(data) {
    const ring = $("reliabilityRing");
    if (!ring) return;
    const validation = data.meta && data.meta.confidence_validation;
    const score = validation ? validation.effective_score : data.anomaly.confidence_score;
    const pct = score != null ? Math.max(0, Math.min(100, Number(score))) : 0;
    ring.style.setProperty("--pct", pct);
    ring.textContent = score != null ? `${score}` : "—";
  }

  function renderAnalystRecommendation(data) {
    const host = $("analystRecommendation");
    if (!host) return;
    const actions = data.reporter.recommended_actions || [];
    host.textContent = [
      "Recommended next action (requires analyst validation):",
      actions.length ? actions.map((item) => "- " + maskText(item)).join("\n") : "- Continue supervised review of deterministic evidence.",
      "",
      data.meta.human_review_required ? "Manual review requirement: yes" : "Manual review requirement: optional",
      data.meta.escalation_recommended ? "Escalation assessment: suggested for analyst review" : "Escalation assessment: not auto-suggested",
    ].join("\n");
  }

  function renderOverviewSafeMode(data) {
    const host = $("overviewSafeMode");
    if (!host) return;
    host.textContent = [
      safeModeText(data.meta),
      "",
      data.meta.emergency_mode ? "Degraded/emergency: active" : "Degraded/emergency: inactive",
      data.meta.fallback_used ? "Fallback narrative: active" : "Fallback narrative: inactive",
      "Replay-safe deterministic scoring: preserved",
      "Deterministic-only evidence: visible",
    ].join("\n");
  }

  function renderTypologyTable(data) {
    const host = $("typologyTable");
    if (!host) return;
    clearNode(host);
    const evidence = (data.anomaly && data.anomaly.evidence) || [];
    if (!evidence.length) {
      host.textContent = "Typology triggers отсутствуют.";
      return;
    }
    const table = document.createElement("table");
    table.className = "ev-grid";
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    ["typology", "severity band", "score contribution", "deterministic rule", "evidence refs"].forEach((label) => {
      headerRow.appendChild(makeEl("th", "", label));
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    evidence.forEach((item) => {
      const tr = document.createElement("tr");
      tr.appendChild(makeEl("td", "", item.category || "—"));
      tr.appendChild(makeEl("td", "", severityLabel(data.anomaly.severity || "low")));
      tr.appendChild(makeEl("td", "", item.contribution != null ? item.contribution : "—"));
      tr.appendChild(makeEl("td", "", item.code || "—"));
      tr.appendChild(makeEl("td", "", (item.tx_refs || []).join(", ") || "profile"));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    host.appendChild(table);
  }

  function renderTxTimeline(data, row) {
    const host = $("txTimeline");
    if (!host) return;
    clearNode(host);
    const txs = allTransactions()
      .filter((tx) => tx && tx.ts)
      .sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts));
    if (!txs.length) {
      host.textContent = "Нет транзакций для timeline.";
      return;
    }
    const highlight = new Set((row && row.evidence && row.evidence.tx_refs) || []);
    txs.forEach((tx) => {
      const key = tx.id || "";
      const item = makeEl("div", "tx-timeline-item" + (highlight.has(key) ? " tx-timeline-item-active" : ""));
      item.appendChild(makeEl("div", "tx-timeline-ts", formatDateTime(tx.ts)));
      item.appendChild(
        makeEl(
          "div",
          "tx-timeline-body",
          `${tx.direction || "—"} · ${formatAmount(tx.amount)} · ${maskText(tx.counterparty || "—")} · ${tx.asset_type || "—"}`
        )
      );
      host.appendChild(item);
    });
  }

  function renderEvidencePagination(totalRows) {
    const host = $("evidencePagination");
    if (!host) return;
    clearNode(host);
    const pages = Math.max(1, Math.ceil(totalRows / EVIDENCE_PAGE_SIZE));
    if (totalRows <= EVIDENCE_PAGE_SIZE) {
      host.hidden = true;
      return;
    }
    host.hidden = false;
    const info = makeEl("span", "pagination-info", `Строки ${evidencePage * EVIDENCE_PAGE_SIZE + 1}–${Math.min(totalRows, (evidencePage + 1) * EVIDENCE_PAGE_SIZE)} из ${totalRows}`);
    const prev = makeEl("button", "btn btn-small", "Назад");
    prev.type = "button";
    prev.disabled = evidencePage <= 0;
    prev.addEventListener("click", () => {
      evidencePage -= 1;
      if (lastResponse) renderEvidence(lastResponse);
    });
    const next = makeEl("button", "btn btn-small", "Вперёд");
    next.type = "button";
    next.disabled = evidencePage >= pages - 1;
    next.addEventListener("click", () => {
      evidencePage += 1;
      if (lastResponse) renderEvidence(lastResponse);
    });
    host.append(info, prev, next);
  }

  function cloneReplayStatusCards(sourceHost, targetHost, data) {
    if (!sourceHost || !targetHost) return;
    clearNode(targetHost);
    sourceHost.querySelectorAll(".status-item").forEach((item) => {
      targetHost.appendChild(item.cloneNode(true));
    });
    if (!targetHost.childElementCount) {
      targetHost.textContent = data
        ? "Replay diagnostics доступны после проверки bundle."
        : "Загрузите signed bundle для deterministic replay verification.";
    }
  }

  function saveReviewDraft(closeCase) {
    if (!lastResponse) {
      showError("Нет активного кейса для сохранения review.");
      return;
    }
    applyReviewState(lastResponse);
    const statusEl = $("reviewDraftStatus");
    if (closeCase) {
      $("reviewStatus").value = $("reviewStatus").value === "pending" ? "analyst_confirmed" : $("reviewStatus").value;
      lastResponse.meta.review_status = $("reviewStatus").value;
    }
    setReviewControls(lastResponse.meta);
    setHeaderText("caseReviewStatus", lastResponse.meta.review_status || "pending");
    setHeaderText("caseAnalyst", lastResponse.meta.reviewed_by || "—");
    setHeaderText("caseUpdatedAt", formatDateTime(lastResponse.meta.reviewed_at || Date.now()));
    if (statusEl) {
      statusEl.textContent = closeCase
        ? "Кейс закрыт для review (локально). Экспортируйте bundle для audit trail."
        : "Черновик review сохранён локально. Экспортируйте bundle для фиксации в audit chain.";
    }
    bundleStatus.textContent = "Review state обновлён. Используйте ZIP export для audit preservation.";
  }

  function renderEnvironmentStatus() {
    const ready = environmentState.ready;
    const telemetry = environmentState.telemetry;
    renderSessionStatus();
    if (ready && ready.demo_mode) {
      demoBanner.hidden = false;
      demoModeBadge.hidden = false;
    }
    const snapshot = {
      readiness: ready
        ? {
            status: ready.status,
            demo_mode: ready.demo_mode,
            api_key_required: ready.api_key_required,
            runtime_guard: ready.runtime_guard || {},
            llm: ready.llm || {},
          }
        : null,
      telemetry: telemetry ? telemetry.telemetry || {} : null,
      runtime_guard: telemetry ? telemetry.runtime_guard || {} : null,
    };
    telemetryPanel.textContent = fmt(snapshot);
  }

  function renderDemoLibrary() {
    const host = $("demoLibrary");
    clearNode(host);
    DEMO_CASES.forEach((item) => {
      const card = makeEl("div", "demo-card");
      card.appendChild(makeEl("strong", "", item.title));
      card.appendChild(makeEl("p", "hint", item.description));
      const buttonRow = makeEl("div", "btn-row");
      const previewBtn = makeEl("button", "btn btn-small", "Открыть демо");
      previewBtn.type = "button";
      previewBtn.addEventListener("click", () => previewDemoCase(item));
      const analyzeBtn = makeEl("button", "btn btn-small", "Анализировать демо");
      analyzeBtn.type = "button";
      analyzeBtn.addEventListener("click", async () => {
        await previewDemoCase(item);
        await analyzePreview();
      });
      const link = makeEl("a", "", "Скачать CSV");
      link.href = item.file;
      link.setAttribute("download", "");
      buttonRow.append(previewBtn, analyzeBtn, link);
      card.appendChild(buttonRow);
      host.appendChild(card);
    });
  }

  async function previewDemoCase(item) {
    $("csvMode").value = item.mode;
    $("csvJurisdiction").value = item.jurisdiction;
    const response = await fetch(item.file, { headers: authHeaders() });
    const blob = await response.blob();
    await requestCsvPreview({ blob, filename: item.file.split("/").pop() || `${item.id}.csv` });
  }

  async function requestCsvPreview(source) {
    showError("");
    bundleStatus.textContent = "";
    const fd = new FormData();
    fd.append("file", source.blob, source.filename);
    fd.append("mode", $("csvMode").value);
    fd.append("jurisdiction", $("csvJurisdiction").value);
    fd.append("focus_last_n", String(parseInt($("csvFocusN").value, 10) || 12));
    const overrides = collectOverrides();
    if (Object.keys(overrides).length) fd.append("column_overrides_json", JSON.stringify(overrides));
    setLoading(true);
    try {
      const res = await fetch("/api/v1/ingest/csv", { method: "POST", headers: authHeaders(), body: fd });
      const json = await parseJsonResponse(res);
      lastIngest = json;
      lastSourceRequest = json.normalized_request;
      payloadEl.value = JSON.stringify(json.normalized_request, null, 2);
      renderCsvPreview(json);
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  function renderCsvPreview(data) {
    $("csvReport").textContent = fmt({ summary: data.summary, normalization_report: data.normalization_report });
    $("csvColumns").textContent = fmt({ available_columns: data.available_columns, detected_mapping: data.normalization_report?.column_mapping || {} });
    renderNormalizationViz(data);
    renderPreviewTable($("csvPreviewTable"), data.preview_rows.filter((row) => row.status === "parsed"));
    renderPreviewTable($("csvMalformedTable"), data.preview_rows.filter((row) => row.status === "rejected"));
    const hasMapping = Object.values(mappingInputs).some((input) => input && input.value.trim());
    setWizardStep(hasMapping ? "mapping" : data.preview_rows && data.preview_rows.length ? "preview" : "upload");
    if (data.normalization_report) setWizardStep("normalize");
  }

  function renderPreviewTable(host, rows) {
    clearNode(host);
    if (!rows.length) {
      host.textContent = "Нет строк для отображения.";
      return;
    }
    const table = document.createElement("table");
    table.className = "ev-grid";
    const keys = Object.keys(rows[0].values || {});
    const thead = document.createElement("thead");
    const htr = document.createElement("tr");
    ["строка", "статус"].concat(keys).concat(["ошибка"]).forEach((key) => htr.appendChild(makeEl("th", "", key)));
    thead.appendChild(htr);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      if (row.status === "rejected") tr.className = "row-rejected";
      tr.appendChild(makeEl("td", "", row.row_number));
      tr.appendChild(makeEl("td", "", row.status));
      keys.forEach((key) => tr.appendChild(makeEl("td", "", row.values[key] != null ? row.values[key] : "—")));
      tr.appendChild(makeEl("td", "", row.issue_message || "—"));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    host.appendChild(table);
  }

  async function previewCsvFromInput() {
    const fileInput = $("csvFile");
    if (!fileInput.files || !fileInput.files[0]) {
      showError("Сначала выберите CSV-файл.");
      return;
    }
    await requestCsvPreview({ blob: fileInput.files[0], filename: fileInput.files[0].name });
  }

  async function previewXlsxFromInput() {
    const fileInput = $("xlsxFile");
    if (!fileInput || !fileInput.files || !fileInput.files[0]) {
      showError("Сначала выберите XLSX-файл.");
      return;
    }
    showError("");
    const fd = new FormData();
    fd.append("file", fileInput.files[0]);
    fd.append("mode", $("csvMode").value);
    fd.append("jurisdiction", $("csvJurisdiction").value);
    fd.append("focus_last_n", String(parseInt($("csvFocusN").value, 10) || 12));
    const overrides = collectOverrides();
    if (Object.keys(overrides).length) fd.append("column_overrides_json", JSON.stringify(overrides));
    setLoading(true);
    try {
      const res = await fetch("/api/v1/ingest/xlsx", { method: "POST", headers: authHeaders(), body: fd });
      const json = await parseJsonResponse(res);
      lastIngest = json;
      lastSourceRequest = json.normalized_request;
      payloadEl.value = JSON.stringify(json.normalized_request, null, 2);
      renderCsvPreview(json);
      setWizardStep("preview");
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  async function applyWorkflow(action, options) {
    if (!lastResponse || !lastSourceRequest) {
      showError("Нет активного кейса для workflow.");
      return null;
    }
    const body = {
      source_request: lastSourceRequest,
      analysis: applyReviewState(JSON.parse(JSON.stringify(lastResponse))),
      action,
      actor_id: $("reviewedBy").value.trim() || "analyst@workspace",
      actor_role: ($("amberRole") && $("amberRole").value) || "analyst",
      assignee: options && options.assignee,
      review_status: options && options.review_status,
      disposition_code: options && options.disposition_code,
      escalation_reason: options && options.escalation_reason,
      review_notes: $("reviewNotes").value.trim() || null,
    };
    const res = await fetch("/api/v1/case/workflow", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify(body),
    });
    const json = await parseJsonResponse(res);
    lastResponse = json;
    setReviewControls(json.meta || {});
    renderCaseHeaderForAnalyze(json);
    renderGovernancePanels(json);
    upsertQueueCase(json, lastSourceRequest);
    return json;
  }

  async function analyzePreview() {
    if (!lastIngest || !lastIngest.normalized_request) {
      showError("Сначала выполните предпросмотр CSV.");
      return;
    }
    await analyzeRequest(lastIngest.normalized_request);
  }

  async function analyzeRequest(body) {
    showError("");
    setLoading(true);
    try {
      lastSourceRequest = body;
      const res = await fetch("/api/v1/analyze", {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
        body: JSON.stringify(body),
      });
      const json = await parseJsonResponse(res);
      renderAnalyzeResult(json);
      await refreshOperationalPanels();
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  async function sendJson() {
    try {
      const body = parsePayload();
      await analyzeRequest(body);
    } catch (err) {
      showError("Некорректный JSON: " + err.message);
    }
  }

  function setHeaderText(id, text) {
    const el = $(id);
    if (el) el.textContent = text;
  }

  function renderChips(data) {
    clearNode(chips);
    const items = [
      { className: "chip", text: "режим: " + data.mode },
      { className: "chip", text: "юрисдикция: " + data.jurisdiction },
      { className: "chip", text: "narrative provider: " + (data.meta.llm_used || "none") },
    ];
    if (data.meta.fallback_used) items.push({ className: "chip chip-warn", text: "fallback narrative" });
    if (data.meta.degraded_mode) items.push({ className: "chip chip-warn", text: "restricted narrative mode" });
    if (data.meta.emergency_mode) items.push({ className: "chip chip-danger", text: "controlled safe mode" });
    if (data.meta.operating_reason) items.push({ className: "chip", text: "причина: " + data.meta.operating_reason });
    items.forEach((item) => chips.appendChild(makeEl("span", item.className, item.text)));
  }

  function renderCaseHeaderForAnalyze(data) {
    const meta = data.meta || {};
    setHeaderText("caseTitle", data.alert_id ? `Кейс ${data.alert_id}` : `Расследование ${meta.request_id || "без идентификатора"}`);
    setHeaderText("caseSubtitle", "Детерминированные индикаторы повышенного риска и внутренний compliance memo для supervised review. Финальные выводы подтверждаются аналитиком.");
    setHeaderText("caseId", data.alert_id || meta.request_id || "—");
    setHeaderText("caseReviewStatus", meta.review_status || "pending");
    setHeaderText("caseJurisdiction", data.jurisdiction || "—");
    setHeaderText("caseMode", data.mode || "—");
    setHeaderText("caseAnalyst", meta.reviewed_by || "назначить аналитика");
    setHeaderText("caseUpdatedAt", formatDateTime(meta.reviewed_at || Date.now()));
    setHeaderText("caseReliability", reliabilityText(data));
    setHeaderText("caseValidatorStatus", meta.validator_status || "not_run");
    caseReviewRequired.hidden = !meta.human_review_required;
    caseReviewRequired.textContent = meta.human_review_required ? "review required" : "review optional";
    caseReplayBadge.hidden = false;
    caseReplayBadge.textContent = "replay available after bundle export";
    caseSafeModeBadge.hidden = !meta.emergency_mode && !meta.degraded_mode;
    caseSafeModeBadge.textContent = meta.emergency_mode ? "deterministic fallback active" : "restricted narrative mode";
    const detBadge = $("caseDeterministicBadge");
    const llmBadge = $("caseLlmBadge");
    if (detBadge) detBadge.textContent = "deterministic-first";
    if (llmBadge) llmBadge.textContent = meta.llm_used ? `narrative: ${meta.llm_used}` : "narrative: policy-validated";
  }

  function renderCaseHeaderForReplay(data) {
    setHeaderText("caseTitle", data.request_id ? `Replay verification ${data.request_id}` : "Replay verification");
    setHeaderText("caseSubtitle", "Replay подтверждает целостность bundle, подписи и детерминированного scoring без обращения к LLM.");
    setHeaderText("caseId", data.request_id || "—");
    setHeaderText("caseReviewStatus", "replay only");
    setHeaderText("caseJurisdiction", "—");
    setHeaderText("caseMode", "replay");
    setHeaderText("caseAnalyst", "—");
    setHeaderText("caseUpdatedAt", formatDateTime(Date.now()));
    setHeaderText("caseReliability", data.replayed_anomaly ? `${data.replayed_anomaly.confidence_score}/100` : "—");
    setHeaderText("caseValidatorStatus", data.validator_summary?.status || "not_run");
    caseReviewRequired.hidden = false;
    caseReviewRequired.textContent = "integrity review";
    caseReplayBadge.hidden = false;
    caseReplayBadge.textContent = data.drift_detected ? "drift detected" : "replay verified";
    caseSafeModeBadge.hidden = true;
  }

  function renderRiskHeader(score, severity) {
    if (score == null) {
      scorePill.hidden = true;
      severityPill.hidden = true;
      return;
    }
    scorePill.hidden = false;
    scorePill.textContent = `Risk Score: ${score}/100`;
    severityPill.hidden = false;
    severityPill.textContent = severityLabel(severity || "low");
    severityPill.className = "pill-severity " + severityClass(severity || "low");
  }

  function renderMetaGrid(data) {
    const host = $("overviewMeta");
    clearNode(host);
    const meta = data.meta || {};
    const validation = meta.confidence_validation || {};
    const items = [
      ["Risk Score", `${data.anomaly.anomaly_score}/100`],
      ["Severity band", severityLabel(data.anomaly.severity || "low")],
      ["Evidence count", (data.anomaly.evidence || []).length],
      ["Evidence reliability", reliabilityText(data)],
      ["Narrative mode", narrativeMode(meta)],
      ["Validator summary", meta.validator_summary?.status || meta.validator_status || "not_run"],
      ["Latency (ms)", [meta.latency_ms_router, meta.latency_ms_analyst, meta.latency_ms_reporter].filter((item) => item != null).join(" / ") || "—"],
      ["Transaction coverage", validation.data_completeness != null ? `${validation.data_completeness}%` : "—"],
    ];
    items.forEach(([k, v]) => {
      const tile = makeEl("div", "meta-tile");
      tile.appendChild(makeEl("dt", "", k));
      tile.appendChild(makeEl("dd", "", v));
      host.appendChild(tile);
    });
  }

  function renderOverview(data) {
    const reasons = (data.anomaly.reasons || []).map((item) => "- " + maskText(item));
    $("whyFlagged").textContent = [
      `Risk Score=${data.anomaly.anomaly_score}/100`,
      `Severity=${severityLabel(data.anomaly.severity || "low")}`,
      `Категории=${(data.anomaly.categories || []).join(", ") || "—"}`,
      `Evidence count=${(data.anomaly.evidence || []).length}`,
      "",
      "Ключевые причины:",
      reasons.join("\n") || "- Причины не указаны.",
    ].join("\n");

    const validation = data.meta.confidence_validation;
    $("overviewConfidence").textContent = validation
      ? [
          `Evidence Reliability=${validation.effective_score}/100`,
          `history_depth=${validation.history_depth}`,
          `evidence_count=${validation.evidence_count}`,
          `anomaly_agreement=${validation.anomaly_agreement}`,
          `transaction_coverage=${validation.data_completeness}%`,
          `malformed_input_ratio=${validation.malformed_input_ratio}`,
          "",
          validation.explanation || "",
        ].join("\n")
      : `Evidence Reliability=${data.anomaly.confidence_score}/100\nПодробности confidence calibration отсутствуют.`;

    $("overviewText").textContent = [
      "Case summary",
      maskText(data.analyst.risk_summary || "Внутренний narrative отсутствует."),
      "",
      "Investigation support note",
      maskText(data.meta.review_notice || "Все результаты предназначены только для поддержки внутреннего review."),
      "",
      "Escalation guidance",
      (data.reporter.recommended_actions || []).length
        ? (data.reporter.recommended_actions || []).map((item) => "- " + maskText(item)).join("\n")
        : "- Review escalation suggested only after analyst validation.",
      "",
      "Analyst sign-off",
      "Проверил: " + (data.meta.reviewed_by || "не указан"),
      "Заметки: " + maskText(data.meta.review_notes || "отсутствуют"),
    ].join("\n");
    renderReliabilityRing(data);
    renderAnalystRecommendation(data);
    renderOverviewSafeMode(data);
    renderTypologyTable(data);
  }

  function renderValidatorSummaryFromAnalyze(data) {
    const summary = data.meta.validator_summary || {};
    const failures = data.meta.policy_failures || [];
    $("validatorWhy").textContent = [
      `status=${summary.status || data.meta.validator_status || "not_run"}`,
      `issues_count=${summary.issues_count != null ? summary.issues_count : data.meta.issues_count || 0}`,
      `failed_stages=${(summary.failed_stages || []).join(", ") || "—"}`,
      `remediation_action=${summary.remediation_action || data.meta.remediation_action || "none"}`,
      "",
      failures.length ? failures.join("\n") : "Явных validator issues нет.",
    ].join("\n");
  }

  function renderSafeModeSummaryFromAnalyze(data) {
    const traces = (data.meta.stage_traces || [])
      .filter((trace) => trace.error_code || trace.status === "emergency")
      .map((trace) => `${trace.stage}: ${trace.error_code || trace.status}`);
    $("emergencyWhy").textContent = [
      narrativeMode(data.meta),
      "",
      safeModeText(data.meta),
      "",
      traces.length ? traces.join("\n") : "Дополнительных ограничений narrative pipeline не зафиксировано.",
    ].join("\n");
  }

  function renderSarArtifact(data) {
    $("sarStructured").textContent = fmt({
      case_reference: data.alert_id || data.meta.request_id,
      review_status: data.meta.review_status,
      escalation_recommendation: data.reporter.recommended_actions || [],
      deterministic_evidence_appendix: (data.anomaly.evidence || []).map((item) => ({
        code: item.code,
        category: item.category,
        contribution: item.contribution,
        tx_refs: item.tx_refs || [],
      })),
      analyst_sign_off: {
        reviewed_by: data.meta.reviewed_by,
        reviewed_at: data.meta.reviewed_at,
        review_notes: data.meta.review_notes,
      },
      human_review_notice: data.meta.review_notice,
    });
    $("outSar").textContent = maskText(data.reporter.sar_body || "");
  }

  function allTransactions() {
    const source = lastSourceRequest || {};
    return (source.historical_transactions || []).concat(source.focus_transactions || []);
  }

  function transactionMap() {
    const map = {};
    allTransactions().forEach((tx, idx) => {
      const key = tx.id || `idx-${idx + 1}`;
      map[key] = tx;
    });
    return map;
  }

  function buildEvidenceRows(data) {
    const reviewNote = $("reviewNotes").value.trim() || data.meta.review_notes || "";
    const txMap = transactionMap();
    const rows = [];
    ((data.anomaly && data.anomaly.evidence) || []).forEach((evidence, evidenceIndex) => {
      const refs = evidence.tx_refs && evidence.tx_refs.length ? evidence.tx_refs : [null];
      refs.forEach((txRef, refIndex) => {
        const tx = txRef ? txMap[txRef] : null;
        rows.push({
          key: `${evidenceIndex}:${txRef || "aggregate"}:${refIndex}`,
          evidence,
          txRef,
          tx,
          timestamp: tx && tx.ts ? tx.ts : "",
          counterparty: tx && tx.counterparty ? tx.counterparty : "",
          direction: tx && tx.direction ? tx.direction : "",
          amount: tx && tx.amount != null ? tx.amount : null,
          typology: evidence.category || "",
          contribution: evidence.contribution != null ? evidence.contribution : null,
          source: txRef ? "transaction evidence" : "profile evidence",
          rule: evidence.code || "",
          reviewNote,
        });
      });
    });
    return rows;
  }

  function currentEvidenceRows() {
    if (!lastResponse) return [];
    const filterValue = evidenceFilterEl.value.trim().toLowerCase();
    const rows = buildEvidenceRows(lastResponse).filter((row) => {
      if (!filterValue) return true;
      const haystack = [
        row.evidence.code,
        row.evidence.category,
        row.counterparty,
        row.direction,
        row.txRef,
        row.evidence.label,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(filterValue);
    });

    rows.sort((a, b) => compareEvidenceRows(a, b, evidenceSort.key, evidenceSort.dir));
    return rows;
  }

  function compareEvidenceRows(a, b, key, dir) {
    const av = rowSortValue(a, key);
    const bv = rowSortValue(b, key);
    if (typeof av === "number" && typeof bv === "number") return dir === "asc" ? av - bv : bv - av;
    return dir === "asc" ? String(av).localeCompare(String(bv)) : String(bv).localeCompare(String(av));
  }

  function rowSortValue(row, key) {
    if (key === "timestamp") return row.timestamp || "";
    if (key === "counterparty") return row.counterparty || "";
    if (key === "direction") return row.direction || "";
    if (key === "amount") return row.amount != null ? row.amount : Number.NEGATIVE_INFINITY;
    if (key === "typology") return row.typology || "";
    if (key === "contribution") return row.contribution != null ? row.contribution : Number.NEGATIVE_INFINITY;
    if (key === "source") return row.source || "";
    if (key === "rule") return row.rule || "";
    return "";
  }

  function toggleEvidenceSort(key) {
    if (evidenceSort.key === key) {
      evidenceSort.dir = evidenceSort.dir === "asc" ? "desc" : "asc";
    } else {
      evidenceSort.key = key;
      evidenceSort.dir = key === "contribution" || key === "amount" ? "desc" : "asc";
    }
    if (lastResponse) renderEvidence(lastResponse);
  }

  function detailBox(label, text) {
    const box = makeEl("div", "detail-box");
    box.appendChild(makeEl("strong", "", label));
    box.appendChild(makeEl("div", "", text || "—"));
    return box;
  }

  function renderEvidence(data) {
    const host = $("evidenceTable");
    clearNode(host);
    const allRows = currentEvidenceRows();
    const totalRows = allRows.length;
    renderEvidencePagination(totalRows);
    if (!totalRows) {
      host.textContent = "Нет детерминированного evidence для текущего фильтра.";
      renderTransactionDrilldown(data, null);
      renderTxTimeline(data, null);
      return;
    }
    const maxPage = Math.max(0, Math.ceil(totalRows / EVIDENCE_PAGE_SIZE) - 1);
    if (evidencePage > maxPage) evidencePage = maxPage;
    const rows = allRows.slice(evidencePage * EVIDENCE_PAGE_SIZE, (evidencePage + 1) * EVIDENCE_PAGE_SIZE);

    const table = document.createElement("table");
    table.className = "ev-grid";
    const columns = [
      ["timestamp", "timestamp"],
      ["counterparty", "counterparty"],
      ["direction", "direction"],
      ["amount", "amount"],
      ["typology", "typology trigger"],
      ["contribution", "anomaly contribution"],
      ["source", "evidence source"],
      ["rule", "deterministic rule"],
      ["review_note", "review note"],
    ];

    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    columns.forEach(([key, label]) => {
      const th = document.createElement("th");
      if (SORTABLE_COLUMNS.includes(key)) {
        const btn = makeEl("button", "table-sort", label + (evidenceSort.key === key ? ` (${evidenceSort.dir})` : ""));
        btn.type = "button";
        btn.addEventListener("click", () => toggleEvidenceSort(key));
        th.appendChild(btn);
      } else {
        th.textContent = label;
      }
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    let currentGroup = null;
    rows.forEach((row, index) => {
      const groupMode = evidenceGroupEl.value;
      const groupValue = groupMode === "category" ? row.typology || "без категории" : groupMode === "source" ? row.source : null;
      if (groupValue && groupValue !== currentGroup) {
        currentGroup = groupValue;
        const groupRow = document.createElement("tr");
        groupRow.className = "group-row";
        const groupCell = document.createElement("td");
        groupCell.colSpan = columns.length;
        groupCell.textContent = `${groupMode === "category" ? "Типология" : "Источник"}: ${groupValue}`;
        groupRow.appendChild(groupCell);
        tbody.appendChild(groupRow);
      }

      const tr = document.createElement("tr");
      tr.className = "clickable-row";
      tr.addEventListener("click", () => {
        selectedEvidenceKey = selectedEvidenceKey === row.key ? null : row.key;
        renderEvidence(data);
        renderTransactionDrilldown(data, row);
      });
      tr.appendChild(makeEl("td", "", row.timestamp ? formatDateTime(row.timestamp) : "—"));
      tr.appendChild(makeEl("td", "", maskText(row.counterparty || "—")));
      tr.appendChild(makeEl("td", "", row.direction || "—"));
      tr.appendChild(makeEl("td", "", row.amount != null ? formatAmount(row.amount) : "—"));
      tr.appendChild(makeEl("td", "", row.typology || "—"));
      tr.appendChild(makeEl("td", "", row.contribution != null ? row.contribution : "—"));
      tr.appendChild(makeEl("td", "", row.source));
      tr.appendChild(makeEl("td", "", row.rule || "—"));
      tr.appendChild(makeEl("td", "", maskText(row.reviewNote || "—")));
      tbody.appendChild(tr);

      if (selectedEvidenceKey === row.key) {
        const detailRow = document.createElement("tr");
        detailRow.className = "detail-row";
        const detailCell = document.createElement("td");
        detailCell.colSpan = columns.length;
        const detailCard = makeEl("div", "detail-card");
        detailCard.appendChild(detailBox("Typology trigger", `${row.evidence.code || "—"} / ${row.evidence.category || "—"}`));
        detailCard.appendChild(detailBox("Observed vs threshold", `${row.evidence.observed_value != null ? row.evidence.observed_value : "—"} vs ${row.evidence.threshold_value != null ? row.evidence.threshold_value : "—"}`));
        detailCard.appendChild(detailBox("Baseline", row.evidence.baseline_value != null ? String(row.evidence.baseline_value) : "—"));
        detailCard.appendChild(detailBox("Linked tx refs", (row.evidence.tx_refs || []).join(", ") || "—"));
        detailCard.appendChild(detailBox("Evidence source", row.txRef ? `transaction ${row.txRef}` : "aggregate profile rule"));
        detailCard.appendChild(detailBox("Rule note", maskText(row.evidence.label || "—")));
        detailCell.appendChild(detailCard);
        detailRow.appendChild(detailCell);
        tbody.appendChild(detailRow);
      }

      if (selectedEvidenceKey == null && index === 0) selectedEvidenceKey = row.key;
    });

    table.appendChild(tbody);
    host.appendChild(table);
    const selected = allRows.find((row) => row.key === selectedEvidenceKey) || allRows[0];
    renderTransactionDrilldown(data, selected);
    renderTxTimeline(data, selected);
  }

  function renderTransactionDrilldown(data, row) {
    const txHost = $("txDrilldown");
    const relatedHost = $("txRelated");
    clearNode(txHost);
    clearNode(relatedHost);
    if (!row) {
      txHost.textContent = "Выберите строку evidence, чтобы открыть transaction drilldown.";
      relatedHost.textContent = "Связанные typologies и соседние транзакции появятся здесь.";
      return;
    }

    const tx = row.tx;
    const summary = {
      evidence_code: row.evidence.code,
      typology_trigger: row.evidence.category,
      tx_ref: row.txRef || "aggregate",
      timestamp: tx && tx.ts ? tx.ts : null,
      counterparty: tx && tx.counterparty ? tx.counterparty : null,
      direction: tx && tx.direction ? tx.direction : null,
      amount: tx && tx.amount != null ? tx.amount : null,
      channel: tx && tx.channel ? tx.channel : null,
      narrative: tx && tx.narrative ? tx.narrative : null,
      review_note: $("reviewNotes").value.trim() || data.meta.review_notes || null,
    };
    txHost.appendChild(makeEl("pre", "pre-wrap small compact-pre", JSON.stringify(maskDeep(summary), null, 2)));

    const refs = row.evidence.tx_refs || [];
    const nearby = [];
    refs.forEach((ref) => {
      const base = transactionMap()[ref];
      if (!base || !base.ts) return;
      const baseTs = Date.parse(base.ts);
      allTransactions().forEach((candidate) => {
        if (!candidate.ts || candidate === base) return;
        const deltaMs = Math.abs(Date.parse(candidate.ts) - baseTs);
        if (Number.isFinite(deltaMs) && deltaMs <= 2 * 60 * 60 * 1000) {
          nearby.push({
            counterparty: candidate.counterparty || "n/a",
            direction: candidate.direction || "n/a",
            amount: candidate.amount,
            delta_minutes: Math.round(deltaMs / 60000),
          });
        }
      });
    });
    const relatedEvidence = ((data.anomaly && data.anomaly.evidence) || [])
      .filter((item) => (item.tx_refs || []).some((ref) => refs.includes(ref)))
      .map((item) => `${item.code}:${item.category}`);
    relatedHost.appendChild(
      makeEl(
        "pre",
        "pre-wrap small compact-pre",
        JSON.stringify(
          maskDeep({
            linked_typologies: [row.evidence.category],
            related_evidence: relatedEvidence,
            time_proximity: nearby.slice(0, 20),
          }),
          null,
          2
        )
      )
    );
  }

  function renderTraces(data) {
    const hosts = [$("traceContainer"), $("traceContainerWorkspace")].filter(Boolean);
    const traces = (data && data.meta && data.meta.stage_traces) || [];
    hosts.forEach((host) => {
      clearNode(host);
      if (!traces.length) {
        host.textContent = "Трассы пока отсутствуют.";
        return;
      }
      traces.forEach((trace) => {
        const det = document.createElement("details");
        det.className = "trace-block";
        const summary = document.createElement("summary");
        summary.appendChild(makeEl("strong", "", trace.stage || ""));
        summary.appendChild(document.createTextNode(` · ${trace.status} · ${trace.provider || "none"} · validator=${trace.validator_status || "not_run"}`));
        const pre = makeEl("pre", "pre-wrap small compact-pre", JSON.stringify(maskDeep(trace), null, 2));
        det.append(summary, pre);
        host.appendChild(det);
      });
    });
  }

  function renderReplaySummary(data) {
    clearNode(replaySummary);
    if (!data) {
      const pending = [
        ["Replay verified", "Ожидает загруженный bundle для проверки."],
        ["Hash verified", "Будет рассчитано после replay."],
        ["Signature verified", "Будет рассчитано после replay."],
        ["No drift detected", "Пока нет данных replay."],
      ];
      pending.forEach(([title, message]) => {
        const item = makeEl("div", "status-item status-warn");
        item.appendChild(makeEl("strong", "", title));
        item.appendChild(makeEl("div", "", message));
        replaySummary.appendChild(item);
      });
      diagnosticReplay.textContent = fmt({ replay_status: "pending", message: "Загрузите signed bundle для deterministic replay verification." });
      cloneReplayStatusCards(replaySummary, $("replayAuditPanel"), null);
      return;
    }

    const signatureCheck = (data.hash_checks || []).find((item) => item.name === "manifest_signature");
    const deterministicChecks = (data.hash_checks || []).filter((item) => item.name.indexOf("deterministic:") === 0);
    const fileChecks = (data.hash_checks || []).filter((item) => item.name.indexOf("deterministic:") !== 0 && item.name !== "manifest_signature");
    const signatureVerified = signatureCheck ? signatureCheck.matches : false;
    const hashVerified = fileChecks.length ? fileChecks.every((item) => item.matches) : false;
    const driftFree = !data.drift_detected;
    const replayVerified = data.replay_status === "match";

    [
      ["Replay verified", replayVerified ? "Deterministic replay confirmed." : "Replay detected mismatch or invalid bundle.", replayVerified],
      ["Hash verified", hashVerified ? "Bundle file hashes match manifest." : "Hash mismatch detected in one or more bundle files.", hashVerified],
      ["Signature verified", signatureVerified ? "Manifest signature is valid." : "Manifest signature check failed.", signatureVerified],
      ["No drift detected", driftFree ? "Stored and replayed deterministic outputs are aligned." : "Drift detected between stored and replayed artifacts.", driftFree],
    ].forEach(([title, message, ok]) => {
      const item = makeEl("div", "status-item " + (ok ? "status-ok" : "status-danger"));
      item.appendChild(makeEl("strong", "", title));
      item.appendChild(makeEl("div", "", message));
      replaySummary.appendChild(item);
    });

    diagnosticReplay.textContent = fmt({
      request_id: data.request_id,
      replay_status: data.replay_status,
      signature_verified: signatureVerified,
      bundle_hashes_verified: hashVerified,
      deterministic_hashes_verified: deterministicChecks.every((item) => item.matches),
      drift_detected: data.drift_detected,
      validator_summary: data.validator_summary,
      drift_report: data.drift_report,
    });
    cloneReplayStatusCards(replaySummary, $("replayAuditPanel"), data);
  }

  function renderAnalyzeResult(data) {
    lastResponse = data;
    lastReplay = null;
    emptyHint.style.display = "none";
    reviewBanner.hidden = false;
    copySarStatus.textContent = "";
    bundleStatus.textContent = "";
    setReviewControls(data.meta || {});
    renderCaseHeaderForAnalyze(data);
    renderRiskHeader(data.anomaly.anomaly_score, data.anomaly.severity || "low");
    renderChips(data);
    renderMetaGrid(data);
    renderOverview(data);
    renderValidatorSummaryFromAnalyze(data);
    renderSafeModeSummaryFromAnalyze(data);
    renderEvidence(data);
    renderTraces(data);
    renderSarArtifact(data);
    renderReplaySummary(null);
    $("outRouter").textContent = fmt(data.router);
    $("outAnalyst").textContent = fmt(data.analyst);
    $("outRaw").textContent = fmt(data);
    setWizardStep("analyze");
    upsertQueueCase(data, lastSourceRequest);
    renderGovernancePanels(data);
    activateWorkspaceTab("overview");
    activateTab("replay");
  }

  function renderReplay(data) {
    lastResponse = null;
    lastReplay = data;
    emptyHint.style.display = "none";
    reviewBanner.hidden = false;
    clearNode(chips);
    chips.appendChild(makeEl("span", data.drift_detected ? "chip chip-danger" : "chip", data.drift_detected ? "drift detected" : "replay verified"));
    chips.appendChild(makeEl("span", "chip", "replay status: " + data.replay_status));
    renderCaseHeaderForReplay(data);
    renderRiskHeader(data.replayed_anomaly ? data.replayed_anomaly.anomaly_score : null, data.replayed_anomaly ? data.replayed_anomaly.severity : null);
    renderReplaySummary(data);
    $("overviewMeta").textContent = "";
    $("whyFlagged").textContent = "Replay mode сравнивает integrity, hash checks и deterministic drift. Analyst narrative при replay не генерируется.";
    $("overviewConfidence").textContent = data.replayed_anomaly ? `Evidence Reliability=${data.replayed_anomaly.confidence_score}/100` : "Evidence Reliability не рассчитан.";
    $("validatorWhy").textContent = fmt(data.validator_summary || {});
    $("emergencyWhy").textContent = data.drift_detected ? "Replay обнаружил mismatch или tamper. Требуется ручная проверка bundle." : "Replay подтвердил детерминированную целостность bundle и отсутствие drift.";
    $("overviewText").textContent = fmt({
      request_id: data.request_id,
      replay_status: data.replay_status,
      hash_checks: data.hash_checks,
      drift_report: data.drift_report,
    });
    $("evidenceTable").textContent = "Replay mode: evidence explorer недоступен без полного analyst response. Используйте replay diagnostics справа.";
    $("txDrilldown").textContent = "Replay mode: transaction drilldown не загружен.";
    $("txRelated").textContent = "";
    const hashText = fmt(data.hash_checks || []);
    $("traceContainer").textContent = hashText;
    const traceWs = $("traceContainerWorkspace");
    if (traceWs) traceWs.textContent = hashText;
    const txTimeline = $("txTimeline");
    if (txTimeline) txTimeline.textContent = "Replay mode: transaction timeline недоступен.";
    $("outRouter").textContent = "";
    $("outAnalyst").textContent = "";
    $("sarStructured").textContent = "";
    $("outSar").textContent = "";
    $("outRaw").textContent = fmt(data);
    copySarStatus.textContent = "";
    bundleStatus.textContent = data.drift_detected ? "Replay обнаружил drift или tamper." : "Replay подтвердил signed audit bundle.";
    activateWorkspaceTab("replay-audit");
    activateTab("replay");
  }

  async function exportBundle() {
    if (!lastResponse) {
      showError("Сначала выполните анализ кейса.");
      return;
    }
    let sourceRequest;
    try {
      sourceRequest = parsePayload();
    } catch (err) {
      showError("Не удалось прочитать normalized request: " + err.message);
      return;
    }
    const analysis = applyReviewState(JSON.parse(JSON.stringify(lastResponse)));
    setLoading(true);
    try {
      const res = await fetch("/api/v1/export/case", {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
        body: JSON.stringify({ source_request: sourceRequest, analysis }),
      });
      if (!res.ok) throw new Error((await res.text()).slice(0, 200));
      await downloadResponse(res, "amber-case.zip");
      bundleStatus.textContent = "Signed case bundle экспортирован. Теперь его можно проверить через deterministic replay.";
      caseReplayBadge.textContent = "bundle ready for replay verification";
    } catch (err) {
      showError("Ошибка экспорта bundle: " + (err.message || String(err)));
    } finally {
      setLoading(false);
      await refreshOperationalPanels();
    }
  }

  async function exportSar(format) {
    if (!lastResponse) {
      showError("Сначала выполните анализ кейса.");
      return;
    }
    let sourceRequest;
    try {
      sourceRequest = parsePayload();
    } catch (err) {
      showError("Не удалось прочитать normalized request: " + err.message);
      return;
    }
    const analysis = applyReviewState(JSON.parse(JSON.stringify(lastResponse)));
    setLoading(true);
    try {
      const res = await fetch(`/api/v1/export/sar?format=${encodeURIComponent(format)}`, {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
        body: JSON.stringify({ source_request: sourceRequest, analysis }),
      });
      if (!res.ok) throw new Error((await res.text()).slice(0, 200));
      await downloadResponse(res, `amber-sar.${format === "markdown" ? "md" : format}`);
      bundleStatus.textContent = "Internal compliance memo экспортирован в формате " + format + ".";
    } catch (err) {
      showError("Ошибка экспорта memo: " + (err.message || String(err)));
    } finally {
      setLoading(false);
      await refreshOperationalPanels();
    }
  }

  async function downloadResponse(res, fallbackName) {
    const blob = await res.blob();
    const match = (res.headers.get("Content-Disposition") || "").match(/filename=\"([^\"]+)\"/);
    const filename = match ? match[1] : fallbackName;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  async function replayBundle() {
    const fileInput = $("replayBundleFile");
    if (!fileInput.files || !fileInput.files[0]) {
      showError("Сначала выберите replay bundle.");
      return;
    }
    const fd = new FormData();
    fd.append("file", fileInput.files[0]);
    setLoading(true);
    try {
      const res = await fetch("/api/v1/replay", { method: "POST", headers: authHeaders(), body: fd });
      const json = await parseJsonResponse(res);
      renderReplay(json);
      await refreshOperationalPanels();
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  function exportEvidenceCsv() {
    const rows = currentEvidenceRows();
    if (!rows.length) {
      showError("Нет evidence для экспорта.");
      return;
    }
    const headers = ["timestamp", "counterparty", "direction", "amount", "typology_trigger", "anomaly_contribution", "evidence_source", "deterministic_rule", "review_note"];
    const lines = [headers.join(",")];
    rows.forEach((row) => {
      const values = [
        row.timestamp || "",
        row.counterparty || "",
        row.direction || "",
        row.amount != null ? row.amount : "",
        row.typology || "",
        row.contribution != null ? row.contribution : "",
        row.source || "",
        row.rule || "",
        row.reviewNote || "",
      ];
      lines.push(values.map(csvEscape).join(","));
    });
    const blob = new Blob([lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "amber-evidence.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    bundleStatus.textContent = "Evidence explorer экспортирован в CSV.";
  }

  function csvEscape(value) {
    const text = String(maskText(value == null ? "" : value));
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  async function copySar() {
    if (!lastResponse || !lastResponse.reporter || !lastResponse.reporter.sar_body) {
      copySarStatus.textContent = "Memo body отсутствует.";
      return;
    }
    try {
      await navigator.clipboard.writeText(lastResponse.reporter.sar_body);
      copySarStatus.textContent = "Скопировано.";
    } catch {
      copySarStatus.textContent = "Clipboard недоступен.";
    }
  }

  async function copyPilotClientText() {
    const source = $("pilotClientText");
    if (!source) return;
    pilotClientCopyStatus.textContent = "";
    try {
      await navigator.clipboard.writeText(source.textContent || "");
      pilotClientCopyStatus.textContent = "Текст для пилотных клиентов скопирован.";
    } catch {
      pilotClientCopyStatus.textContent = "Clipboard недоступен.";
    }
  }

  function clearOut() {
    lastResponse = null;
    lastReplay = null;
    lastIngest = null;
    selectedEvidenceKey = null;
    evidenceSort = { key: SORT_DEFAULT.key, dir: SORT_DEFAULT.dir };
    evidenceFilterEl.value = "";
    evidenceGroupEl.value = "none";
    evidencePage = 0;
    setWizardStep("upload");
    activateWorkspaceTab("overview");
    reviewBanner.hidden = true;
    scorePill.hidden = true;
    severityPill.hidden = true;
    caseSafeModeBadge.hidden = true;
    caseReplayBadge.textContent = "replay not verified";
    caseReviewRequired.hidden = false;
    clearNode(chips);
    [
      "outRouter",
      "outAnalyst",
      "outSar",
      "outRaw",
      "overviewText",
      "overviewConfidence",
      "sarStructured",
      "whyFlagged",
      "validatorWhy",
      "emergencyWhy",
      "csvReport",
      "csvColumns",
      "txDrilldown",
      "txRelated",
      "telemetryPanel",
      "diagnosticReplay",
    ].forEach((id) => {
      const el = $(id);
      if (el) el.textContent = "";
    });
    ["overviewMeta", "evidenceTable", "traceContainer", "traceContainerWorkspace", "csvPreviewTable", "csvMalformedTable", "replaySummary", "replayAuditPanel", "normalizationViz", "typologyTable", "evidencePagination"].forEach((id) => {
      const el = $(id);
      if (el) clearNode(el);
    });
    setHeaderText("caseTitle", "Ожидание кейса");
    setHeaderText("caseSubtitle", "Amber подготавливает explainable evidence и внутренний memo для supervised review. Любая эскалация требует подтверждения аналитиком.");
    setHeaderText("caseId", "—");
    setHeaderText("caseReviewStatus", "pending");
    setHeaderText("caseJurisdiction", "—");
    setHeaderText("caseMode", "—");
    setHeaderText("caseAnalyst", "—");
    setHeaderText("caseUpdatedAt", "—");
    setHeaderText("caseReliability", "—");
    setHeaderText("caseValidatorStatus", "—");
    emptyHint.style.display = "block";
    copySarStatus.textContent = "";
    bundleStatus.textContent = "";
    if (pilotClientCopyStatus) pilotClientCopyStatus.textContent = "";
    showError("");
    renderReplaySummary(null);
    renderEnvironmentStatus();
  }

  $("btnSend").addEventListener("click", sendJson);
  $("btnCsvPreview").addEventListener("click", previewCsvFromInput);
  const btnXlsx = $("btnXlsxPreview");
  if (btnXlsx) btnXlsx.addEventListener("click", previewXlsxFromInput);
  const queueFilter = $("queueFilter");
  if (queueFilter) queueFilter.addEventListener("change", renderQueuePanel);
  $("btnAnalyzePreview").addEventListener("click", analyzePreview);
  $("btnExportBundle").addEventListener("click", exportBundle);
  $("btnExportSarTxt").addEventListener("click", () => exportSar("txt"));
  $("btnExportSarMd").addEventListener("click", () => exportSar("markdown"));
  $("btnExportSarDocx").addEventListener("click", () => exportSar("docx"));
  $("btnReplayBundle").addEventListener("click", replayBundle);
  $("btnEvidenceCsv").addEventListener("click", exportEvidenceCsv);
  $("btnCopySar").addEventListener("click", copySar);
  $("btnCopyPilotText").addEventListener("click", copyPilotClientText);
  $("btnSample").addEventListener("click", () => {
    payloadEl.value = JSON.stringify(SAMPLE, null, 2);
    lastSourceRequest = SAMPLE;
  });
  $("btnClear").addEventListener("click", clearOut);
  evidenceFilterEl.addEventListener("input", () => {
    evidencePage = 0;
    if (lastResponse) renderEvidence(lastResponse);
  });
  evidenceGroupEl.addEventListener("change", () => {
    evidencePage = 0;
    if (lastResponse) renderEvidence(lastResponse);
  });
  const btnSaveDraft = $("btnSaveDraft");
  const btnCloseCase = $("btnCloseCase");
  if (btnSaveDraft) {
    btnSaveDraft.addEventListener("click", async () => {
      try {
        await applyWorkflow("set_status", { review_status: $("reviewStatus").value });
        $("reviewDraftStatus").textContent = "Черновик workflow сохранён (stateless artifact).";
      } catch (err) {
        showError(err.message || String(err));
      }
    });
  }
  if (btnCloseCase) {
    btnCloseCase.addEventListener("click", async () => {
      try {
        await applyWorkflow("close", { review_status: "closed" });
        $("reviewDraftStatus").textContent = "Кейс закрыт supervisor workflow (локальный artifact).";
      } catch (err) {
        showError(err.message || String(err));
      }
    });
  }

  renderDemoLibrary();
  renderReplaySummary(null);
  renderSessionStatus();
  renderQueuePanel();
  refreshOperationalPanels();
})();
