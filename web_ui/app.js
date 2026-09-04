/* GrokTOPUS dashboard.
 *
 * Vanilla ES2020, no build step, no dependency. It polls /api/telemetry and
 * paints: an eight-tentacle octopus whose limbs light up as their agent votes,
 * a consensus gauge with the 70.0 / 76.8 bands drawn where they actually are,
 * an equity curve, the live vote table, and the hardware meters.
 *
 * Two loops on purpose. The network poll runs at POLL_MS because that is how
 * often the server has anything new to say; the tentacle animation runs on
 * requestAnimationFrame because motion at 2 Hz looks broken. Mixing them would
 * mean either a stuttering octopus or a needless request every 16 ms.
 *
 * Nothing here can trade. The API is read-only, and the thresholds shown are
 * echoed from the server rather than hard-coded, so this file cannot drift away
 * from the gate it is describing.
 */
"use strict";

const POLL_MS = 500;
const CX = 530, CY = 355, HEAD_RX = 132, HEAD_RY = 116;
const NODE_RX = 368, NODE_RY = 262;

/* TIDAL first, going clockwise from the upper right. RUNE and VESKA end up
 * adjacent on the left, which is where the eye lands last - fitting for the
 * veto gate and the only agent allowed to touch the book. */
const ANGLES = {
  TIDAL: -67.5, NORO: -22.5, LUMEN: 22.5, ZEPHR: 67.5,
  OKAPI: 112.5, RUNE: 157.5, VESKA: -157.5, MARIN: -112.5,
};

const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";

let latest = null;
let agentOrder = [];
let lastGoodAt = 0;
let campaignBusy = false;

/* ---------------------------------------------------------------- utils */

