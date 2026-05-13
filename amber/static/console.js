/**
 * Веб-консоль Amber: вызов POST /api/v1/analyze и отрисовка вкладок.
 */
(function () {
  const SAMPLE = {
    mode: "fiat",
    jurisdiction: "RB",
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

  const $ = (id) => document.getElementById(id);
  const loading = $("loading");
  const payloadEl = $("payload");
  const apiKeyEl = $("apiKey");
  const errorBox = $("errorBox");
  const emptyHint = $("emptyHint");
  const scorePill = $("scorePill");
  const chips = $("chips");

  const STORAGE_KEY = "amber_console_api_key";
  apiKeyEl.value = localStorage.getItem(STORAGE_KEY) || "";
  apiKeyEl.addEventListener("change", () => localStorage.setItem(STORAGE_KEY, apiKeyEl.value.trim()));

  payloadEl.value = JSON.stringify(SAMPLE, null, 2);

  function setLoading(on) {
    loading.classList.toggle("show", on);
    loading.setAttribute("aria-hidden", on ? "false" : "true");
    $("btnSend").disabled = on;
  }

  function showError(msg) {
    errorBox.textContent = msg || "";
  }

  function uuid() {
    return crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
  }

  function activateTab(name) {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === name);
    });
    document.querySelectorAll(".tab-panel").forEach((p) => {
      p.classList.toggle("active", p.dataset.panel === name);
    });
  }

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });

  function fmt(obj) {
    return JSON.stringify(obj, null, 2);
  }

  function render(data) {
    emptyHint.style.display = "none";
    scorePill.hidden = false;
    scorePill.textContent = "anomaly " + data.anomaly.anomaly_score + "/100";

    chips.innerHTML = "";
    const c = [
      "mode: " + data.mode,
      "jurisdiction: " + data.jurisdiction,
      "LLM: " + data.meta.llm_used,
      data.meta.emergency_mode ? "emergency" : "live",
      data.meta.fallback_used ? "fallback" : "primary",
    ];
    c.forEach((t) => {
      const s = document.createElement("span");
      s.className = "chip";
      s.textContent = t;
      chips.appendChild(s);
    });

    const meta = $("overviewMeta");
    meta.innerHTML = "";
    const tiles = [
      ["request_id", data.meta.request_id || "—"],
      ["anomaly_score", data.anomaly.anomaly_score],
      ["llm_used", data.meta.llm_used],
      ["latency_ms", [data.meta.latency_ms_router, data.meta.latency_ms_analyst, data.meta.latency_ms_reporter].filter(Boolean).join(" / ") || "—"],
    ];
    tiles.forEach(([k, v]) => {
      const dl = document.createElement("div");
      dl.className = "meta-tile";
      dl.innerHTML = "<dt>" + k + "</dt><dd>" + String(v) + "</dd>";
      meta.appendChild(dl);
    });

    $("overviewText").textContent =
      data.analyst.risk_summary + "\n\n—\n\n" + (data.anomaly.reasons || []).map((r) => "• " + r).join("\n");

    $("outRouter").textContent = fmt(data.router);
    $("outAnalyst").textContent = fmt(data.analyst);
    $("outSar").textContent = data.reporter.sar_title + "\n\n" + data.reporter.sar_body + "\n\n—\n" + data.reporter.sar_disclaimer;
    $("outRaw").textContent = fmt(data);
  }

  async function send() {
    showError("");
    let body;
    try {
      body = JSON.parse(payloadEl.value);
    } catch (e) {
      showError("Некорректный JSON: " + e.message);
      return;
    }

    const headers = { "Content-Type": "application/json", "X-Request-ID": uuid() };
    const key = apiKeyEl.value.trim();
    if (key) headers["X-Api-Key"] = key;

    setLoading(true);
    try {
      const res = await fetch("/api/v1/analyze", { method: "POST", headers, body: JSON.stringify(body) });
      const text = await res.text();
      let json;
      try {
        json = JSON.parse(text);
      } catch {
        throw new Error("Ответ не JSON: " + text.slice(0, 200));
      }
      if (!res.ok) {
        const msg = json.error?.message || json.detail || res.statusText;
        showError(res.status + ": " + msg);
        return;
      }
      render(json);
      activateTab("overview");
    } catch (e) {
      showError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  $("btnSend").addEventListener("click", send);
  $("btnSample").addEventListener("click", () => {
    payloadEl.value = JSON.stringify(SAMPLE, null, 2);
  });
  $("btnClear").addEventListener("click", () => {
    $("outRouter").textContent = "";
    $("outAnalyst").textContent = "";
    $("outSar").textContent = "";
    $("outRaw").textContent = "";
    $("overviewText").textContent = "";
    $("overviewMeta").innerHTML = "";
    chips.innerHTML = "";
    scorePill.hidden = true;
    emptyHint.style.display = "block";
    showError("");
  });
})();
