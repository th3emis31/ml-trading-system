// The service worker does the fetching, not the content script.
//
// A content script runs in the page's origin, so a request to 127.0.0.1 from there is cross-origin
// and the browser blocks it unless the Flask app grows CORS headers. The service worker runs in the
// extension's own origin with host_permissions, so it can call localhost directly and the trading
// app needs no change at all - nothing about the running system is touched to make this work.
const APP = "http://127.0.0.1:5000";

async function plan(symbol) {
  const r = await fetch(`${APP}/api/tradingview/plan?symbol=${encodeURIComponent(symbol)}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`the system answered ${r.status}`);
  return r.json();
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.type !== "smartentry-plan") return false;
  plan(msg.symbol)
    .then(data => reply({ ok: true, data }))
    // Never throw into the page: a stopped app must read as "not running", not as a broken panel.
    .catch(err => reply({ ok: false, error: String(err.message || err) }));
  return true;                                  // keep the channel open for the async reply
});
