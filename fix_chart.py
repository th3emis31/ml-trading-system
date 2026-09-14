"""Replace old chart rendering code with new high-quality version."""
import re

src = open('app.py', encoding='utf-8').read()

OLD = r"""    let lwChartInstance = null;
    let lwCandleSeries = null;
    let lwLineSeries = {}; // entry / tp / sl lines
    let chartAutoRefreshTimer = null;"""

NEW_HEADER = """    let lwChartInstance = null;
    let lwCandleSeries = null;
    let lwLineSeries = {};
    let chartAutoRefreshTimer = null;
    let lastAnalysisLevels = null;"""

# Just do the header swap (add lastAnalysisLevels)
src2 = src.replace(OLD, NEW_HEADER, 1)

# Now replace the loadChart and renderSvgCandleChart functions
OLD_LOAD_START  = "    async function loadChart() {"
OLD_LEGACY_MARK = "    // ── Legacy helper kept for backward compatibility ─────────────────────────"

idx_start  = src2.find(OLD_LOAD_START)
idx_end    = src2.find(OLD_LEGACY_MARK)

if idx_start == -1 or idx_end == -1:
    print("ERROR: Could not find boundaries")
    print("loadChart found:", idx_start != -1)
    print("Legacy mark found:", idx_end != -1)
    exit(1)

