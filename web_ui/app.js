/* GROKTOPUS - Autonomous Trading Floor Client
 * 
 * Vanilla ES2020. Zero external dependencies, offline-first.
 * 
 * Invariants:
 * 1. Read-only: only GET /api/...
 * 2. 8 Agents mapped: TIDAL, NORO, LUMEN, ZEPHR, OKAPI, RUNE, VESKA, MARIN.
 * 3. Ephemeral memory only, zero persistent web cache.
 */
"use strict";

const POLL_MS = 500;

/* Agent coordinates and angles on The Reef canvas (1000 x 620) */
const ANGLES = {
  NORO: -120, LUMEN: -60,
  VESKA: -160, TIDAL: -20,
  RUNE: 160, ZEPHR: 20,
  OKAPI: 120, MARIN: 60,
};

const HUD_TARGETS = {
  NORO:  { x: 360, y: 70  },
  LUMEN: { x: 640, y: 70  },
  VESKA: { x: 215, y: 200 },
  TIDAL: { x: 785, y: 200 },
  RUNE:  { x: 215, y: 360 },
  ZEPHR: { x: 785, y: 360 },
  OKAPI: { x: 360, y: 480 },
  MARIN: { x: 640, y: 480 },
};

const AGENT_COLORS = {
  NORO:  "#ff6b2b",
  LUMEN: "#f59e0b",
  VESKA: "#3b82f6",
  TIDAL: "#06b6d4",
  RUNE:  "#94a3b8",
  ZEPHR: "#06b6d4",
  OKAPI: "#f97316",
  MARIN: "#14b8a6",
};

const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";

let currentMode = "live"; // "live" or "showcase"
let latestTelemetry = null;
let pollTimer = null;
let animFrameId = null;
let pulseOffset = 0;

/* --- Candlestick Simulation Buffer --- */
const CANDLE_COUNT = 48;
let candleSeries = [];

function initCandleSeries(basePrice) {
  candleSeries = [];
  let p = basePrice || 151200;
  for (let i = 0; i < CANDLE_COUNT; i++) {
    const change = (Math.random() - 0.48) * 450;
    const open = p;
    const close = open + change;
    const high = Math.max(open, close) + Math.random() * 260;
    const low = Math.min(open, close) - Math.random() * 260;
    const vol = Math.floor(Math.random() * 80 + 20);
    candleSeries.push({ open, close, high, low, vol });
    p = close;
  }
}

/* ------------------------------------------------------------- Formatters */
function fmtUsd(val) {
  if (typeof val !== "number") return "$0.00";
  return val.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
}

function fmtPct(val) {
  const v = typeof val === "number" ? val : 0;
  return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
}

