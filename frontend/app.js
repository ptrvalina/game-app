const API_BASE = window.location.origin + "/api";
const tg = window.Telegram?.WebApp;
const initData = tg?.initData || "";

function initialGameFromUrl() {
  try {
    const params = new URLSearchParams(window.location.search);
    const g = params.get("game");
    if (["neon_snake", "match3_nova", "ludo_world"].includes(g)) return g;
  } catch {
    /* ignore */
  }
  return "neon_snake";
}

let score = 0;
let currentGameId = initialGameFromUrl();
let storeCatalog = [];
let games = [];
let currentConfig = null;
let streak = 0;
let roundStartedAt = 0;
let roundTimerInterval = null;
let combo = 1;
let audioCtx = null;
let bestScore = 0;
let particles = [];
let particleCtx = null;
let particleAnim = null;

function randomNonce() {
  return `${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

async function trackEvent(eventName, metadata = {}) {
  await fetch(`${API_BASE}/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      eventName,
      gameId: currentGameId,
      metadata,
    }),
  }).catch(() => {});
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.innerText = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 900);
}

function setupFxCanvas() {
  const canvas = document.getElementById("fxCanvas");
  if (!canvas) return;
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  canvas.width = Math.floor(window.innerWidth * dpr);
  canvas.height = Math.floor(window.innerHeight * dpr);
  canvas.style.width = `${window.innerWidth}px`;
  canvas.style.height = `${window.innerHeight}px`;
  particleCtx = canvas.getContext("2d");
  particleCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function spawnBurst(x, y, color = "#22d3ee", count = 14) {
  for (let i = 0; i < count; i += 1) {
    const angle = (Math.PI * 2 * i) / count + Math.random() * 0.4;
    const speed = 1 + Math.random() * 2.8;
    particles.push({
      x,
      y,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      life: 18 + Math.floor(Math.random() * 10),
      size: 2 + Math.random() * 3,
      color,
    });
  }
  if (!particleAnim) runParticles();
}

function runParticles() {
  if (!particleCtx) setupFxCanvas();
  particleAnim = requestAnimationFrame(runParticles);
  particleCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  particles = particles.filter((p) => p.life > 0);
  particles.forEach((p) => {
    p.x += p.vx;
    p.y += p.vy;
    p.vy += 0.05;
    p.life -= 1;
    particleCtx.globalAlpha = Math.max(0, p.life / 24);
    particleCtx.fillStyle = p.color;
    particleCtx.beginPath();
    particleCtx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
    particleCtx.fill();
  });
  if (particles.length === 0) {
    cancelAnimationFrame(particleAnim);
    particleAnim = null;
    particleCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  }
}

function playTone(freq, durationMs = 80, type = "sine", gainValue = 0.04) {
  if (!window.AudioContext && !window.webkitAudioContext) return;
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new Ctx();
  }
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  gain.gain.value = gainValue;
  osc.connect(gain);
  gain.connect(audioCtx.destination);
  osc.start();
  setTimeout(() => {
    osc.stop();
    osc.disconnect();
    gain.disconnect();
  }, durationMs);
}

function setThemeByGame(gameId) {
  document.body.classList.remove("theme-neon", "theme-match3", "theme-ludo");
  if (gameId === "neon_snake") document.body.classList.add("theme-neon");
  if (gameId === "match3_nova") document.body.classList.add("theme-match3");
  if (gameId === "ludo_world") document.body.classList.add("theme-ludo");
}

async function loadHub() {
  const res = await fetch(`${API_BASE}/games?wave=4`);
  const payload = await res.json();
  games = payload.data || [];
  renderHub();
  await loadGame(currentGameId);
}

function renderHub() {
  const select = document.getElementById("gameSelect");
  select.innerHTML = "";
  games.filter((g) => g.isFlagship).forEach((game) => {
    const opt = document.createElement("option");
    opt.value = game.id;
    opt.innerText = `${game.title} (${game.genre})`;
    if (game.id === currentGameId) opt.selected = true;
    select.appendChild(opt);
  });
  const countNode = document.getElementById("catalogCount");
  countNode.innerText = `Catalog: ${games.length} games | Flagships ready`;
}

function startRoundTimer() {
  clearInterval(roundTimerInterval);
  roundStartedAt = Date.now();
  roundTimerInterval = setInterval(() => {
    const sec = Math.max(0, Math.floor((Date.now() - roundStartedAt) / 1000));
    document.getElementById("roundTimer").innerText = `Round: ${sec}s`;
  }, 250);
}

function stopRoundTimer() {
  clearInterval(roundTimerInterval);
  roundTimerInterval = null;
}