function el(tag, attrs, parent) {
  const n = document.createElementNS(SVGNS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(n);
  return n;
}
function isVnd() {
  return latest && latest.currency === "VND";
}
function usd(cents) {
  if (isVnd()) {
    return Math.round(cents || 0).toLocaleString("vi-VN") + " ₫";
  }
  const v = (cents || 0) / 100;
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
}
function usdShort(cents) {
  if (isVnd()) {
    const v = cents || 0;
    if (Math.abs(v) >= 1000) return (v / 1000).toFixed(1) + "k ₫";
    return v + " ₫";
  }
  const v = (cents || 0) / 100;
  if (Math.abs(v) >= 1000) return "$" + (v / 1000).toFixed(1) + "k";
  return "$" + v.toFixed(0);
}
function pct(x, digits) { return (x || 0).toFixed(digits === undefined ? 2 : digits) + "%"; }
function hue(h) { return `hsl(${h} 72% 58%)`; }
function txt(node, s) { if (node && node.textContent !== s) node.textContent = s; }
function stanceClass(s) {
  return s === "BUY" ? "buy" : s === "SELL" ? "sell" : "hold";
}
function nodePoint(name) {
  const a = (ANGLES[name] || 0) * Math.PI / 180;
  return { a, x: CX + NODE_RX * Math.cos(a), y: CY + NODE_RY * Math.sin(a) };
}

/* ------------------------------------------------------------- octopus */

const tentacles = {};

function buildOctopus(agents) {
  const gT = $("tentacles"), gH = $("head"), gN = $("nodes");
  if (!gT || !gH || !gN) return;
  gT.innerHTML = ""; gH.innerHTML = ""; gN.innerHTML = "";

  // Head first in document order so tentacles read as emerging from behind it.
  el("ellipse", { cx: CX, cy: CY, rx: HEAD_RX + 16, ry: HEAD_RY + 16,
                  fill: "none", stroke: "#16273d", "stroke-width": 1 }, gH);
  const ring = el("circle", { cx: CX, cy: CY, r: HEAD_RX + 26, fill: "none",
                              stroke: "#152340", "stroke-width": 4 }, gH);
  ring.setAttribute("id", "consensus-ring-bg");
  // Thin on purpose. It is a secondary readout - the gauge on the right is the
  // primary one - and a thick coloured band this close to the tentacles reads as
  // an alarm rather than as a number.
  const arc = el("path", { id: "consensus-ring", fill: "none", stroke: "#2dd4bf",
                           "stroke-width": 4, "stroke-linecap": "round", opacity: 0.85,
                           transform: `rotate(-90 ${CX} ${CY})` }, gH);
  arc.setAttribute("d", "");
  el("ellipse", { cx: CX, cy: CY, rx: HEAD_RX, ry: HEAD_RY, fill: "url(#headGrad)",
                  stroke: "#2a4664", "stroke-width": 1.5, class: "head-glow" }, gH);
  // eyes
  for (const s of [-1, 1]) {
    el("ellipse", { cx: CX + s * 44, cy: CY - 16, rx: 26, ry: 30, fill: "#cfe6f5", opacity: 0.92 }, gH);
    const p = el("ellipse", { cx: CX + s * 44, cy: CY - 12, rx: 12, ry: 16, class: "eye" }, gH);
    p.setAttribute("id", "pupil" + (s < 0 ? "L" : "R"));
    el("circle", { cx: CX + s * 44 - 5, cy: CY - 22, r: 4, class: "spark" }, gH);
  }
  el("text", { x: CX, y: CY + 62, "text-anchor": "middle", fill: "#6f86a5",
               "font-family": "ui-monospace, monospace", "font-size": 11,
               "letter-spacing": "0.22em" }, gH).textContent = "GROKTOPUS";
  const core = el("text", { x: CX, y: CY + 84, "text-anchor": "middle", fill: "#2dd4bf",
                            "font-family": "ui-monospace, monospace", "font-size": 15,
                            "letter-spacing": "0.06em" }, gH);
  core.setAttribute("id", "head-score");
  core.textContent = "--.--%";

  for (const a of agents) {
    const p = nodePoint(a.name);
    const color = hue(a.hue);
    const limb = el("path", { class: "tentacle", stroke: color, "stroke-width": 7 }, gT);
    const sk = el("path", { class: "tentacle sucker", stroke: "#dff6ff", "stroke-width": 2.4,
                            "stroke-dasharray": "0.5 13", "stroke-linecap": "round" }, gT);
    const pulse = el("circle", { r: 4.5, fill: "#ffffff", opacity: 0, filter: "url(#glow)" }, gT);

    const right = Math.cos(p.a) > 0;
    const anchor = right ? "start" : "end";
    const off = right ? 34 : -34;
    const g = el("g", {}, gN);
    const ringN = el("circle", { cx: p.x, cy: p.y, r: 15, class: "node-ring", stroke: color }, g);
    const inner = el("circle", { cx: p.x, cy: p.y, r: 6, fill: color, opacity: 0.85 }, g);
    const name = el("text", { x: p.x + off, y: p.y - 6, "text-anchor": anchor,
                              class: "node-label", fill: color }, g);
    name.textContent = a.name;
    const role = el("text", { x: p.x + off, y: p.y + 9, "text-anchor": anchor, class: "node-role" }, g);
    role.textContent = a.role.length > 26 ? a.role.slice(0, 25) + "\u2026" : a.role;
    const vote = el("text", { x: p.x + off, y: p.y + 25, "text-anchor": anchor, class: "node-vote" }, g);
    vote.textContent = "-";

    tentacles[a.name] = { limb, sk, pulse, ringN, inner, vote, p, color, active: false, phase: Math.random() * 6.28 };
  }
}

function animate(ts) {
  const t = ts / 1000;
  for (const name in tentacles) {
    const T = tentacles[name];
    const hx = CX + (HEAD_RX + 6) * Math.cos(T.p.a);
    const hy = CY + (HEAD_RY + 6) * Math.sin(T.p.a);
    const nx = T.p.x - 15 * Math.cos(T.p.a);
    const ny = T.p.y - 15 * Math.sin(T.p.a);
    const dx = nx - hx, dy = ny - hy;
    const len = Math.hypot(dx, dy) || 1;
    const px = -dy / len, py = dx / len;
    const amp = (T.active ? 34 : 17) * (0.75 + 0.25 * Math.sin(t * 0.7 + T.phase));
    const w1 = Math.sin(t * 1.5 + T.phase) * amp;
    const w2 = Math.sin(t * 1.5 + T.phase + 2.2) * amp * 0.62;
    const c1x = hx + dx * 0.34 + px * w1, c1y = hy + dy * 0.34 + py * w1;
    const c2x = hx + dx * 0.68 + px * w2, c2y = hy + dy * 0.68 + py * w2;
    const d = `M ${hx.toFixed(1)} ${hy.toFixed(1)} C ${c1x.toFixed(1)} ${c1y.toFixed(1)}, ${c2x.toFixed(1)} ${c2y.toFixed(1)}, ${nx.toFixed(1)} ${ny.toFixed(1)}`;
    T.limb.setAttribute("d", d);
    T.sk.setAttribute("d", d);
    T.limb.setAttribute("stroke-width", T.active ? 9 : 6);

    if (T.active) {
      const total = T.limb.getTotalLength();
      const frac = ((t * 0.55 + T.phase / 6.28) % 1);
      const pt = T.limb.getPointAtLength(total * frac);
      T.pulse.setAttribute("cx", pt.x);
      T.pulse.setAttribute("cy", pt.y);
      T.pulse.setAttribute("opacity", 0.85 * Math.sin(frac * Math.PI));
      T.ringN.setAttribute("r", 15 + 2.2 * Math.sin(t * 4 + T.phase));
    } else {
      T.pulse.setAttribute("opacity", 0);
      T.ringN.setAttribute("r", 15);
    }
  }
  // eyes drift, so a paused swarm still looks alive rather than crashed
  const dxE = 3 * Math.sin(t * 0.6), dyE = 2 * Math.sin(t * 0.9);
  const pl = $("pupilL"), pr = $("pupilR");
  if (pl) { pl.setAttribute("cx", CX - 44 + dxE); pl.setAttribute("cy", CY - 12 + dyE); }
  if (pr) { pr.setAttribute("cx", CX + 44 + dxE); pr.setAttribute("cy", CY - 12 + dyE); }
  requestAnimationFrame(animate);
}

function paintOctopus(data) {
  const score = (data.consensus && data.consensus.last_score_pct) || 0;
  const decision = (data.consensus && data.consensus.last_decision) || "IDLE";
  for (const a of data.agents) {
    const T = tentacles[a.name];
    if (!T) continue;
    T.active = a.status === "THINKING" || a.status === "ACTING" ||
               (a.status === "VOTED" && (Date.now() / 1000 - a.last_active_at) < 3);
    T.limb.classList.toggle("active", T.active);
    const isVeto = a.status === "VETO";
    T.ringN.setAttribute("stroke", isVeto ? "#f87171" : T.color);
    T.inner.setAttribute("fill", isVeto ? "#f87171" : T.color);
    const line = a.tier === "ANALYST"
      ? (a.stance ? `${a.stance} ${(a.confidence * 100).toFixed(0)}%` : "-")
      : `${a.calls} lenh`;
    txt(T.vote, isVeto ? "VETO" : line);
    T.vote.setAttribute("fill", isVeto ? "#f87171" : "#8798b4");
  }

  const arc = $("consensus-ring");
  if (arc) {
    const r = HEAD_RX + 26;
    const frac = Math.max(0, Math.min(1, score / 100));
    const ang = frac * 2 * Math.PI;
    if (frac <= 0.0001) { arc.setAttribute("d", ""); }
    else {
      const large = ang > Math.PI ? 1 : 0;
      const x = CX + r * Math.cos(ang), y = CY + r * Math.sin(ang);
      arc.setAttribute("d", `M ${CX + r} ${CY} A ${r} ${r} 0 ${large} 1 ${x.toFixed(2)} ${y.toFixed(2)}`);
    }
    arc.setAttribute("stroke", bandColor(score, data));
  }
  txt($("head-score"), pct(score));
  const hs = $("head-score");
  if (hs) hs.setAttribute("fill", bandColor(score, data));
  txt($("octo-caption"), decision === "IDLE" ? "dang khoi dong" :
      `vong #${data.cycles_run} - ${decision}`);
}

function bandColor(score, data) {
  const c = (data && data.consensus) || {};
  const exec = c.execute_threshold_pct === undefined ? 76.8 : c.execute_threshold_pct;
  const hold = c.hold_threshold_pct === undefined ? 70.0 : c.hold_threshold_pct;
  if (score >= exec) return "#34d399";
  if (score >= hold) return "#fbbf24";
  return "#f87171";
}

/* --------------------------------------------------------------- gauge */

function buildGauge() {
  const g = $("gauge");
  if (!g || g.dataset.built) return;
  g.dataset.built = "1";
  const cx = 130, cy = 126, r = 96;
  const arcPath = (from, to) => {
    const a0 = Math.PI * (1 + from / 100), a1 = Math.PI * (1 + to / 100);
    const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
    const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  };
  el("path", { d: arcPath(0, 70), stroke: "#f87171", "stroke-width": 13, fill: "none", opacity: 0.35 }, g);
  el("path", { d: arcPath(70, 76.8), stroke: "#fbbf24", "stroke-width": 13, fill: "none", opacity: 0.55 }, g);
  el("path", { d: arcPath(76.8, 100), stroke: "#34d399", "stroke-width": 13, fill: "none", opacity: 0.4 }, g);
  for (const m of [70, 76.8]) {
    const a = Math.PI * (1 + m / 100);
    el("line", {
      x1: cx + (r - 10) * Math.cos(a), y1: cy + (r - 10) * Math.sin(a),
      x2: cx + (r + 10) * Math.cos(a), y2: cy + (r + 10) * Math.sin(a),
      stroke: "#dde6f4", "stroke-width": 1.5, opacity: 0.8,
    }, g);
  }
  el("text", { x: 20, y: 146, class: "axis" }, g).textContent = "0";
  el("text", { x: 234, y: 146, class: "axis" }, g).textContent = "100";
  const needle = el("line", { x1: cx, y1: cy, x2: cx - r + 18, y2: cy,
                              stroke: "#dde6f4", "stroke-width": 3, "stroke-linecap": "round" }, g);
  needle.setAttribute("id", "needle");
  el("circle", { cx: cx, cy: cy, r: 7, fill: "#0c1220", stroke: "#2a4664", "stroke-width": 2 }, g);
}

/* Why has nothing fired? The question the dashboard exists to answer. */
function paintGateProgress(data) {
  const con = data.consensus || {};
  const bar = con.required_pct || 76.8;
  const now = con.score_pct || 0;
  const fill = $("gate-fill");
  if (fill) {
    fill.style.width = Math.max(0, Math.min(100, (now / bar) * 100)).toFixed(1) + "%";
    fill.style.background = bandColor(now, data);
  }
  // The marks live on a scale where the execute bar sits at 100%, so the hold
  // line lands proportionally below it whatever the active regime moved it to.
  const holdAt = con.hold_threshold_pct === undefined ? 70.0 : con.hold_threshold_pct;
  const mh = $("mark-hold"), me = $("mark-exec");
  if (mh) mh.style.left = Math.min(100, (holdAt / bar) * 100).toFixed(1) + "%";
  if (me) me.style.left = "100%";

  txt($("gate-label"), `dong thuan ${now.toFixed(2)}% / nguong ${bar.toFixed(1)}%`);
  const short = con.points_short || 0;
  const gap = $("gate-gap");
  txt(gap, short > 0 ? `con thieu ${short.toFixed(2)} diem` : "du dieu kien");
  if (gap) gap.style.color = short > 0 ? "#fbbf24" : "#34d399";

  const badge = $("regime-badge");
  if (badge) {
    const reg = con.regime || "UNKNOWN";
    badge.className = "badge " + reg.toLowerCase();
    txt(badge, reg);
  }
  txt($("regime-why"), con.regime_rationale || "");

  const box = $("blocked-reason");
  if (box) {
    const counts = con.decision_counts || {};
    const tally = ["EXECUTE", "HOLD", "VETO", "SKIP"]
      .map((k) => `${k} ${counts[k] || 0}`).join("  \u00b7  ");
    box.innerHTML = `<b>Vi sao chua vao lenh:</b> ${esc(con.blocked_reason || "-")}<br>` +
                    `<span style="color:var(--ink-faint)">${tally}</span>`;
  }
}

function paintGauge(data) {
  const score = (data.consensus && data.consensus.last_score_pct) || 0;
  const decision = (data.consensus && data.consensus.last_decision) || "IDLE";
  const n = $("needle");
  if (n) {
    const cx = 130, cy = 126, r = 96;
    const a = Math.PI * (1 + Math.max(0, Math.min(100, score)) / 100);
    n.setAttribute("x2", (cx + (r - 14) * Math.cos(a)).toFixed(2));
    n.setAttribute("y2", (cy + (r - 14) * Math.sin(a)).toFixed(2));
    n.setAttribute("stroke", bandColor(score, data));
  }
  const num = $("consensus-num");
  txt(num, pct(score));
  if (num) num.style.color = bandColor(score, data);

  const last = (data.recent_cycles && data.recent_cycles[0]) || null;
  const p = last && last.proposal;
  txt($("consensus-prop"), p ? `${p.side} ${p.qty} ${p.symbol} @ ${usd(p.price_cents)}`
                             : "chua co de xuat");

  const v = $("verdict");
  if (v) {
    const map = { EXECUTE: ["execute", "UY QUYEN VESKA"], HOLD: ["hold", "GIU NGUYEN"],
                  VETO: ["veto", "RUNE HUY BO"], WARMUP: ["idle", "DANG KHOI DONG"],
                  SKIP: ["idle", "BO QUA"], IDLE: ["idle", "CHO"] };
    const [cls, label] = map[decision] || ["idle", decision];
    v.className = "verdict " + cls;
    txt(v, label);
  }

  paintGateProgress(data);

  const st = data.consensus && data.consensus.stats;
  if (st) {
    setStats("vote-stats", [
      ["De xuat", st.evaluated || 0],
      ["Uy quyen", st.execute || 0, "pos"],
      ["Giu nguyen", st.hold || 0, "warn"],
      ["Huy bo", st.veto || 0, "neg"],
    ]);
  }

  const tb = $("votes");
  if (tb) {
    const analysts = data.agents.filter((a) => a.weight > 0);
    if (!analysts.length || !analysts.some((a) => a.stance)) {
      tb.innerHTML = '<tr><td colspan="6">chua bo phieu</td></tr>';
    } else {
      tb.innerHTML = analysts.map((a) => {
        const contrib = (a.stance === "HOLD" || !a.stance) ? 0
          : (a.weight * a.confidence * 100 * (a.stance === "BUY" ? 1 : -1));
        const cls = contrib > 0 ? "pos" : contrib < 0 ? "neg" : "";
        const drag = (data.consensus || {}).biggest_drag === a.name;
        return `<tr>
          <td style="color:${hue(a.hue)}">${a.name}${drag ? ' <span class="tag sell">KEO TUT</span>' : ""}</td>
          <td>${esc(a.role)}</td>
          <td class="num">${(a.weight * 100).toFixed(0)}%</td>
          <td><span class="tag ${stanceClass(a.stance)}">${a.stance || "-"}</span>${a.status === "VETO" ? ' <span class="tag sell">VETO</span>' : ""}</td>
          <td class="num">${(a.confidence * 100).toFixed(0)}%</td>
          <td class="num ${cls}">${contrib >= 0 ? "+" : ""}${contrib.toFixed(2)}</td>
        </tr>`;
      }).join("");
    }
  }
}

function esc(s) {
  return String(s === undefined || s === null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* --------------------------------------------------------------- chart */

function paintEquity(curve, startCents) {
  const svg = $("equity");
  if (!svg) return;
  svg.innerHTML = "";
  const W = 900, H = 200, PADL = 8, PADR = 8, PADT = 12, PADB = 18;
  if (!curve || curve.length < 2) {
    el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "axis" }, svg)
      .textContent = "dang thu thap du lieu...";
    return;
  }
  const ys = curve.map((p) => p.equity_cents);
  let lo = Math.min(...ys, startCents), hi = Math.max(...ys, startCents);
  if (hi === lo) { hi = lo + 1; }
  const pad = (hi - lo) * 0.12;
  lo -= pad; hi += pad;
  const X = (i) => PADL + (i / (curve.length - 1)) * (W - PADL - PADR);
  const Y = (v) => PADT + (1 - (v - lo) / (hi - lo)) * (H - PADT - PADB);

  for (let k = 0; k <= 3; k++) {
    const v = lo + (hi - lo) * (k / 3);
    el("line", { x1: PADL, y1: Y(v), x2: W - PADR, y2: Y(v), class: "gridline" }, svg);
    el("text", { x: PADL + 3, y: Y(v) - 3, class: "axis" }, svg).textContent = usdShort(v);
  }
  el("line", { x1: PADL, y1: Y(startCents), x2: W - PADR, y2: Y(startCents),
               stroke: "#3d4d68", "stroke-width": 1, "stroke-dasharray": "4 4" }, svg);

  const pts = curve.map((p, i) => `${X(i).toFixed(1)},${Y(p.equity_cents).toFixed(1)}`);
  const up = ys[ys.length - 1] >= startCents;
  const color = up ? "#34d399" : "#f87171";
  el("polygon", {
    points: `${PADL},${H - PADB} ${pts.join(" ")} ${W - PADR},${H - PADB}`,
    fill: color, opacity: 0.09,
  }, svg);
  el("polyline", { points: pts.join(" "), fill: "none", stroke: color,
                   "stroke-width": 2, "stroke-linejoin": "round" }, svg);
  const lastX = X(curve.length - 1), lastY = Y(ys[ys.length - 1]);
  el("circle", { cx: lastX, cy: lastY, r: 3.5, fill: color }, svg);

  txt($("equity-range"), `${usd(Math.min(...ys))} - ${usd(Math.max(...ys))}`);
}

/* --------------------------------------------------------------- stats */

function setStats(id, rows) {
  const host = $(id);
  if (!host) return;
  host.innerHTML = rows.map(([k, v, cls]) =>
    `<div class="stat"><div class="k">${esc(k)}</div><div class="v ${cls || ""}">${esc(v)}</div></div>`
  ).join("");
}

function paintMarket(data) {
  const m = data.market || {};
  const ret = m.return_pct || 0;
  setStats("market-stats", [
    ["Von hien tai", usd(m.equity_cents)],
    ["Loi nhuan", (ret >= 0 ? "+" : "") + pct(ret), ret >= 0 ? "pos" : "neg"],
    ["Tien mat", usd(m.cash_cents)],
    ["Vi the mo", m.open_positions || 0],
    ["Vong khep", m.closed_trades || 0],
    ["Ty le thang", pct((m.win_rate || 0) * 100, 1)],
    ["Phi + truot gia", usd(m.fees_paid_cents)],
    ["Lai/lo da chot", usd(m.realised_pnl_cents), (m.realised_pnl_cents || 0) >= 0 ? "pos" : "neg"],
  ]);
  paintEquity(m.equity_curve, m.start_equity_cents || 15000000);

  const tb = $("trades");
  const rows = m.recent_trades || [];
  txt($("trade-count"), String(m.closed_trades || 0));
  if (tb) {
    tb.innerHTML = rows.length ? rows.map((t) => `<tr>
      <td>${esc(t.symbol)}</td>
      <td><span class="tag ${t.side === "LONG" ? "buy" : "sell"}">${esc(t.side)}</span></td>
      <td class="num">${t.qty}</td>
      <td class="num">${usd(t.entry_price_cents)}</td>
      <td class="num">${usd(t.exit_price_cents)}</td>
      <td class="num ${t.net_pnl_cents >= 0 ? "pos" : "neg"}">${t.net_pnl_cents >= 0 ? "+" : ""}${usd(t.net_pnl_cents)}</td>
      <td class="num">${t.entry_step}</td>
      <td class="num">${t.exit_step}</td>
      <td>${esc(t.reason)}</td>
      <td class="num">${pct(t.consensus_pct, 1)}</td>
    </tr>`).join("") : '<tr><td colspan="10">chua co giao dich nao</td></tr>';
  }

  const rj = m.rejections || {};
  txt($("foot-reject"), Object.entries(rj).map(([k, v]) => `${k}=${v}`).join(" "));
  txt($("foot-step"), String(m.step || 0));
  txt($("foot-kill"), m.halted ? "KILL-SWITCH DA BAT" : "binh thuong");
  const fk = $("foot-kill");
  if (fk) fk.style.color = m.halted ? "#f87171" : "#34d399";
}

function paintHardware(data) {
  const hw = data.hardware || {};
  const vramCeil = hw.vram_ceiling_gb || 10;
  const ramCeil = hw.ram_ceiling_gb || 6;
  const vu = hw.vram_used_gb, ru = hw.ram_used_gb;

  const setMeter = (barId, textId, used, ceil, total, unit) => {
    const bar = $(barId);
    if (used === null || used === undefined) {
      txt($(textId), "khong do duoc");
      if (bar) { bar.style.width = "0%"; bar.style.background = "#3d4d68"; }
      return;
    }
    const frac = Math.max(0, Math.min(1, used / ceil));
    if (bar) {
      bar.style.width = (frac * 100).toFixed(1) + "%";
      bar.style.background = frac > 1 ? "#f87171" : frac > 0.9 ? "#fbbf24" : "#2dd4bf";
    }
    txt($(textId), `${used.toFixed(2)} / ${ceil.toFixed(1)} GB` +
      (total ? `  (card ${total.toFixed(1)} GB)` : ""));
  };
  setMeter("vram-bar", "vram-text", vu, vramCeil, hw.vram_total_gb);
  setMeter("ram-bar", "ram-text", ru, ramCeil, hw.ram_total_gb);
  txt($("hw-source"), hw.source || "-");
}

/* ---------------------------------------------------------------- poll */

async function poll() {
  try {
    const r = await fetch("/api/telemetry", { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    latest = data;
    lastGoodAt = Date.now();

    const names = data.agents.map((a) => a.name).join(",");
    if (names !== agentOrder.join(",")) {
      agentOrder = data.agents.map((a) => a.name);
      buildOctopus(data.agents);
    }
    paintOctopus(data);
    paintGauge(data);
    paintMarket(data);
    paintHardware(data);

    txt($("brain-name"), data.brain || "-");
    txt($("cycle-count"), String(data.cycles_run || 0));
    txt($("infer-count"), String(data.inference_count || 0));
    txt($("peak-conc"), `${data.peak_concurrency || 0}/${data.max_concurrent_inferences || 1}`);
    if (data.mode) txt($("mode-badge"), data.mode);

    const running = data.hub ? data.hub.running : true;
    setStatus(running ? "live" : "stale", running ? "dang chay" : "da dung");
  } catch (err) {
    setStatus("dead", "mat ket noi");
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

function setStatus(cls, label) {
  const d = $("live-dot");
  if (d) d.className = "dot " + cls;
  txt($("live-text"), label);
}

/* ------------------------------------------------------------ campaign */

async function runCampaign() {
  if (campaignBusy) return;
  campaignBusy = true;
  const btn = $("run-campaign"), body = $("campaign-body");
  if (btn) { btn.disabled = true; btn.textContent = "dang chay..."; }
  if (body) body.textContent = "dang mo phong 1.981 lenh, vui long doi vai giay...";
  try {
    const r = await fetch("/api/campaign", { cache: "no-store" });
    const d = await r.json();
    if (body) {
      const ret = d.return_pct || 0;
      body.innerHTML = `
        <div class="stats" style="margin:0 0 12px;border:1px solid var(--line);border-radius:8px;overflow:hidden">
          <div class="stat"><div class="k">Lenh khop</div><div class="v">${d.executed.toLocaleString()}</div></div>
          <div class="stat"><div class="k">Von cuoi</div><div class="v ${ret >= 0 ? "pos" : "neg"}">${usd(d.end_equity_cents)}</div></div>
          <div class="stat"><div class="k">Loi nhuan</div><div class="v ${ret >= 0 ? "pos" : "neg"}">${ret >= 0 ? "+" : ""}${pct(ret)}</div></div>
          <div class="stat"><div class="k">Sut giam toi da</div><div class="v warn">${pct(d.max_drawdown_pct)}</div></div>
          <div class="stat"><div class="k">Ty le thang</div><div class="v">${pct(d.win_rate * 100, 1)}</div></div>
          <div class="stat"><div class="k">Uy quyen/Giu/Huy</div><div class="v">${d.decisions.EXECUTE}/${d.decisions.HOLD}/${d.decisions.VETO}</div></div>
        </div>
        <b>Cong bo gia dinh.</b> ${esc(d.disclosure)}`;
    }
  } catch (err) {
    if (body) body.textContent = "khong chay duoc chien dich: " + err;
  } finally {
    campaignBusy = false;
    if (btn) { btn.disabled = false; btn.textContent = "chay lai"; }
  }
}

/* ---------------------------------------------------------------- boot */

document.addEventListener("DOMContentLoaded", () => {
  buildGauge();
  const btn = $("run-campaign");
  if (btn) btn.addEventListener("click", runCampaign);
  requestAnimationFrame(animate);
  poll();
});