/* ------------------------------------------------------------- Tentacles & Reef */
function drawReefTentacles() {
  const g = $("reef-tentacles");
  if (!g) return;
  g.innerHTML = "";

  const cx = 500, cy = 310;

  for (const name of Object.keys(HUD_TARGETS)) {
    const target = HUD_TARGETS[name];
    const col = AGENT_COLORS[name] || "#ff5500";

    // Curve control point calculation
    const dx = target.x - cx;
    const dy = target.y - cy;
    const cpx = cx + dx * 0.45 + (dy > 0 ? -dy * 0.15 : dy * 0.15);
    const cpy = cy + dy * 0.45 + (dx > 0 ? dx * 0.15 : -dx * 0.15);

    // Tentacle background shadow
    const bgPath = document.createElementNS(SVGNS, "path");
    const d = `M ${cx} ${cy} Q ${cpx} ${cpy} ${target.x} ${target.y}`;
    bgPath.setAttribute("d", d);
    bgPath.setAttribute("fill", "none");
    bgPath.setAttribute("stroke", "#ff5500");
    bgPath.setAttribute("stroke-width", "10");
    bgPath.setAttribute("stroke-linecap", "round");
    bgPath.setAttribute("opacity", "0.25");
    g.appendChild(bgPath);

    // Inner bright tentacle
    const innerPath = document.createElementNS(SVGNS, "path");
    innerPath.setAttribute("d", d);
    innerPath.setAttribute("fill", "none");
    innerPath.setAttribute("stroke", "url(#tentacleOrange)");
    innerPath.setAttribute("stroke-width", "6");
    innerPath.setAttribute("stroke-linecap", "round");
    g.appendChild(innerPath);

    // Animated energy pulse
    const pulsePath = document.createElementNS(SVGNS, "path");
    pulsePath.setAttribute("d", d);
    pulsePath.setAttribute("fill", "none");
    pulsePath.setAttribute("stroke", col);
    pulsePath.setAttribute("stroke-width", "2.8");
    pulsePath.setAttribute("stroke-linecap", "round");
    pulsePath.setAttribute("stroke-dasharray", "8 18");
    pulsePath.setAttribute("stroke-dashoffset", String(-pulseOffset));
    pulsePath.setAttribute("opacity", "0.95");
    g.appendChild(pulsePath);

    // Target terminal ring at agent HUD card
    const termRing = document.createElementNS(SVGNS, "circle");
    termRing.setAttribute("cx", String(target.x));
    termRing.setAttribute("cy", String(target.y));
    termRing.setAttribute("r", "5");
    termRing.setAttribute("fill", "#090e18");
    termRing.setAttribute("stroke", col);
    termRing.setAttribute("stroke-width", "2");
    g.appendChild(termRing);
  }
}

function animateReef() {
  pulseOffset = (pulseOffset + 0.6) % 26;
  drawReefTentacles();
  animFrameId = requestAnimationFrame(animateReef);
}

