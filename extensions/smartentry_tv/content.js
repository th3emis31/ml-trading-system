// The SmartEntry panel, drawn on the owner's real TradingView chart.
//
// Why an extension, after three other attempts. The plan was on the system's own page, then injected
// into a Pine script that had to be copied by hand, then written by a worker that drove a browser.
// The first two were not the chart. The third needed a TradingView session the worker did not have,
// and getting one meant either asking the owner to sign in a second time or copying their cookies.
// An extension runs inside the browser they are ALREADY signed into, so none of that applies: no
// session, no password, no automation, and it cannot touch their Pine scripts or their layout.
//
// It only reads. It renders a panel and places no orders - the system's own strategies do that.

const POLL_MS = 30000;
const KNOWN = ["XAUUSD", "BTCUSD"];

// TradingView names the tab "XAUUSD 4,368.135 ..." and the chart may be on something else entirely,
// so the symbol is read from the page rather than assumed. Anything we do not follow shows the board
// alone, which is still true and still useful, instead of a plan for the wrong market.
function currentSymbol() {
  const haystack = (document.title + " " + location.pathname).toUpperCase();
  return KNOWN.find(s => haystack.includes(s)) || null;
}

const css = `
#smartentry-panel{position:fixed;top:64px;right:14px;width:310px;z-index:2147483000;
  font:12px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;color:#e2e8f0;
  background:rgba(11,18,32,.94);border:1px solid rgba(56,189,248,.45);border-radius:8px;
  box-shadow:0 8px 28px rgba(0,0,0,.5);overflow:hidden}
#smartentry-panel .se-head{display:flex;align-items:center;justify-content:space-between;
  padding:7px 10px;background:rgba(12,74,110,.92);font-weight:600}
#smartentry-panel .se-body{padding:8px 10px;max-height:62vh;overflow:auto}
#smartentry-panel .se-row{display:flex;justify-content:space-between;gap:8px;padding:1px 0}
#smartentry-panel .se-k{color:#94a3b8}
#smartentry-panel .se-head-line{margin:2px 0 6px;padding:5px 7px;border-radius:5px;font-weight:600}
#smartentry-panel .go{background:rgba(34,197,94,.18);color:#4ade80}
#smartentry-panel .wait{background:rgba(251,191,36,.16);color:#fbbf24}
#smartentry-panel .off{background:rgba(148,163,184,.14);color:#cbd5e1}
#smartentry-panel .se-sec{margin-top:8px;padding-top:7px;border-top:1px solid rgba(148,176,222,.2);
  color:#94a3b8}
#smartentry-panel .se-strat{margin:4px 0}
#smartentry-panel .se-why{color:#94a3b8;margin-left:9px;font-size:11px}
#smartentry-panel .sending{color:#4ade80}
#smartentry-panel .dry{color:#94a3b8}
#smartentry-panel .bar{height:5px;border-radius:3px;background:rgba(148,176,222,.2);margin:5px 0 2px}
#smartentry-panel .bar>i{display:block;height:5px;border-radius:3px}
#smartentry-panel .se-x{cursor:pointer;opacity:.75;padding:0 3px}
#smartentry-panel .se-err{color:#fca5a5}
#smartentry-show{position:fixed;top:64px;right:14px;z-index:2147483000;cursor:pointer;
  background:rgba(12,74,110,.95);color:#e2e8f0;border:1px solid rgba(56,189,248,.45);
  border-radius:8px;padding:5px 9px;font:12px -apple-system,Segoe UI,Roboto,sans-serif}
`;

