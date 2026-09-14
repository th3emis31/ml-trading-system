/**
 * JARVIS AI Professional Tools Dashboard
 * Advanced market analysis, prediction, pattern detection
 */

const JARVIS_ToolsDashboard = {
  isInitialized: false,
  currentSymbol: 'XAUUSD',
  refreshInterval: 60000, // Refresh every minute

  setResult(targetId, message, isError = false) {
    const node = document.getElementById(targetId);
    if (!node) return;
    node.innerHTML = message;
    node.style.color = isError ? '#ff8f8f' : '#00d4ff';
  },

  async fetchJson(url) {
    const response = await fetch(url, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok || (data && data.error)) {
      throw new Error((data && data.error) || `Request failed: ${response.status}`);
    }
    return data;
  },
  
  async init() {
    if (this.isInitialized) return;
    
    console.log('🛠️ JARVIS Professional Tools Initializing...');
    this.createDashboard();
    this.startAutoRefresh();
    this.isInitialized = true;
  },
  
  createDashboard() {
    // Check if tools section already exists
    if (document.getElementById('jarvis-tools-dashboard')) {
      return;
    }
    
    const toolsHTML = `
    <div id="jarvis-tools-dashboard" style="margin-top: 40px; padding: 20px; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius: 12px; border: 2px solid #00d4ff;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
        <h2 style="color: #00d4ff; font-family: 'Orbitron', sans-serif; margin: 0; font-size: 24px;">🤖 JARVIS AI Professional Tools</h2>
        <div style="display: flex; gap: 10px;">
          <button onclick="JARVIS_ToolsDashboard.switchSymbol('XAUUSD')" style="padding: 8px 16px; background: #00d4ff; color: #000; border: none; border-radius: 6px; cursor: pointer; font-weight: bold;">GOLD</button>
          <button onclick="JARVIS_ToolsDashboard.switchSymbol('BTCUSD')" style="padding: 8px 16px; background: #f39c12; color: #000; border: none; border-radius: 6px; cursor: pointer; font-weight: bold;">BTC</button>
        </div>
      </div>
      
      <!-- Tools Grid -->
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 20px;">
        
        <!-- Market Analysis Tool -->
        <div style="background: #0f3460; border: 1px solid #00d4ff; border-radius: 8px; padding: 15px;">
          <h3 style="color: #00d4ff; margin-top: 0;">📊 Market Analysis</h3>
          <button onclick="JARVIS_ToolsDashboard.runAnalysis()" style="width: 100%; padding: 10px; background: #00d4ff; color: #000; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; margin-bottom: 10px;">Run Advanced Analysis</button>
          <div id="analysis-results" style="font-size: 12px; color: #00d4ff; line-height: 1.6;">Ready</div>
        </div>
        
        <!-- Price Prediction Tool -->
        <div style="background: #0f3460; border: 1px solid #00d4ff; border-radius: 8px; padding: 15px;">
          <h3 style="color: #00d4ff; margin-top: 0;">🎯 Price Prediction (5-Day)</h3>
          <button onclick="JARVIS_ToolsDashboard.runPrediction()" style="width: 100%; padding: 10px; background: #00d4ff; color: #000; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; margin-bottom: 10px;">Generate Forecast</button>
          <div id="prediction-results" style="font-size: 12px; color: #00d4ff; line-height: 1.6;">Ready</div>
        </div>
        
        <!-- Pattern Detection Tool -->
        <div style="background: #0f3460; border: 1px solid #00d4ff; border-radius: 8px; padding: 15px;">
          <h3 style="color: #00d4ff; margin-top: 0;">🔍 Pattern Detection</h3>
          <button onclick="JARVIS_ToolsDashboard.detectPatterns()" style="width: 100%; padding: 10px; background: #00d4ff; color: #000; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; margin-bottom: 10px;">Scan Patterns</button>
          <div id="patterns-results" style="font-size: 12px; color: #00d4ff; line-height: 1.6;">Ready</div>
        </div>
        
        <!-- Technical Indicators Tool -->
        <div style="background: #0f3460; border: 1px solid #00d4ff; border-radius: 8px; padding: 15px;">
          <h3 style="color: #00d4ff; margin-top: 0;">📈 Technical Indicators</h3>
          <button onclick="JARVIS_ToolsDashboard.getIndicators()" style="width: 100%; padding: 10px; background: #00d4ff; color: #000; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; margin-bottom: 10px;">Calculate Indicators</button>
          <div id="indicators-results" style="font-size: 12px; color: #00d4ff; line-height: 1.6;">Ready</div>
        </div>
        
        <!-- Symbol Comparison Tool -->
        <div style="background: #0f3460; border: 1px solid #00d4ff; border-radius: 8px; padding: 15px;">
          <h3 style="color: #00d4ff; margin-top: 0;">⚖️ Symbol Comparison</h3>
          <button onclick="JARVIS_ToolsDashboard.compareSymbols()" style="width: 100%; padding: 10px; background: #00d4ff; color: #000; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; margin-bottom: 10px;">Compare Gold & BTC</button>
          <div id="comparison-results" style="font-size: 12px; color: #00d4ff; line-height: 1.6;">Ready</div>
        </div>
        
        <!-- Voice Analysis Tool -->
        <div style="background: #0f3460; border: 1px solid #00d4ff; border-radius: 8px; padding: 15px;">
          <h3 style="color: #00d4ff; margin-top: 0;">🎙️ Voice Analysis</h3>
          <button onclick="JARVIS_ToolsDashboard.voiceAnalysis()" style="width: 100%; padding: 10px; background: #00d4ff; color: #000; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; margin-bottom: 10px;">Analyze & Speak</button>
          <div id="voice-results" style="font-size: 12px; color: #00d4ff; line-height: 1.6;">Ready</div>
        </div>
      </div>
      
      <!-- Insights Panel -->
      <div style="background: #0f3460; border: 1px solid #00d4ff; border-radius: 8px; padding: 15px;">
        <h3 style="color: #00d4ff; margin-top: 0;">💡 AI Insights & Recommendations</h3>
        <div id="insights-panel" style="font-size: 12px; color: #00d4ff; line-height: 1.8; min-height: 80px;">
          <p>Click "Run Advanced Analysis" to generate insights...</p>
        </div>
      </div>
    </div>
    `;
    
    // Find the Daily Signals section or insert before footer
    const content = document.querySelector('[role="main"]') || document.body;
    const div = document.createElement('div');
    div.innerHTML = toolsHTML;
    content.appendChild(div.firstElementChild);
  },
  
  switchSymbol(symbol) {
    this.currentSymbol = symbol;
    console.log(`Switched to ${symbol}`);
    if (window.JARVIS_Voice && typeof window.JARVIS_Voice.speak === 'function') {
      window.JARVIS_Voice.speak(`Switched to ${symbol} analysis.`, 'calm');
    }
  },
  
  async runAnalysis() {
    console.log(`Running analysis for ${this.currentSymbol}...`);
    this.setResult('analysis-results', 'Analyzing...');
    
    try {
      const analysis = await this.fetchJson(`/api/jarvis/market-analysis/${this.currentSymbol}`);
      const insights = Array.isArray(analysis.insights) ? analysis.insights : [];
      const html = `
        <strong>Price:</strong> $${Number(analysis.current_price || 0).toFixed(2)}<br>
        <strong>RSI:</strong> ${Number(analysis.rsi || 0).toFixed(2)}<br>
        <strong>SMA20/50:</strong> ${Number(analysis.sma_20 || 0).toFixed(2)} / ${Number(analysis.sma_50 || 0).toFixed(2)}<br>
        <strong>ATR:</strong> ${Number(analysis.atr || 0).toFixed(2)}<br>
        <strong>Insights:</strong><br>
        ${(insights.length ? insights : ['No fresh insights available yet.']).map(i => `• ${i}`).join('<br>')}
      `;
      this.setResult('analysis-results', html);
      document.getElementById('insights-panel').innerHTML = (insights.length ? insights : ['Analysis complete.']).map(i => `<p>✓ ${i}</p>`).join('');
      if (window.JARVIS_Voice && typeof window.JARVIS_Voice.analyzeAndRespond === 'function') {
        window.JARVIS_Voice.analyzeAndRespond();
      }
    } catch (e) {
      console.error('Analysis error:', e);
      this.setResult('analysis-results', `Error analyzing market: ${e.message}`, true);
      document.getElementById('insights-panel').innerHTML = `<p>Unable to generate insights: ${e.message}</p>`;
    }
  },
  
  async runPrediction() {
    console.log(`Running prediction for ${this.currentSymbol}...`);
    this.setResult('prediction-results', 'Forecasting...');
    
    try {
      const data = await this.fetchJson(`/api/jarvis/prediction/${this.currentSymbol}`);
      const predictions = Array.isArray(data.predictions) ? data.predictions : [];
      const trend = data.trend || 'NEUTRAL';
      const html = `
        <strong>Current:</strong> $${Number(data.current_price || 0).toFixed(2)}<br>
        <strong>Trend:</strong> ${trend}<br>
        <strong>5-Day Forecast:</strong><br>
        ${predictions.length ? predictions.map((p, i) => `Day ${i+1}: $${Number(p).toFixed(2)}`).join('<br>') : 'No forecast values returned'}
      `;
      this.setResult('prediction-results', html);
      if (window.JARVIS_Voice && typeof window.JARVIS_Voice.speak === 'function') {
        window.JARVIS_Voice.speak(`Price prediction indicates ${trend} movement for ${this.currentSymbol}.`, trend === 'BULLISH' ? 'excited' : 'calm');
      }
    } catch (e) {
      console.error('Prediction error:', e);
      this.setResult('prediction-results', `Error generating prediction: ${e.message}`, true);
    }
  },
  
  async detectPatterns() {
    console.log(`Detecting patterns for ${this.currentSymbol}...`);
    this.setResult('patterns-results', 'Scanning...');
    
    try {
      const data = await this.fetchJson(`/api/jarvis/patterns/${this.currentSymbol}`);
      const patterns = data.patterns || {};
      const patternList = Object.entries(patterns)
        .filter(([_, p]) => p)
        .map(([_, pattern]) => `${pattern.pattern}: ${pattern.type} (${(Number(pattern.strength || 0) * 100).toFixed(0)}% confidence)`)
        .join('<br>');
      this.setResult('patterns-results', patternList || 'No significant patterns detected');
      if (Object.keys(patterns).length > 0) {
        const firstPattern = Object.values(patterns).find(p => p);
        if (firstPattern && window.JARVIS_Voice && typeof window.JARVIS_Voice.respondToPattern === 'function') {
          window.JARVIS_Voice.respondToPattern(firstPattern);
        }
      }
    } catch (e) {
      console.error('Pattern error:', e);
      this.setResult('patterns-results', `Error detecting patterns: ${e.message}`, true);
    }
  },
  
  async getIndicators() {
    console.log(`Getting indicators for ${this.currentSymbol}...`);
    this.setResult('indicators-results', 'Calculating...');
    
    try {
      const data = await this.fetchJson(`/api/jarvis/technical-indicators/${this.currentSymbol}`);
      const html = `
        <strong>Price:</strong> $${Number(data.price || 0).toFixed(2)}<br>
        <strong>RSI:</strong> ${Number(data.rsi || 0).toFixed(2)}<br>
        <strong>MACD:</strong> ${Number(data.macd || 0).toFixed(4)}<br>
        <strong>BB Upper/Mid/Lower:</strong> ${Number(data.bollinger_upper || 0).toFixed(2)} / ${Number(data.bollinger_middle || 0).toFixed(2)} / ${Number(data.bollinger_lower || 0).toFixed(2)}<br>
        <strong>ATR:</strong> ${Number(data.atr || 0).toFixed(2)}<br>
        <strong>Stoch K/D:</strong> ${Number(data.stochastic_k || 0).toFixed(2)} / ${Number(data.stochastic_d || 0).toFixed(2)}
      `;
      this.setResult('indicators-results', html);
    } catch (e) {
      console.error('Indicators error:', e);
      this.setResult('indicators-results', `Error calculating indicators: ${e.message}`, true);
    }
  },
  
  async compareSymbols() {
    console.log('Comparing XAUUSD and BTCUSD...');
    this.setResult('comparison-results', 'Comparing...');
    
    try {
      const data = await this.fetchJson('/api/jarvis/compare-symbols');
      let html = '';
      for (const [symbol, info] of Object.entries(data || {})) {
        html += `
          <strong>${symbol}:</strong><br>
          Price: $${Number(info.current_price || 0).toFixed(2)}<br>
          RSI: ${Number(info.rsi || 0).toFixed(2)}<br>
          Trend: ${info.trend || 'NEUTRAL'}<br>
          Strength: ${info.strength || 'N/A'}<br><br>
        `;
      }
      this.setResult('comparison-results', html || 'No comparison data returned');
      if (window.JARVIS_Voice && typeof window.JARVIS_Voice.speak === 'function') {
        window.JARVIS_Voice.speak('Symbol comparison complete. Check results.', 'calm');
      }
    } catch (e) {
      console.error('Comparison error:', e);
      this.setResult('comparison-results', `Error comparing symbols: ${e.message}`, true);
    }
  },
  
  async voiceAnalysis() {
    console.log('Running voice analysis...');
    this.setResult('voice-results', 'Analyzing...');
    try {
      if (window.JARVIS_Voice && typeof window.JARVIS_Voice.analyzeAndRespond === 'function') {
        await window.JARVIS_Voice.analyzeAndRespond();
        this.setResult('voice-results', `Voice analysis complete for ${this.currentSymbol}. Check speaker output.`);
      } else {
        this.setResult('voice-results', 'Voice analysis system is unavailable.', true);
      }
    } catch (e) {
      this.setResult('voice-results', `Voice analysis failed: ${e.message}`, true);
    }
  },
  
  startAutoRefresh() {
    // Optional: Auto-refresh analysis every minute
    setInterval(() => {
      // Uncomment to enable auto-refresh
      // this.runAnalysis();
    }, this.refreshInterval);
  }
};

// Auto-initialize tools dashboard
window.addEventListener('load', function() {
  setTimeout(function() {
    if (typeof JARVIS_ToolsDashboard !== 'undefined') {
      JARVIS_ToolsDashboard.init();
    }
  }, 3000);
});

// Make globally available
window.JARVIS_Tools = JARVIS_ToolsDashboard;