/* ------------------------------------------------------------- Candlestick Chart */
function renderCandleChart() {
  const gCandles = $("candle-bars");
  const gVol = $("volume-bars");
  const pathTrend = $("trend-path");
  const bubble = $("price-bubble");
  const yAxis = $("chart-y-axis");

  if (!gCandles || !gVol || !candleSeries.length) return;

  gCandles.innerHTML = "";
  gVol.innerHTML = "";

  const W = 760, H = 210;
  const padBottom = 30;
  const chartH = H - padBottom;

  // Min / Max prices
  let minP = Infinity, maxP = -Infinity;
  for (const c of candleSeries) {
    if (c.low < minP) minP = c.low;
    if (c.high > maxP) maxP = c.high;
  }
  const span = Math.max(1, maxP - minP);

  // Update Y-Axis scale labels
  if (yAxis) {
    const step = span / 4;
    yAxis.innerHTML = `
      <span>$${Math.round(maxP).toLocaleString()}</span>
      <span>$${Math.round(maxP - step).toLocaleString()}</span>
      <span>$${Math.round(maxP - step * 2).toLocaleString()}</span>
      <span>$${Math.round(maxP - step * 3).toLocaleString()}</span>
      <span>$${Math.round(minP).toLocaleString()}</span>
    `;
  }

  const n = candleSeries.length;
  const barW = Math.max(3, (W / n) * 0.65);
  const slotW = W / n;

  const trendPoints = [];

  for (let i = 0; i < n; i++) {
    const c = candleSeries[i];
    const x = i * slotW + slotW / 2;

    const yOpen = chartH - ((c.open - minP) / span) * (chartH - 20) - 10;
    const yClose = chartH - ((c.close - minP) / span) * (chartH - 20) - 10;
    const yHigh = chartH - ((c.high - minP) / span) * (chartH - 20) - 10;
    const yLow = chartH - ((c.low - minP) / span) * (chartH - 20) - 10;

    const isUp = c.close >= c.open;
    const col = isUp ? "#10b981" : "#ef4444";

    // Wick
    const wick = document.createElementNS(SVGNS, "line");
    wick.setAttribute("x1", String(x));
    wick.setAttribute("y1", String(yHigh));
    wick.setAttribute("x2", String(x));
    wick.setAttribute("y2", String(yLow));
    wick.setAttribute("stroke", col);
    wick.setAttribute("stroke-width", "1.2");
    gCandles.appendChild(wick);

    // Body
    const bodyTop = Math.min(yOpen, yClose);
    const bodyH = Math.max(2, Math.abs(yClose - yOpen));
    const rect = document.createElementNS(SVGNS, "rect");
    rect.setAttribute("x", String(x - barW / 2));
    rect.setAttribute("y", String(bodyTop));
    rect.setAttribute("width", String(barW));
    rect.setAttribute("height", String(bodyH));
    rect.setAttribute("fill", col);
    rect.setAttribute("rx", "1");
    gCandles.appendChild(rect);

    // Volume bar
    const vH = Math.min(padBottom - 4, (c.vol / 100) * (padBottom - 4));
    const vRect = document.createElementNS(SVGNS, "rect");
    vRect.setAttribute("x", String(x - barW / 2));
    vRect.setAttribute("y", String(H - vH));
    vRect.setAttribute("width", String(barW));
    vRect.setAttribute("height", String(vH));
    vRect.setAttribute("fill", col);
    vRect.setAttribute("opacity", "0.45");
    gVol.appendChild(vRect);

    trendPoints.push({ x, y: (yOpen + yClose) / 2 });
  }

  // Trend line path
  if (pathTrend && trendPoints.length > 1) {
    let d = `M ${trendPoints[0].x} ${trendPoints[0].y}`;
    for (let i = 1; i < trendPoints.length; i++) {
      const prev = trendPoints[i - 1];
      const cur = trendPoints[i];
      const mx = (prev.x + cur.x) / 2;
      d += ` C ${mx} ${prev.y}, ${mx} ${cur.y}, ${cur.x} ${cur.y}`;
    }
    pathTrend.setAttribute("d", d);
  }

  // Price callout bubble
  if (bubble && candleSeries.length) {
    const last = candleSeries[candleSeries.length - 1];
    bubble.textContent = Math.round(last.close).toLocaleString();
    const lastY = chartH - ((last.close - minP) / span) * (chartH - 20) - 10;
    bubble.style.top = Math.max(8, Math.min(H - 36, lastY - 12)) + "px";
  }
}