function esc(v) {
  return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
const num = v => (v === null || v === undefined || v === "") ? "—"
  : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function build() {
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  const panel = document.createElement("div");
  panel.id = "smartentry-panel";
  panel.innerHTML = `<div class='se-head'><span>SmartEntry</span><span class='se-x' title='hide'>✕</span></div>
    <div class='se-body'>Reading the system…</div>`;
  document.body.appendChild(panel);

  // Hidden, not removed, and the choice is remembered - so it never becomes something that has to be
  // dismissed on every page load.
  panel.querySelector(".se-x").addEventListener("click", () => {
    panel.style.display = "none";
    chrome.storage.local.set({ hidden: true });
    let pill = document.getElementById("smartentry-show");
    if (!pill) {
      pill = document.createElement("div");
      pill.id = "smartentry-show";
      pill.textContent = "SmartEntry";
      pill.addEventListener("click", () => {
        panel.style.display = "";
        pill.remove();
        chrome.storage.local.set({ hidden: false });
      });
      document.body.appendChild(pill);
    }
  });
  chrome.storage.local.get("hidden", v => { if (v?.hidden) panel.querySelector(".se-x").click(); });
  return panel;
}

function renderPlan(plan) {
  if (!plan?.available) return `<div class='se-err'>${esc(plan?.reason || "no plan")}</div>`;
  const cls = ["active", "new_setup"].includes(plan.status) ? "go"
    : plan.status === "waiting_pullback" ? "wait" : "off";
  const r = plan.readiness || {};
  const pct = Math.round((r.share || 0) * 100);
  const colour = pct >= 80 ? "#22c55e" : pct >= 50 ? "#fbbf24" : "#94a3b8";
  return `
    <div class='se-head-line ${cls}'>${esc(plan.headline)}</div>
    <div class='se-row'><span class='se-k'>Entry</span><span>${num(plan.entry)}</span></div>
    <div class='se-row'><span class='se-k'>Stop</span><span>${num(plan.stop_loss)}</span></div>
    <div class='se-row'><span class='se-k'>Target</span><span>${num(plan.take_profit)}</span></div>
    ${r.total ? `<div class='bar'><i style='width:${pct}%;background:${colour}'></i></div>
      <div class='se-k'>Readiness ${esc(r.met)} of ${esc(r.total)} · ${esc(r.reading)}</div>` : ""}`;
}

function renderBoard(board) {
  const rows = board?.strategies || [];
  if (!rows.length) return "";
  const body = rows.map(s => {
    if (!s.available) return `<div class='se-strat'>${esc(s.name)} — <span class='se-err'>unreachable</span></div>`;
    const state = s.halted ? `<span class='se-err'>HALTED</span>`
      : s.sending_orders ? `<span class='sending'>sending orders</span>` : `<span class='dry'>dry run</span>`;
    const money = (s.realised_money === null || s.realised_money === undefined) ? ""
      : ` · banked ${Number(s.realised_money).toFixed(2)}`;
    return `<div class='se-strat'><b>${esc(s.name)}</b> — ${state}${money}
      <div class='se-why'>${esc(s.waiting_for || s.decision || "")}</div></div>`;
  }).join("");
  return `<div class='se-sec'>Strategies that can place an order
    (${esc(board.sending ?? 0)} sending)</div>${body}`;
}

async function tick(panel) {
  const body = panel.querySelector(".se-body");
  const symbol = currentSymbol();
  const reply = await new Promise(res =>
    chrome.runtime.sendMessage({ type: "smartentry-plan", symbol: symbol || "XAUUSD" }, res));

  if (!reply?.ok) {
    // The commonest cause by far is the trading app not running, so say that rather than an error code.
    body.innerHTML = `<div class='se-err'>The SmartEntry system is not answering on port 5000.</div>
      <div class='se-k'>${esc(reply?.error || "")}</div>`;
    return;
  }
  const plan = reply.data;
  body.innerHTML =
    (symbol ? renderPlan(plan)
            : `<div class='se-k'>This chart is not XAUUSD or BTCUSD, so no plan is shown.</div>`) +
    renderBoard(plan.strategy_board) +
    `<div class='se-sec'>Read-only. Places no orders. ${esc(plan.generated_at || "")} UTC</div>`;
}

const panel = build();
tick(panel);
setInterval(() => { if (document.visibilityState === "visible") tick(panel); }, POLL_MS);
