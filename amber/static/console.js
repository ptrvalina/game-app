/**
 * Amber supervised pilot console.
 * All rendering stays text-only and deterministic-first.
 */
(function () {
  const SAMPLE = {
    mode: "fiat",
    jurisdiction: "BY",
    alert_id: "DEMO-2026-001",
    client_profile: { declared_monthly_income: 1000, declared_occupation: "Инженер" },
    historical_transactions: [
      { ts: "2026-04-01T10:00:00", amount: 1200, direction: "in", counterparty: "ООО Ромашка", asset_type: "fiat" },
      { ts: "2026-04-15T14:00:00", amount: 800, direction: "out", counterparty: "ИП Иванов", asset_type: "fiat" },
    ],
    focus_transactions: [
      {
        ts: "2026-05-10T23:15:00",
        amount: 5800,
        direction: "in",
        counterparty: "ООО Технопром",
        channel: "cash",
        asset_type: "fiat",
        narrative: "Внесение наличных",
      },
      {
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
    { id: "fiat-structuring", title: "Фиатное дробление", mode: "fiat", jurisdiction: "EU", file: "/demo/fiat_structuring.csv", description: "Серия близких входящих сумм под threshold band." },
    { id: "crypto-layering", title: "Крипто-layering", mode: "crypto", jurisdiction: "EU", file: "/demo/crypto_layering.csv", description: "Крипто-маршрутизация и exchange-like активность с детерминированными сигналами." },
    { id: "cross-transition", title: "Cross-border transition", mode: "cross", jurisdiction: "EU", file: "/demo/cross_border_case.csv", description: "Быстрый fiat-to-crypto переход в коротком окне." },
    { id: "dormant-reactivation", title: "Реактивация спящего профиля", mode: "fiat", jurisdiction: "BY", file: "/demo/dormant_reactivation.csv", description: "Долгий период без активности и резкое возобновление." },
    { id: "salary-mismatch", title: "Несоответствие зарплатному профилю", mode: "fiat", jurisdiction: "BY", file: "/demo/salary_mismatch.csv", description: "Заявленный профиль не совпадает с наблюдаемыми притоками." },
    { id: "exchange-hopping", title: "Exchange hopping", mode: "crypto", jurisdiction: "EU", file: "/demo/exchange_hopping.csv", description: "Несколько exchange-like контрагентов за короткий период." },
  ];

  const STORAGE_KEY = "amber_console_api_key";
  const SORTABLE_COLUMNS = ["code", "category", "contribution", "threshold_value", "baseline_value", "observed_value"];

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
  apiKeyEl.addEventListener("change", () => sessionStorage.setItem(STORAGE_KEY, apiKeyEl.value.trim()));
  payloadEl.value = JSON.stringify(SAMPLE, null, 2);

  let lastResponse = null;
  let lastIngest = null;
  let lastSourceRequest = SAMPLE;
  let selectedEvidenceIndex = 0;
  let evidenceSort = { key: "contribution", dir: "desc" };

  function setLoading(on) {
    loading.classList.toggle("show", on);
    loading.setAttribute("aria-hidden", on ? "false" : "true");
    ["btnSend", "btnCsvPreview", "btnAnalyzePreview", "btnExportBundle", "btnReplayBundle"].forEach((id) => {
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

  function authHeaders() {
    const headers = { "X-Request-ID": uuid() };
    const key = apiKeyEl.value.trim();
    if (key) headers["X-Api-Key"] = key;
    return headers;
  }

  function activateTab(name) {
    document.querySelectorAll(".tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === name);
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.panel === name);
    });
  }

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });

  function makeEl(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text != null) el.textContent = String(text);
    return el;
  }

  function clearNode(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function maskText(value) {
    if (value == null) return value;
    return String(value)
      .replace(/\b([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b/g, (_, name, host) => `${name.slice(0, 1)}***${name.slice(-1)}@${host}`)
      .replace(/(?<!\w)(\+?\d[\d\-\s()]{7,}\d)(?!\w)/g, (m) => (m.length > 6 ? `${m.slice(0, 2)}***${m.slice(-2)}` : "***"))
      .replace(/\b\d{10,20}\b/g, (m) => `${m.slice(0, 2)}***${m.slice(-2)}`)
      .replace(/\b(?:0x[a-fA-F0-9]{10,}|[13][a-km-zA-HJ-NP-Z1-9]{10,}|[A-Za-z0-9]{18,64})\b/g, (m) =>
        m.length > 8 ? `${m.slice(0, 4)}***${m.slice(-4)}` : "***"
      );
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

  function severityClass(sev) {
    if (sev === "critical") return "sev-critical";
    if (sev === "high") return "sev-high";
    if (sev === "medium") return "sev-medium";
    return "sev-low";
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

  async function loadEnvironment() {
    try {
      const res = await fetch("/ready", { headers: authHeaders() });
      const data = await parseJsonResponse(res);
      if (data.demo_mode) {
        demoBanner.hidden = false;
        demoModeBadge.hidden = false;
      }
    } catch {
      // Ignore environment banner failures for local demos.
    }
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
    if (Object.keys(overrides).length) {
      fd.append("column_overrides_json", JSON.stringify(overrides));
    }
    setLoading(true);
    try {
      const res = await fetch("/api/v1/ingest/csv", { method: "POST", headers: authHeaders(), body: fd });
      const json = await parseJsonResponse(res);
      lastIngest = json;
      lastSourceRequest = json.normalized_request;
      payloadEl.value = JSON.stringify(json.normalized_request, null, 2);
      renderCsvPreview(json);
      activateTab("overview");
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  function renderCsvPreview(data) {
    $("csvReport").textContent = fmt({ summary: data.summary, normalization_report: data.normalization_report });
    $("csvColumns").textContent = fmt({ available_columns: data.available_columns, detected_mapping: data.normalization_report?.column_mapping || {} });
    renderPreviewTable($("csvPreviewTable"), data.preview_rows.filter((row) => row.status === "parsed"));
    renderPreviewTable($("csvMalformedTable"), data.preview_rows.filter((row) => row.status === "rejected"));
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
      activateTab("overview");
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

  function renderChips(data) {
    clearNode(chips);
    ["режим: " + data.mode, "юрисдикция: " + data.jurisdiction, "llm: " + data.meta.llm_used].forEach((label) => {
      chips.appendChild(makeEl("span", "chip", label));
    });
    if (data.meta.degraded_mode) chips.appendChild(makeEl("span", "chip chip-warn", "DEGRADED"));
    if (data.meta.emergency_mode) chips.appendChild(makeEl("span", "chip chip-danger", "EMERGENCY"));
    if (data.meta.fallback_used) chips.appendChild(makeEl("span", "chip", "fallback провайдера"));
    if (data.meta.operating_reason) chips.appendChild(makeEl("span", "chip", "причина: " + data.meta.operating_reason));
  }

  function renderMetaGrid(data) {
    const host = $("overviewMeta");
    clearNode(host);
    const items = [
      ["request_id", data.meta.request_id || "—"],
      ["review_status", data.meta.review_status || "pending"],
      ["validator_status", data.meta.validator_status || "—"],
      ["confidence", data.anomaly.confidence_score],
      ["severity", data.anomaly.severity || "—"],
      ["llm_used", data.meta.llm_used || "—"],
      ["latency_ms", [data.meta.latency_ms_router, data.meta.latency_ms_analyst, data.meta.latency_ms_reporter].filter(Boolean).join(" / ") || "—"],
    ];
    items.forEach(([k, v]) => {
      const tile = makeEl("div", "meta-tile");
      tile.appendChild(makeEl("dt", "", k));
      tile.appendChild(makeEl("dd", "", v));
      host.appendChild(tile);
    });
  }

  function renderOverviewExplainability(data) {
    $("whyFlagged").textContent = [
      "anomaly_score=" + data.anomaly.anomaly_score,
      "категории=" + (data.anomaly.categories || []).join(", "),
      "причины:",
      (data.anomaly.reasons || []).map((item) => "- " + maskText(item)).join("\n"),
    ].join("\n");
    $("overviewConfidence").textContent = data.meta.confidence_validation
      ? [
          "cap=" + data.meta.confidence_validation.cap,
          "reasons=" + (data.meta.confidence_validation.reasons || []).join(", "),
          data.meta.confidence_validation.explanation || "",
        ].join("\n")
      : "Нет подробностей по confidence calibration.";
    $("validatorWhy").textContent = data.meta.policy_failures && data.meta.policy_failures.length
      ? data.meta.policy_failures.join("\n")
      : "Validator downgrade issues отсутствуют.";
    const emergencyReasons = (data.meta.stage_traces || [])
      .filter((trace) => trace.error_code || trace.status === "emergency")
      .map((trace) => `${trace.stage}: ${trace.error_code || trace.status}`);
    $("emergencyWhy").textContent = emergencyReasons.length
      ? emergencyReasons.join("\n")
      : (data.meta.operating_reason || "Emergency trigger отсутствует.");
    $("overviewText").textContent = [
      maskText(data.meta.review_notice || ""),
      "",
      maskText(data.analyst.risk_summary || ""),
      "",
      "Проверил: " + (data.meta.reviewed_by || "n/a"),
      "Заметки: " + maskText(data.meta.review_notes || "Заметки аналитика отсутствуют."),
    ].join("\n");
  }

  function sortedEvidence(data) {
    const rows = ((data.anomaly && data.anomaly.evidence) || []).slice();
    const { key, dir } = evidenceSort;
    rows.sort((a, b) => {
      const av = a[key] == null ? "" : a[key];
      const bv = b[key] == null ? "" : b[key];
      if (typeof av === "number" && typeof bv === "number") return dir === "asc" ? av - bv : bv - av;
      const as = String(av);
      const bs = String(bv);
      return dir === "asc" ? as.localeCompare(bs) : bs.localeCompare(as);
    });
    return rows;
  }

  function toggleEvidenceSort(key) {
    if (evidenceSort.key === key) {
      evidenceSort.dir = evidenceSort.dir === "asc" ? "desc" : "asc";
    } else {
      evidenceSort.key = key;
      evidenceSort.dir = key === "contribution" ? "desc" : "asc";
    }
    if (lastResponse) renderEvidence(lastResponse);
  }

  function renderEvidence(data) {
    const host = $("evidenceTable");
    clearNode(host);
    const rows = sortedEvidence(data);
    if (!rows.length) {
      host.textContent = "Нет детерминированного evidence.";
      return;
    }
    const table = document.createElement("table");
    table.className = "ev-grid";
    const columns = [
      ["code", "код"],
      ["category", "категория"],
      ["contribution", "вес"],
      ["threshold_value", "порог"],
      ["baseline_value", "baseline"],
      ["observed_value", "наблюдение"],
      ["tx_refs", "tx refs"],
      ["label", "описание"],
    ];
    const thead = document.createElement("thead");
    const htr = document.createElement("tr");
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
      htr.appendChild(th);
    });
    thead.appendChild(htr);
    table.appendChild(thead);
    const tbody = document.createElement("tbody");
    rows.forEach((ev, index) => {
      const tr = document.createElement("tr");
      tr.className = "clickable-row";
      tr.addEventListener("click", () => {
        selectedEvidenceIndex = index;
        renderTransactionDrilldown(data, ev);
        activateTab("transactions");
      });
      tr.appendChild(makeEl("td", "", ev.code || ""));
      tr.appendChild(makeEl("td", "", ev.category || ""));
      tr.appendChild(makeEl("td", "", ev.contribution != null ? ev.contribution : "—"));
      tr.appendChild(makeEl("td", "", ev.threshold_value != null ? ev.threshold_value : "—"));
      tr.appendChild(makeEl("td", "", ev.baseline_value != null ? ev.baseline_value : "—"));
      tr.appendChild(makeEl("td", "", ev.observed_value != null ? ev.observed_value : "—"));
      tr.appendChild(makeEl("td", "", (ev.tx_refs || []).join(", ")));
      tr.appendChild(makeEl("td", "", maskText(ev.label || "")));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    host.appendChild(table);
    renderTransactionDrilldown(data, rows[Math.min(selectedEvidenceIndex, rows.length - 1)]);
  }

  function allTransactions() {
    const source = lastSourceRequest || {};
    const hist = source.historical_transactions || [];
    const focus = source.focus_transactions || [];
    return hist.concat(focus);
  }

  function transactionMap() {
    const map = {};
    allTransactions().forEach((tx, idx) => {
      const key = tx.id || `idx-${idx + 1}`;
      map[key] = tx;
    });
    return map;
  }

  function renderTransactionDrilldown(data, evidence) {
    const txHost = $("txDrilldown");
    const relatedHost = $("txRelated");
    clearNode(txHost);
    clearNode(relatedHost);
    if (!evidence) {
      txHost.textContent = "Выберите evidence, чтобы посмотреть связанные транзакции.";
      return;
    }
    const map = transactionMap();
    const refs = evidence.tx_refs || [];
    txHost.appendChild(makeEl("div", "hint", `Evidence: ${evidence.code} / ${evidence.category}`));
    if (!refs.length) {
      txHost.appendChild(makeEl("div", "", "Для этого evidence нет привязанных tx references."));
      return;
    }
    const txList = refs.map((ref) => ({ ref, tx: map[ref] })).filter((item) => item.tx);
    txList.forEach(({ ref, tx }) => {
      const card = makeEl("pre", "pre-wrap small compact-pre", JSON.stringify(maskDeep({ tx_ref: ref, transaction: tx }), null, 2));
      txHost.appendChild(card);
    });
    const nearby = [];
    txList.forEach(({ tx }) => {
      if (!tx.ts) return;
      const baseTs = Date.parse(tx.ts);
      allTransactions().forEach((candidate) => {
        if (!candidate.ts || candidate.id === tx.id) return;
        const deltaMs = Math.abs(Date.parse(candidate.ts) - baseTs);
        if (Number.isFinite(deltaMs) && deltaMs <= 2 * 60 * 60 * 1000) {
          nearby.push({
            tx_id: candidate.id || "n/a",
            delta_minutes: Math.round(deltaMs / 60000),
            counterparty: candidate.counterparty || "n/a",
            amount: candidate.amount,
            direction: candidate.direction,
          });
        }
      });
    });
    relatedHost.appendChild(
      makeEl(
        "pre",
        "pre-wrap small compact-pre",
        JSON.stringify(
          maskDeep({
            linked_typologies: [evidence.category],
            related_evidence: ((data.anomaly && data.anomaly.evidence) || [])
              .filter((item) => (item.tx_refs || []).some((ref) => refs.includes(ref)))
              .map((item) => item.code),
            time_proximity: nearby.slice(0, 20),
          }),
          null,
          2
        )
      )
    );
  }

  function renderTraces(data) {
    const host = $("traceContainer");
    clearNode(host);
    const traces = (data.meta && data.meta.stage_traces) || [];
    if (!traces.length) {
      host.textContent = "Нет трасс.";
      return;
    }
    traces.forEach((trace) => {
      const det = document.createElement("details");
      det.className = "trace-block";
      const summary = document.createElement("summary");
      summary.appendChild(makeEl("strong", "", trace.stage || ""));
      summary.appendChild(document.createTextNode(` · ${trace.status} · ${trace.provider || "none"} · validator=${trace.validator_status || "not_run"}`));
      const pre = makeEl("pre", "pre-wrap small", JSON.stringify(maskDeep(trace), null, 2));
      det.append(summary, pre);
      host.appendChild(det);
    });
  }

  function renderAnalyzeResult(data) {
    lastResponse = data;
    emptyHint.style.display = "none";
    reviewBanner.hidden = false;
    setReviewControls(data.meta || {});
    renderChips(data);
    renderMetaGrid(data);
    renderOverviewExplainability(data);
    renderEvidence(data);
    renderTraces(data);

    scorePill.hidden = false;
    scorePill.textContent = "anomaly " + data.anomaly.anomaly_score + "/100";
    severityPill.hidden = false;
    severityPill.textContent = "severity: " + (data.anomaly.severity || "low");
    severityPill.className = "pill-severity " + severityClass(data.anomaly.severity || "low");

    $("outRouter").textContent = fmt(data.router);
    $("outAnalyst").textContent = fmt(data.analyst);
    $("sarStructured").textContent = fmt({
      deterministic_evidence_count: (data.anomaly.evidence || []).length,
      ai_narrative_summary: data.analyst.risk_summary,
      analyst_notes: data.meta.review_notes,
      review_notice: data.meta.review_notice,
      reporter: data.reporter,
    });
    $("outSar").textContent = maskText(data.reporter.sar_body || "");
    $("outRaw").textContent = fmt(data);
    copySarStatus.textContent = "";
    bundleStatus.textContent = "";
  }

  function renderReplay(data) {
    lastResponse = null;
    emptyHint.style.display = "none";
    reviewBanner.hidden = false;
    clearNode(chips);
    chips.appendChild(makeEl("span", "chip", "replay_status: " + data.replay_status));
    chips.appendChild(makeEl("span", "chip" + (data.drift_detected ? " chip-danger" : ""), data.drift_detected ? "DRIFT" : "MATCH"));
    scorePill.hidden = true;
    severityPill.hidden = true;
    $("overviewMeta").textContent = "";
    $("whyFlagged").textContent = "Replay не пересобирает analyst workspace. Используйте hash checks и drift report.";
    $("overviewConfidence").textContent = "";
    $("validatorWhy").textContent = JSON.stringify(data.validator_summary || {}, null, 2);
    $("emergencyWhy").textContent = data.drift_detected ? "Обнаружен tamper или mismatch bundle." : "Целостность bundle подтверждена.";
    $("overviewText").textContent = fmt({ request_id: data.request_id, hash_checks: data.hash_checks, drift_report: data.drift_report });
    $("evidenceTable").textContent = "Replay mode: смотрите drift report в JSON / обзор.";
    $("txDrilldown").textContent = "Replay mode: transaction drilldown не загружен.";
    $("txRelated").textContent = "";
    $("traceContainer").textContent = fmt(data.hash_checks || []);
    $("outRouter").textContent = "";
    $("outAnalyst").textContent = "";
    $("sarStructured").textContent = "";
    $("outSar").textContent = "";
    $("outRaw").textContent = fmt(data);
    bundleStatus.textContent = data.drift_detected ? "Replay обнаружил tamper или deterministic drift." : "Replay подтвердил signed bundle.";
    activateTab("overview");
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
      if (!res.ok) {
        throw new Error((await res.text()).slice(0, 200));
      }
      await downloadResponse(res, "amber-case.zip");
      bundleStatus.textContent = "Signed case bundle экспортирован.";
    } catch (err) {
      showError("Ошибка экспорта bundle: " + (err.message || String(err)));
    } finally {
      setLoading(false);
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
      if (!res.ok) {
        throw new Error((await res.text()).slice(0, 200));
      }
      await downloadResponse(res, `amber-sar.${format === "markdown" ? "md" : format}`);
      bundleStatus.textContent = "SAR экспортирован в формате " + format + ".";
    } catch (err) {
      showError("Ошибка экспорта SAR: " + (err.message || String(err)));
    } finally {
      setLoading(false);
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
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }

  async function copySar() {
    if (!lastResponse || !lastResponse.reporter || !lastResponse.reporter.sar_body) {
      copySarStatus.textContent = "SAR body отсутствует.";
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
    selectedEvidenceIndex = 0;
    reviewBanner.hidden = true;
    scorePill.hidden = true;
    severityPill.hidden = true;
    clearNode(chips);
    ["outRouter", "outAnalyst", "outSar", "outRaw", "overviewText", "overviewConfidence", "sarStructured", "whyFlagged", "validatorWhy", "emergencyWhy", "csvReport", "csvColumns", "txDrilldown", "txRelated"].forEach((id) => {
      const el = $(id);
      if (el) el.textContent = "";
    });
    ["overviewMeta", "evidenceTable", "traceContainer", "csvPreviewTable", "csvMalformedTable"].forEach((id) => {
      const el = $(id);
      if (el) clearNode(el);
    });
    emptyHint.style.display = "block";
    copySarStatus.textContent = "";
    bundleStatus.textContent = "";
    if (pilotClientCopyStatus) pilotClientCopyStatus.textContent = "";
    showError("");
  }

  $("btnSend").addEventListener("click", sendJson);
  $("btnCsvPreview").addEventListener("click", previewCsvFromInput);
  $("btnAnalyzePreview").addEventListener("click", analyzePreview);
  $("btnExportBundle").addEventListener("click", exportBundle);
  $("btnExportSarTxt").addEventListener("click", () => exportSar("txt"));
  $("btnExportSarMd").addEventListener("click", () => exportSar("markdown"));
  $("btnExportSarDocx").addEventListener("click", () => exportSar("docx"));
  $("btnReplayBundle").addEventListener("click", replayBundle);
  $("btnCopySar").addEventListener("click", copySar);
  $("btnCopyPilotText").addEventListener("click", copyPilotClientText);
  $("btnSample").addEventListener("click", () => {
    payloadEl.value = JSON.stringify(SAMPLE, null, 2);
    lastSourceRequest = SAMPLE;
  });
  $("btnClear").addEventListener("click", clearOut);

  renderDemoLibrary();
  loadEnvironment();
})();