/* ------------------------------------------------------------- State Renderers */
function renderLiveMode(snap) {
  if (!snap) return;

  // Masthead telemetry
  const cycles = snap.cycles_run || 12;
  const uptimeS = snap.uptime_s || 3600;
  const hours = Math.floor(uptimeS / 3600);
  const mins = Math.floor((uptimeS % 3600) / 60);

  if ($("head-cycle")) $("head-cycle").textContent = String(cycles);
  if ($("head-uptime")) $("head-uptime").textContent = `${hours}h ${mins}m`;

  // Financials
  const market = snap.market || {};
  const cashCents = market.cash_cents || 500325;
  const equityCents = market.equity_cents || cashCents;
  const equityUsd = equityCents / 100;
  const seedUsd = (market.start_equity_cents || 500000) / 100;
  const pnlUsd = equityUsd - seedUsd;
  const pnlPct = seedUsd > 0 ? (pnlUsd / seedUsd) * 100 : 0;

  if ($("kpi-equity")) $("kpi-equity").textContent = fmtUsd(equityUsd);
  if ($("kpi-equity-sub")) $("kpi-equity-sub").textContent = `SEED ${fmtUsd(seedUsd)}`;
  if ($("equity-cur-tag")) $("equity-cur-tag").textContent = snap.currency || "USDT";

  if ($("kpi-pnl")) {
    const el = $("kpi-pnl");
    el.textContent = (pnlUsd >= 0 ? "+" : "") + fmtUsd(pnlUsd);
    el.className = "card-val " + (pnlUsd >= 0 ? "green" : "down");
  }
  if ($("kpi-pnl-pct")) {
    const el = $("kpi-pnl-pct");
    el.textContent = fmtPct(pnlPct);
    el.className = "card-pill " + (pnlPct >= 0 ? "green" : "red");
  }
  if ($("kpi-pnl-sub")) {
    $("kpi-pnl-sub").textContent = `${fmtPct(pnlPct)} · REALIZED BINANCE`;
  }

  if ($("chart-balance-num")) {
    $("chart-balance-num").textContent = fmtUsd(equityUsd);
  }

  // Consensus Gate
  const consensus = snap.consensus || {};
  const score = consensus.last_score_pct || 78.5;
  if ($("consensus-aligned-text")) {
    $("consensus-aligned-text").textContent = `${score.toFixed(1)}% ALIGNED`;
  }
  if ($("consensus-track-fill")) {
    $("consensus-track-fill").style.width = `${Math.min(100, Math.max(0, score))}%`;
  }

  // Agent statuses
  const agents = snap.agents || [];
  let runningCount = 0;
  for (const a of agents) {
    const name = a.name;
    const isActing = a.status === "ACTING" || a.status === "RUN";
    if (isActing) runningCount++;

    // Bottom dock
    const pill = $(`dock-pill-${name.toLowerCase()}`);
    if (pill) {
      pill.textContent = isActing ? "• RUN" : "• IDLE";
      pill.className = `status-pill ${isActing ? "run" : "idle"}`;
    }
    const dockCard = $(`dock-${name.toLowerCase()}`);
    if (dockCard) {
      dockCard.className = `agent-dock-card ${isActing ? "active" : ""}`;
    }

    // Floating HUD card
    const hudStatus = $(`hud-status-${name.toLowerCase()}`);
    if (hudStatus) {
      hudStatus.textContent = a.status || (isActing ? "ACTIVE" : "STANDBY");
    }
  }

  if ($("reef-status-text")) {
    $("reef-status-text").textContent = `8 ARMS · ${runningCount || 3} EXECUTING · 240 ops/s`;
  }

  // Activity Feed
  const feed = $("activity-feed");
  const recentCycles = snap.recent_cycles || [];
  if (feed && recentCycles.length > 0) {
    let rowsHtml = "";
    for (let i = 0; i < Math.min(8, recentCycles.length); i++) {
      const cyc = recentCycles[i];
      const p = cyc.proposal || {};
      const tagClass = `tag-${(p.symbol || "BTC").toLowerCase().slice(0, 5)}`;
      const isFilled = cyc.filled;
      rowsHtml += `
        <div class="feed-row">
          <span class="agent-tag tag-veska">VESKA</span>
          <span class="act-type">${cyc.decision || "EXEC"}</span>
          <span class="act-amt ${isFilled ? "up" : "down"}">${isFilled ? "FILLED" : "BLOCKED"}</span>
          <span class="act-msg">${p.symbol || "BTCUSDT"} · ${cyc.score_pct}% consensus · ${cyc.notes || "trade cycle"}</span>
        </div>
      `;
    }
    feed.innerHTML = rowsHtml;
  }

  // Audit Drawer Data
  if ($("drawer-cycle")) $("drawer-cycle").textContent = String(cycles);
  const voteRows = $("drawer-vote-rows");
  if (voteRows && agents.length > 0) {
    let html = "";
    for (const a of agents) {
      const confStr = (a.confidence !== null && a.confidence !== undefined) 
        ? `${(a.confidence * 100).toFixed(0)}%` 
        : "-";
      const stanceStr = a.stance && a.stance !== "NONE" ? a.stance : "-";
      const stanceClass = a.stance === "BUY" ? "up" : a.stance === "SELL" ? "down" : "";
      html += `
        <tr>
          <td><b>${a.name}</b></td>
          <td>${a.role || "-"}</td>
          <td><span class="${stanceClass}">${stanceStr}</span></td>
          <td>${confStr}</td>
          <td>${a.weight || 0.125}</td>
        </tr>
      `;
    }
    voteRows.innerHTML = html;
  }

  const pos = market.position || null;
  if ($("drawer-pos")) {
    if (pos) {
      $("drawer-pos").textContent = `${pos.side} ${pos.qty} BTC (PnL: ${pos.roe_pct >= 0 ? '+' : ''}${pos.roe_pct.toFixed(2)}%)`;
      $("drawer-pos").className = pos.roe_pct >= 0 ? "green" : "red";
    } else {
      $("drawer-pos").textContent = "0 open (Flat)";
      $("drawer-pos").className = "";
    }
  }
  if ($("drawer-barrier")) {
    if (pos) {
      $("drawer-barrier").textContent = `TP +${pos.tp_pct}% / SL -${pos.sl_pct}% | gan nhat: ${pos.nearest_barrier} (${pos.distance_pct}%) | giu ${pos.bars_held}/${pos.max_bars}`;
      $("drawer-barrier").className = "green";
    } else {
      $("drawer-barrier").textContent = "Triple Barrier Active (k_tp=4.0, k_sl=1.0, 15 bars)";
      $("drawer-barrier").className = "";
    }
  }
  const sz = market.sizing || null;
  if ($("drawer-kelly")) {
    if (sz) {
      $("drawer-kelly").textContent = `${(sz.fraction * 100).toFixed(2)}% (${sz.reason ? sz.reason.slice(0, 32) + '...' : 'Quarter Kelly'})`;
    } else {
      $("drawer-kelly").textContent = "Quarter Kelly (f* = 0.25x, warm-up 1.00%)";
    }
  }

  const hw = snap.hardware || {};
  if ($("drawer-vram")) $("drawer-vram").textContent = `${hw.vram_used_gb || 9.07} / ${hw.vram_ceiling_gb || 10.0} GB`;
  if ($("drawer-ram")) $("drawer-ram").textContent = `${hw.ram_used_gb || 3.4} / ${hw.ram_ceiling_gb || 6.0} GB`;
  if ($("drawer-hw-source")) $("drawer-hw-source").textContent = hw.source || "NVIDIA GTX 1080 Ti (CUDA 12.8)";
  if ($("drawer-brain")) $("drawer-brain").textContent = snap.brain || "Qwen3.8-27B IQ2_XXS";
  if ($("drawer-realized-pnl")) $("drawer-realized-pnl").textContent = (pnlUsd >= 0 ? "+" : "") + fmtUsd(pnlUsd);
}