NEW_FUNCTIONS = r"""    async function loadChart() {
      var symbol    = (document.getElementById('chart-symbol').value || 'XAUUSD').toUpperCase();
      var container = document.getElementById('lwchart');
      container.innerHTML = '<div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#0099ff;font-size:14px;">Loading live chart for ' + symbol + '...</div>';
      updateStatus('Loading chart...', 'processing');
      try {
        var resp = await fetch('/api/chart-data/' + symbol);
        var json = await resp.json();
        if (!json.candles || json.candles.length === 0) {
          container.innerHTML = '<div style="color:#f87171;padding:20px;">No chart data for ' + symbol + '</div>';
          updateStatus('No data', 'listening'); return;
        }
        renderProChart(container, symbol, json.candles, lastAnalysisLevels);
        var latest    = json.candles[json.candles.length - 1];
        var prev      = json.candles[json.candles.length - 2] || latest;
        var changePct = ((latest.close - prev.close) / prev.close * 100).toFixed(2);
        var cc        = Number(changePct) >= 0 ? '#22c55e' : '#f87171';
        document.getElementById('cs-symbol').textContent = symbol;
        document.getElementById('cs-price').textContent  = fmtPrice(latest.close, symbol);
        document.getElementById('cs-change').innerHTML   = '<span style="color:' + cc + '">' + (Number(changePct) >= 0 ? '+' : '') + changePct + '%</span>';
        document.getElementById('cs-time').textContent   = new Date().toLocaleTimeString();
        updateStatus('Chart loaded', 'success');
        speakResponse('Live chart loaded for ' + symbol + '. Current price ' + fmtPrice(latest.close, symbol));
        clearInterval(chartAutoRefreshTimer);
        chartAutoRefreshTimer = setInterval(loadChart, 60000);
      } catch (err) {
        container.innerHTML = '<div style="color:#f87171;padding:20px;">Chart load failed: ' + err.message + '</div>';
        updateStatus('Chart error', 'listening');
      }
    }

    function fmtPrice(p, sym) {
      return (typeof p === 'number') ? p.toFixed((sym === 'EURUSD' || sym === 'GBPUSD') ? 4 : 2) : '--';
    }

    function calcMA(candles, period) {
      return candles.map(function(c, i) {
        if (i < period - 1) return null;
        var s = 0;
        for (var j = i - period + 1; j <= i; j++) s += candles[j].close;
        return s / period;
      });
    }

    function renderProChart(container, symbol, candles, levels) {
      var W      = Math.max(container.offsetWidth || 900, 600);
      var H_main = 340, H_vol = 60, H = H_main + H_vol + 20;
      var padL = 70, padR = 76, padT = 36;
      var mainW = W - padL - padR;
      var mainH = H_main - padT - 4;
      var volT  = H_main + 8, volH = H_vol - 16;

      var lows  = candles.map(function(c) { return c.low; });
      var highs = candles.map(function(c) { return c.high; });
      var minP  = Math.min.apply(null, lows);
      var maxP  = Math.max.apply(null, highs);
      if (levels) {
        var la = [levels.entry, levels.tp1, levels.tp2, levels.sl].filter(Boolean);
        if (la.length) { minP = Math.min.apply(null, [minP].concat(la)); maxP = Math.max.apply(null, [maxP].concat(la)); }
      }
      var pad  = (maxP - minP) * 0.06; minP -= pad; maxP += pad;
      var rng  = Math.max(maxP - minP, 1e-9);
      var xFor = function(i) { return padL + (i / Math.max(candles.length - 1, 1)) * mainW; };
      var yFor = function(p) { return padT + (1 - (p - minP) / rng) * mainH; };
      var dp   = (symbol === 'EURUSD' || symbol === 'GBPUSD') ? 4 : 2;
      var rawBW = mainW / candles.length * 0.72;
      var bW   = Math.max(1.5, Math.min(rawBW, 12));
      var wkW  = Math.max(0.8, bW * 0.15);

      var ma20 = calcMA(candles, 20);
      var ma50 = calcMA(candles, 50);

      function maPath(arr, col, da) {
        var pts = [];
        arr.forEach(function(v, i) {
          if (v === null) return;
          pts.push((pts.length === 0 ? 'M' : 'L') + xFor(i).toFixed(1) + ',' + yFor(v).toFixed(1));
        });
        if (!pts.length) return '';
        return '<path d="' + pts.join(' ') + '" fill="none" stroke="' + col + '" stroke-width="1.4"' + (da ? ' stroke-dasharray="' + da + '"' : '') + ' opacity="0.85"/>';
      }

      var vols = candles.map(function(c) { return c.volume || 0; });
      var maxV = Math.max.apply(null, vols) || 1;
      var volBars = '';
      candles.forEach(function(c, i) {
        var x  = xFor(i);
        var vh = Math.max(1, (c.volume / maxV) * volH);
        var vc = (c.close >= c.open) ? 'rgba(34,197,94,0.5)' : 'rgba(248,113,113,0.5)';
        volBars += '<rect x="' + (x - bW / 2).toFixed(1) + '" y="' + (volT + volH - vh).toFixed(1) + '" width="' + bW.toFixed(1) + '" height="' + vh.toFixed(1) + '" fill="' + vc + '" rx="1"/>';
      });

      var bars = '';
      candles.forEach(function(c, i) {
        var x = xFor(i), up = c.close >= c.open;
        var col = up ? '#22c55e' : '#ef4444';
        var wkCol = up ? '#16a34a' : '#dc2626';
        var bt = yFor(Math.max(c.open, c.close));
        var bh = Math.max(yFor(Math.min(c.open, c.close)) - bt, 1);
        bars += '<line x1="' + x.toFixed(1) + '" y1="' + yFor(c.high).toFixed(1) + '" x2="' + x.toFixed(1) + '" y2="' + yFor(c.low).toFixed(1) + '" stroke="' + wkCol + '" stroke-width="' + wkW + '"/>';
        bars += '<rect x="' + (x - bW / 2).toFixed(1) + '" y="' + bt.toFixed(1) + '" width="' + bW.toFixed(1) + '" height="' + bh.toFixed(1) + '" fill="' + col + '" rx="1.5"/>';
      });

      var grid = '';
      for (var s = 0; s <= 6; s++) {
        var p  = minP + rng * (s / 6);
        var gy = yFor(p);
        grid += '<line x1="' + padL + '" y1="' + gy.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + gy.toFixed(1) + '" stroke="rgba(148,163,184,0.10)" stroke-width="1"/>';
        grid += '<text x="' + (padL - 8) + '" y="' + (gy + 3.5).toFixed(1) + '" fill="#64748b" font-size="9.5" text-anchor="end">' + p.toFixed(dp) + '</text>';
      }

      var xLbls = '', lc = Math.min(6, candles.length);
      for (var li = 0; li < lc; li++) {
        var idx = Math.round(li * (candles.length - 1) / Math.max(lc - 1, 1));
        var lx  = xFor(idx);
        var ts  = candles[idx].time ? new Date(candles[idx].time * 1000) : null;
        var lab = ts ? ((ts.getMonth() + 1) + '/' + ts.getDate() + ' ' + ts.getHours() + ':00') : ('H' + (idx + 1));
        xLbls += '<text x="' + lx.toFixed(1) + '" y="' + (H_main + 14) + '" fill="#64748b" font-size="9" text-anchor="middle">' + lab + '</text>';
      }

      var lc2   = candles[candles.length - 1].close;
      var ly    = yFor(lc2);
      var pCol  = (candles[candles.length - 1].close >= candles[candles.length - 1].open) ? '#22c55e' : '#ef4444';
      var pLine = '<line x1="' + padL + '" y1="' + ly.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + ly.toFixed(1) + '" stroke="' + pCol + '" stroke-dasharray="3 3" stroke-width="1.2" opacity="0.7"/>' +
        '<rect x="' + (W - padR + 2) + '" y="' + (ly - 10).toFixed(1) + '" width="68" height="20" rx="4" fill="' + pCol + '"/>' +
        '<text x="' + (W - padR + 36) + '" y="' + (ly + 4).toFixed(1) + '" fill="#fff" font-size="10" font-weight="700" text-anchor="middle">' + lc2.toFixed(dp) + '</text>';

      var lvLines = '';
      if (levels) {
        var ld = [
          { price: levels.entry, label: 'ENTRY', color: '#22c55e' },
          { price: levels.sl,    label: 'SL',    color: '#ef4444' },
          { price: levels.tp1,   label: 'TP1',   color: '#f59e0b' },
          { price: levels.tp2,   label: 'TP2',   color: '#a78bfa' }
        ];
        ld.forEach(function(lv) {
          if (!lv.price) return;
          var lvy = yFor(lv.price);
          if (lvy < padT - 10 || lvy > padT + mainH + 10) return;
          lvLines += '<line x1="' + padL + '" y1="' + lvy.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + lvy.toFixed(1) + '" stroke="' + lv.color + '" stroke-dasharray="5 3" stroke-width="1.5"/>';
          lvLines += '<rect x="' + (padL + 4) + '" y="' + (lvy - 9).toFixed(1) + '" width="36" height="16" rx="3" fill="' + lv.color + '" opacity="0.85"/>';
          lvLines += '<text x="' + (padL + 22) + '" y="' + (lvy + 3).toFixed(1) + '" fill="#fff" font-size="9" font-weight="700" text-anchor="middle">' + lv.label + '</text>';
        });
      }

      var vDiv   = '<line x1="' + padL + '" y1="' + (H_main + 4) + '" x2="' + (W - padR) + '" y2="' + (H_main + 4) + '" stroke="rgba(148,163,184,0.15)" stroke-width="1"/>';
      var vLbl   = '<text x="' + (padL - 8) + '" y="' + (volT + 10) + '" fill="#475569" font-size="8.5" text-anchor="end">VOL</text>';
      var legend = '<rect x="' + (padL + 4) + '" y="14" width="8" height="8" rx="2" fill="#f59e0b"/><text x="' + (padL + 16) + '" y="21" fill="#f59e0b" font-size="9">MA20</text>' +
                   '<rect x="' + (padL + 54) + '" y="14" width="8" height="8" rx="2" fill="#a78bfa"/><text x="' + (padL + 66) + '" y="21" fill="#a78bfa" font-size="9">MA50</text>';
      var title  = '<text x="' + (W / 2) + '" y="22" fill="#e0e7ff" font-size="12" font-weight="700" text-anchor="middle">' + symbol + ' | H1 | ' + candles.length + ' bars</text>';
      var bdr    = '<rect x="' + padL + '" y="' + padT + '" width="' + mainW + '" height="' + mainH + '" fill="none" stroke="rgba(148,163,184,0.08)" stroke-width="1"/>';

      var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" style="display:block;background:linear-gradient(180deg,#080d1a 0%,#0a0e27 100%);border-radius:14px;cursor:crosshair;">' +
        bdr + grid + xLbls + vDiv + vLbl + volBars +
        maPath(ma50, '#a78bfa', '4 2') + maPath(ma20, '#f59e0b') +
        lvLines + bars + pLine + title + legend + '</svg>';

      container.innerHTML = svg;

      /* Crosshair */
      var svgEl = container.querySelector('svg');
      if (!svgEl) return;
      var chV = null, chH = null, chT = null;
      svgEl.addEventListener('mousemove', function(e) {
        var r  = svgEl.getBoundingClientRect();
        var mx = (e.clientX - r.left) * (W / r.width);
        var my = (e.clientY - r.top)  * (H / r.height);
        if (mx < padL || mx > W - padR || my < padT || my > padT + mainH) {
          if (chV) { chV.style.display = 'none'; chH.style.display = 'none'; chT.style.display = 'none'; } return;
        }
        var idx = Math.max(0, Math.min(Math.round((mx - padL) / mainW * (candles.length - 1)), candles.length - 1));
        var c   = candles[idx];
        if (!chV) {
          chV = document.createElementNS('http://www.w3.org/2000/svg', 'line');
          chH = document.createElementNS('http://www.w3.org/2000/svg', 'line');
          chT = document.createElementNS('http://www.w3.org/2000/svg', 'g');
          svgEl.appendChild(chV); svgEl.appendChild(chH); svgEl.appendChild(chT);
        }
        chV.setAttribute('x1', xFor(idx)); chV.setAttribute('x2', xFor(idx)); chV.setAttribute('y1', padT); chV.setAttribute('y2', padT + mainH);
        chV.setAttribute('stroke', 'rgba(148,163,184,0.5)'); chV.setAttribute('stroke-width', '1'); chV.setAttribute('stroke-dasharray', '3 3'); chV.style.display = '';
        chH.setAttribute('x1', padL); chH.setAttribute('x2', W - padR); chH.setAttribute('y1', my); chH.setAttribute('y2', my);
        chH.setAttribute('stroke', 'rgba(148,163,184,0.5)'); chH.setAttribute('stroke-width', '1'); chH.setAttribute('stroke-dasharray', '3 3'); chH.style.display = '';
        var ts   = c.time ? new Date(c.time * 1000) : null;
        var tstr = ts ? ts.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
        var tw = 136, th = 92;
        var tx = Math.min(xFor(idx) + 12, W - padR - tw - 4);
        var ty = Math.max(padT, Math.min(my - th / 2, padT + mainH - th));
        chT.style.display = '';
        var upC = (c.close >= c.open) ? '#22c55e' : '#ef4444';
        var m20v = calcMA(candles, 20)[idx]; var m50v = calcMA(candles, 50)[idx];
        chT.innerHTML =
          '<rect x="' + tx + '" y="' + ty + '" width="' + tw + '" height="' + th + '" rx="6" fill="rgba(8,13,26,0.97)" stroke="rgba(148,163,184,0.3)"/>' +
          '<text x="' + (tx+8) + '" y="' + (ty+13) + '" fill="#64748b" font-size="9">' + tstr + '</text>' +
          '<text x="' + (tx+8) + '" y="' + (ty+27) + '" fill="#cbd5e1" font-size="10" font-weight="600">O: <tspan fill="' + upC + '">' + c.open.toFixed(dp) + '</tspan>  H: <tspan fill="#22c55e">' + c.high.toFixed(dp) + '</tspan></text>' +
          '<text x="' + (tx+8) + '" y="' + (ty+42) + '" fill="#cbd5e1" font-size="10" font-weight="600">L: <tspan fill="#ef4444">' + c.low.toFixed(dp) + '</tspan>  C: <tspan fill="' + upC + '" font-weight="800">' + c.close.toFixed(dp) + '</tspan></text>' +
          '<text x="' + (tx+8) + '" y="' + (ty+57) + '" fill="#64748b" font-size="9">Vol: ' + (c.volume ? Math.round(c.volume).toLocaleString() : 'N/A') + '</text>' +
          '<line x1="' + (tx+4) + '" y1="' + (ty+63) + '" x2="' + (tx+tw-4) + '" y2="' + (ty+63) + '" stroke="rgba(148,163,184,0.12)"/>' +
          '<text x="' + (tx+8) + '" y="' + (ty+76) + '" fill="#60a5fa" font-size="9">MA20: <tspan fill="#f59e0b">' + (m20v ? m20v.toFixed(dp) : '--') + '</tspan>  MA50: <tspan fill="#a78bfa">' + (m50v ? m50v.toFixed(dp) : '--') + '</tspan></text>';
      });
      svgEl.addEventListener('mouseleave', function() {
        if (chV) { chV.style.display = 'none'; chH.style.display = 'none'; chT.style.display = 'none'; }
      });
    }

    async function analyzeChartWithJarvis() {
      var symbol = (document.getElementById('chart-symbol').value || 'XAUUSD').toUpperCase();
      updateStatus('JARVIS analyzing...', 'processing');
      document.getElementById('chart-analysis').innerHTML = '<p style="color:#0099ff;">Analyzing ' + symbol + ' chart, please wait...</p>';
      try {
        var resp = await fetch('/api/analyze-chart/' + symbol);
        var data = await resp.json();
        if (data.status !== 'ok') throw new Error(data.message || 'Analysis failed');
        lastAnalysisLevels = { entry: data.support, sl: null, tp1: data.resistance, tp2: null };
        loadChart();
        var rsiEl = document.getElementById('rsi');
        rsiEl.textContent = data.rsi + ' (' + (data.rsi > 70 ? 'Overbought' : data.rsi < 30 ? 'Oversold' : 'Neutral') + ')';
        rsiEl.style.color = data.rsi > 70 ? '#f87171' : data.rsi < 30 ? '#22c55e' : '#0099ff';
        document.getElementById('macd').textContent     = data.volatility + '%';
        var snt = data.sentiment;
        document.getElementById('trend').textContent    = (snt === 'Bullish' ? 'UP ' : snt === 'Bearish' ? 'DOWN ' : '') + snt;
        document.getElementById('trend').style.color    = snt === 'Bullish' ? '#22c55e' : snt === 'Bearish' ? '#f87171' : '#f59e0b';
        document.getElementById('support').textContent    = data.support;
        document.getElementById('resistance').textContent = data.resistance;
        document.getElementById('cs-signal').textContent  = data.signal;
        var sc = data.signal === 'BUY' ? '#22c55e' : data.signal === 'SELL' ? '#f87171' : '#f59e0b';
        document.getElementById('chart-analysis').innerHTML =
          '<div style="font-weight:700;color:#0099ff;font-size:14px;margin-bottom:12px;">JARVIS Analysis - ' + symbol + '</div>' +
          '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;">' +
            '<div style="background:rgba(0,153,255,0.1);padding:8px;border-radius:6px;"><div style="color:#a0aec0;font-size:10px;text-transform:uppercase;">Pattern</div><div style="font-weight:700;margin-top:3px;">' + data.pattern + '</div></div>' +
            '<div style="background:rgba(0,153,255,0.1);padding:8px;border-radius:6px;"><div style="color:#a0aec0;font-size:10px;text-transform:uppercase;">Signal</div><div style="font-weight:700;color:' + sc + ';margin-top:3px;">' + data.signal + ' (' + Math.round(data.model_confidence * 100) + '%)</div></div>' +
            '<div style="background:rgba(34,197,94,0.1);padding:8px;border-radius:6px;"><div style="color:#a0aec0;font-size:10px;text-transform:uppercase;">Support</div><div style="font-weight:700;color:#22c55e;margin-top:3px;">' + data.support + '</div></div>' +
            '<div style="background:rgba(239,68,68,0.1);padding:8px;border-radius:6px;"><div style="color:#a0aec0;font-size:10px;text-transform:uppercase;">Resistance</div><div style="font-weight:700;color:#f87171;margin-top:3px;">' + data.resistance + '</div></div>' +
          '</div>' +
          '<p style="color:#a0aec0;font-size:12px;line-height:1.7;">' + data.analysis_text + '</p>' +
          '<p style="color:#a0aec0;font-size:11px;margin-top:8px;">' + data.chart_metadata.bars_analyzed + ' bars analysed - Updated ' + new Date().toLocaleTimeString() + '</p>';
        updateStatus('Analysis complete', 'success');
        speakResponse(data.analysis_text);
        commandHistory.unshift({ command: 'analyze chart ' + symbol, response: data.analysis_text, time: new Date().toLocaleTimeString(), status: 'success' });
        renderCommandLog();
      } catch (err) {
        document.getElementById('chart-analysis').innerHTML = '<p style="color:#f87171;">Analysis failed: ' + err.message + '</p>';
        updateStatus('Analysis error', 'listening');
        speakResponse('Chart analysis failed. Please try again.');
      }
    }

"""

src3 = src2[:idx_start] + NEW_FUNCTIONS + src2[idx_end:]

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(src3)

print(f"Done! Replaced {idx_end - idx_start} chars with {len(NEW_FUNCTIONS)} chars")