async function loadGame(gameId) {
  currentGameId = gameId;
  score = 0;
  streak = 0;
  combo = 1;
  bestScore = 0;
  const res = await fetch(`${API_BASE}/games/${gameId}/config`);
  const game = await res.json();
  currentConfig = game;
  setThemeByGame(gameId);
  document.getElementById("title").innerText = game.title;
  document.getElementById("objective").innerText = game.objective;
  document.getElementById("currentScore").innerText = "Score: 0";
  document.getElementById("streak").innerText = "Streak: 0";
  document.getElementById("combo").innerText = "Combo: x1";
  document.getElementById("roundTimer").innerText = "Round: 0s";
  const summary = document.getElementById("roundSummary");
  summary.classList.remove("show");
  summary.innerHTML = "";
  startRoundTimer();

  const container = document.getElementById("game");
  container.innerHTML = "";
  if (game.gameId === "neon_snake") {
    for (let i = 0; i < 9; i += 1) {
      const cell = document.createElement("button");
      cell.className = "game-cell";
      cell.innerText = "Orb";
      cell.onclick = () => {
        score += Number(game.pointsPerHit || 15) * combo;
        streak += 1;
        combo = Math.min(6, combo + 1);
        cell.innerText = "Collected";
        cell.disabled = true;
        cell.classList.add("hit");
        const rect = cell.getBoundingClientRect();
        spawnBurst(rect.left + rect.width / 2, rect.top + rect.height / 2, "#22d3ee", 16);
        playTone(520 + combo * 30, 70, "triangle");
        showToast(`Neon hit +${Number(game.pointsPerHit || 15) * combo}`);
        renderScore();
      };
      container.appendChild(cell);
    }
  } else if (game.gameId === "match3_nova") {
    const symbols = ["A", "B", "C", "D", "E", "F"];
    for (let i = 0; i < 12; i += 1) {
      const cell = document.createElement("button");
      cell.className = "game-cell";
      const sym = symbols[Math.floor(Math.random() * symbols.length)];
      cell.innerText = sym;
      cell.onclick = () => {
        if (sym === "A" || sym === "B") {
          score += Number(game.pointsPerHit || 20) * combo;
          streak += 1;
          combo = Math.min(8, combo + 1);
          cell.classList.add("hit");
          const rect = cell.getBoundingClientRect();
          spawnBurst(rect.left + rect.width / 2, rect.top + rect.height / 2, "#c084fc", 14);
          playTone(660 + combo * 20, 80, "square");
          showToast(`Combo match x${combo}`);
        } else {
          streak = 0;
          combo = 1;
          cell.classList.add("fail");
          const rect = cell.getBoundingClientRect();
          spawnBurst(rect.left + rect.width / 2, rect.top + rect.height / 2, "#ef4444", 10);
          playTone(180, 120, "sawtooth");
          showToast("Combo broken");
        }
        renderScore();
      };
      container.appendChild(cell);
    }
  } else if (game.gameId === "ludo_world") {
    for (let i = 0; i < 8; i += 1) {
      const roll = document.createElement("button");
      roll.className = "game-cell";
      roll.innerText = "Roll Dice";
      roll.onclick = () => {
        const dice = 1 + Math.floor(Math.random() * 6);
        roll.innerText = `Dice ${dice}`;
        const gain = dice * 5 * combo;
        score += gain;
        if (dice >= 5) {
          streak += 1;
          combo = Math.min(5, combo + 1);
          roll.classList.add("hit");
          const rect = roll.getBoundingClientRect();
          spawnBurst(rect.left + rect.width / 2, rect.top + rect.height / 2, "#4ade80", 18);
          showToast(`Critical roll ${dice}! +${gain}`);
          playTone(420 + dice * 40, 90, "triangle");
        } else {
          streak = 0;
          combo = 1;
          roll.classList.add("fail");
          const rect = roll.getBoundingClientRect();
          spawnBurst(rect.left + rect.width / 2, rect.top + rect.height / 2, "#f97316", 10);
          showToast(`Roll ${dice}, hold position`);
          playTone(240 + dice * 10, 80, "sine");
        }
        renderScore();
      };
      container.appendChild(roll);
    }
  } else {
    const fallback = document.createElement("div");
    fallback.innerText = "This game is in content wave rollout.";
    container.appendChild(fallback);
  }
  await trackEvent("open_game", { gameId: currentGameId });
}

function renderScore() {
  document.getElementById("currentScore").innerText = `Score: ${score}`;
  document.getElementById("streak").innerText = `Streak: ${streak}`;
  document.getElementById("combo").innerText = `Combo: x${combo}`;
}

function simulateRoundBonus() {
  const elapsed = Math.max(1, Math.floor((Date.now() - roundStartedAt) / 1000));
  const speedBonus = Math.max(0, 25 - elapsed);
  const streakBonus = streak * 3 + combo * 2;
  score += speedBonus + streakBonus;
  bestScore = Math.max(bestScore, score);
  renderScore();
  playTone(780, 130, "triangle", 0.05);
  showToast(`Round complete +${speedBonus + streakBonus}`);
  const summary = document.getElementById("roundSummary");
  summary.innerHTML = `Round Summary: score ${score}, best ${bestScore}, combo x${combo}, time ${elapsed}s`;
  summary.classList.add("show");
  trackEvent("finish_round", { elapsedSec: elapsed, speedBonus, streakBonus, score });
}