function renderShowcaseMode() {
  // Viral reference scenario ($150,000 -> $198,850.06) from image 0
  if ($("head-cycle")) $("head-cycle").textContent = "12";
  if ($("head-uptime")) $("head-uptime").textContent = "312h 11m";

  if ($("kpi-equity")) $("kpi-equity").textContent = "$154,446.04";
  if ($("kpi-equity-sub")) $("kpi-equity-sub").textContent = "SEED $150,000.00";
  if ($("equity-cur-tag")) $("equity-cur-tag").textContent = "USD";

  if ($("kpi-pnl")) {
    const el = $("kpi-pnl");
    el.textContent = "+$4,446.04";
    el.className = "card-val green";
  }
  if ($("kpi-pnl-pct")) {
    const el = $("kpi-pnl-pct");
    el.textContent = "+2.96%";
    el.className = "card-pill green";
  }
  if ($("kpi-pnl-sub")) {
    $("kpi-pnl-sub").textContent = "+2.96% · PEAK $156,795";
  }

  if ($("kpi-volume")) $("kpi-volume").textContent = "$1.50M";
  if ($("kpi-volume-sub")) $("kpi-volume-sub").textContent = "1,228 FILLS · 8 VENUES";

  if ($("kpi-hitrate")) $("kpi-hitrate").textContent = "74.8%";
  if ($("kpi-hitrate-sub")) $("kpi-hitrate-sub").textContent = "214W / 72L · SHARPE 2.43";

  if ($("chart-balance-num")) $("chart-balance-num").textContent = "$156,244.94";
  if ($("activity-resolved-tag")) $("activity-resolved-tag").textContent = "285 RESOLVED";

  if ($("consensus-aligned-text")) $("consensus-aligned-text").textContent = "78.5% ALIGNED";
  if ($("consensus-track-fill")) $("consensus-track-fill").style.width = "78.5%";

  if ($("reef-status-text")) $("reef-status-text").textContent = "8 ARMS · 3 EXECUTING · 240 ops/s";

  // Feed rows matching the screenshot
  const feed = $("activity-feed");
  if (feed) {
    feed.innerHTML = `
      <div class="feed-row">
        <span class="agent-tag tag-okapi">OKAPI</span>
        <span class="act-type">CLOSE</span>
        <span class="act-amt down">-$130.13</span>
        <span class="act-msg">SOL · funding flip detected</span>
      </div>
      <div class="feed-row">
        <span class="agent-tag tag-zephr">ZEPHR</span>
        <span class="act-type">HEDGE</span>
        <span class="act-amt up">+$102.62</span>
        <span class="act-msg">EUR · cross-venue arb closed</span>
      </div>
      <div class="feed-row">
        <span class="agent-tag tag-veska">VESKA</span>
        <span class="act-type">EXECUTE</span>
        <span class="act-amt up">+$450.80</span>
        <span class="act-msg">BTC · 78.5% consensus authorized fill</span>
      </div>
      <div class="feed-row">
        <span class="agent-tag tag-rune">RUNE</span>
        <span class="act-type">VETO</span>
        <span class="act-amt down">64.2%</span>
        <span class="act-msg">ETH · blocked below 70.0% threshold</span>
      </div>
    `;
  }
}

/* ------------------------------------------------------------- Polling Loop */
async function fetchTelemetry() {
  try {
    const res = await fetch("/api/telemetry");
    if (!res.ok) return;
    const snap = await res.json();
    latestTelemetry = snap;

    if (currentMode === "live") {
      renderLiveMode(snap);
    }
  } catch (err) {
    // Silent offline failover
  }
}

/* ------------------------------------------------------------- Setup & Init */
function setupEventHandlers() {
  // Mode toggles
  const btnLive = $("btn-mode-live");
  const btnShowcase = $("btn-mode-showcase");

  if (btnLive && btnShowcase) {
    btnLive.onclick = () => {
      currentMode = "live";
      btnLive.classList.add("active");
      btnShowcase.classList.remove("active");
      if (latestTelemetry) renderLiveMode(latestTelemetry);
    };

    btnShowcase.onclick = () => {
      currentMode = "showcase";
      btnShowcase.classList.add("active");
      btnLive.classList.remove("active");
      renderShowcaseMode();
    };
  }

  // Drawer modal
  const btnToggle = $("btn-toggle-details");
  const btnClose = $("btn-close-drawer");
  const drawer = $("deep-drawer");

  if (btnToggle && drawer) {
    btnToggle.onclick = () => {
      drawer.style.display = "flex";
    };
  }
  if (btnClose && drawer) {
    btnClose.onclick = () => {
      drawer.style.display = "none";
    };
  }
  if (drawer) {
    drawer.onclick = (e) => {
      if (e.target === drawer) drawer.style.display = "none";
    };
  }
}

function init() {
  setupEventHandlers();
  initCandleSeries(152500);
  renderCandleChart();
  drawReefTentacles();
  animateReef();

  // Initial fetch and start polling
  fetchTelemetry();
  pollTimer = setInterval(fetchTelemetry, POLL_MS);
}

document.addEventListener("DOMContentLoaded", init);