async function sendScore() {
  stopRoundTimer();
  simulateRoundBonus();
  const res = await fetch(`${API_BASE}/score`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(initData ? { Authorization: `tma ${initData}` } : {}),
    },
    body: JSON.stringify({
      gameId: currentGameId,
      score,
      nonce: randomNonce(),
      initData,
    }),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ error: "unknown error" }));
    console.error("Score rejected", error.error);
  } else {
    await trackEvent("submit_score", { score });
    showToast("Score submitted to leaderboard");
  }
}

async function sendDailyScore() {
  stopRoundTimer();
  simulateRoundBonus();
  const res = await fetch(`${API_BASE}/games/${encodeURIComponent(currentGameId)}/daily-score`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(initData ? { Authorization: `tma ${initData}` } : {}),
    },
    body: JSON.stringify({
      score,
      nonce: randomNonce(),
      initData,
    }),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ error: "unknown error" }));
    console.error("Daily score rejected", error.error);
    return;
  }
  await trackEvent("submit_daily_score", { score });
  showToast("Daily challenge score submitted");
  await loadDailyLeaderboard();
}

async function loadLeaderboard() {
  const res = await fetch(`${API_BASE}/leaderboard?gameId=${encodeURIComponent(currentGameId)}&limit=10&offset=0`);
  const payload = await res.json();
  const data = payload.data || [];
  const lb = document.getElementById("leaderboard");
  lb.innerHTML = "";

  data.forEach((item, idx) => {
    const div = document.createElement("div");
    div.innerText = `${idx + 1}. ${item.username}: ${item.bestScore}`;
    lb.appendChild(div);
  });
}

async function loadDailyLeaderboard() {
  const res = await fetch(
    `${API_BASE}/games/${encodeURIComponent(currentGameId)}/daily-leaderboard?limit=10&offset=0`
  );
  const payload = await res.json();
  const data = payload.data || [];
  const lb = document.getElementById("dailyLeaderboard");
  lb.innerHTML = "";
  data.forEach((item, idx) => {
    const div = document.createElement("div");
    div.innerText = `${idx + 1}. ${item.username}: ${item.bestScore}`;
    lb.appendChild(div);
  });
}

function renderStore(balance, entitlements) {
  const wallet = document.getElementById("wallet");
  wallet.innerText = `Coins: ${balance} | Entitlements: ${entitlements.join(", ") || "none"}`;

  const store = document.getElementById("store");
  store.innerHTML = "";
  storeCatalog.forEach((item) => {
    const btn = document.createElement("button");
    btn.innerText = `Buy ${item.title} (${item.priceCoins})`;
    btn.onclick = () => purchase(item.sku);
    store.appendChild(btn);
  });
}

async function loadStoreState() {
  const [catalogRes, balanceRes] = await Promise.all([
    fetch(`${API_BASE}/store/catalog?gameId=${encodeURIComponent(currentGameId)}`),
    fetch(`${API_BASE}/store/balance`, {
      headers: initData ? { Authorization: `tma ${initData}` } : {},
    }),
  ]);
  const catalogPayload = await catalogRes.json();
  const balancePayload = balanceRes.ok ? await balanceRes.json() : { balance: 0, entitlements: [] };
  storeCatalog = catalogPayload.data || [];
  renderStore(balancePayload.balance || 0, balancePayload.entitlements || []);
}

async function claimDaily() {
  const res = await fetch(`${API_BASE}/store/claim-daily`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(initData ? { Authorization: `tma ${initData}` } : {}),
    },
    body: JSON.stringify({ initData, gameId: currentGameId }),
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({ error: "claim failed" }));
    console.error(payload.error);
  }
  await loadStoreState();
  await trackEvent("claim_daily", { gameId: currentGameId });
}

async function purchase(sku) {
  const res = await fetch(`${API_BASE}/store/purchase`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(initData ? { Authorization: `tma ${initData}` } : {}),
    },
    body: JSON.stringify({ sku, initData, gameId: currentGameId }),
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({ error: "purchase failed" }));
    console.error(payload.error);
  }
  await loadStoreState();
  await trackEvent("buy_item", { sku, gameId: currentGameId });
}

setInterval(loadLeaderboard, 3000);
document.getElementById("claimDailyBtn").onclick = () => claimDaily();
document.getElementById("playRoundBtn").onclick = async () => {
  await loadGame(currentGameId);
  await trackEvent("start_round", { gameId: currentGameId, mode: currentConfig?.type });
  showToast("New round started");
};
document.getElementById("submitScoreBtn").onclick = () => sendScore();
document.getElementById("submitDailyBtn").onclick = () => sendDailyScore();
document.getElementById("gameSelect").onchange = async (event) => {
  await loadGame(event.target.value);
  await loadLeaderboard();
  await loadDailyLeaderboard();
  await loadStoreState();
};
window.addEventListener("resize", setupFxCanvas);
setupFxCanvas();
loadHub()
  .then(loadLeaderboard)
  .then(loadDailyLeaderboard)
  .then(loadStoreState)
  .catch((error) => console.error(error));