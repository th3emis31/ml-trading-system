/**
 * JARVIS Auto-Learning & Autonomous Voice Alert System
 * Monitors trades, learns from patterns, speaks alerts automatically
 */

const JARVIS_AutoLearning = {
  monitoring: false,
  monitorTimer: null,
  deepTimer: null,
  nextDeepAt: 0,
  tradeHistory: [],
  systemMetrics: {
    totalTrades: 0,
    winRate: 0,
    avgProfit: 0,
    avgLoss: 0,
    learningScore: 0,
    improvementRate: 0
  },
  alerts: [],
  assets: ['XAUUSD', 'BTCUSD'],
  deepHours: 2,
  lastEntryAlertBySymbol: {},
  lastDeepAnalysisBySymbol: {},
  alertCooldownMs: 10 * 60 * 1000,
  deepPollMs: 60 * 1000,
  startupGuardUntil: 0,
  
  // Initialize auto-learning
  init: async function(options = {}) {
    const silent = Boolean(options.silent);
    console.log('🤖 JARVIS Auto-Learning System Initializing...');
    this.monitoring = true;
    // Let startup voice briefing finish before autonomous voice alerts begin.
    this.startupGuardUntil = Date.now() + (90 * 1000);
    this.deepHours = this.loadDeepIntervalHours();
    this.loadProfile();
    this.startAutoMonitoring();
    this.startDeepAnalysisScheduler();
    if (!silent) {
      this.speak('JARVIS auto-learning system activated. Monitoring market and trade performance with deep analysis every ' + this.deepHours + ' hours.');
    }
  },

  loadDeepIntervalHours: function() {
    const raw = Number(localStorage.getItem('jarvis_deep_analysis_hours') || '2');
    if (raw === 4) return 4;
    return 2;
  },

  setDeepIntervalHours: function(hours) {
    const next = Number(hours) === 4 ? 4 : 2;
    this.deepHours = next;
    localStorage.setItem('jarvis_deep_analysis_hours', String(next));
    this.nextDeepAt = 0;
    if (this.monitoring) {
      this.startDeepAnalysisScheduler();
    }
    return next;
  },

  nextDeepAnalysisInMinutes: function() {
    if (!this.nextDeepAt) return 0;
    return Math.max(0, Math.round((this.nextDeepAt - Date.now()) / 60000));
  },
  
  // Load user profile and metrics
  loadProfile: async function() {
    try {
      const response = await fetch('/api/jarvis/profile');
      if (response.ok) {
        const profile = await response.json();
        this.tradeHistory = profile.trades || [];
        this.systemMetrics = {
          totalTrades: profile.trades ? profile.trades.length : 0,
          winRate: profile.win_rate || 0,
          avgProfit: profile.avg_profit || 0,
          avgLoss: profile.avg_loss || 0,
          learningScore: profile.learning_score || 0,
          improvementRate: profile.improvement_rate || 0
        };
        console.log('Profile loaded:', this.systemMetrics);
      }
    } catch (e) {
      console.error('Profile load error:', e);
    }
  },
  
  // Continuous monitoring - checks for trade signals every 30 seconds
  startAutoMonitoring: function() {
    const self = this;
    if (this.monitorTimer) {
      clearInterval(this.monitorTimer);
    }
    this.monitorTimer = setInterval(async function() {
      if (!self.monitoring) return;
      
      try {
        // Check for new signals
        const signalResponse = await fetch('/api/signals');
        const signals = await signalResponse.json();
        
        const tzOffsetMin = new Date().getTimezoneOffset();

        // Check for new opportunities and enrich with professional entry plans.
        for (const signal of signals) {
          const strength = Number(signal.confidence || 0) * 100;
          const symbol = String(signal.symbol || '').toUpperCase();
          if (!symbol) continue;

          let recommendation = null;
          if (strength >= 60) {
            recommendation = await self.fetchRecommendation(symbol, '15m', tzOffsetMin);
          }

          if (strength >= 70) {
            self.generateVoiceAlert(signal, 'HIGH', recommendation);
            self.maybeAlertProfessionalEntry(symbol, recommendation, 'live_scan');
          } else if (strength >= 60) {
            self.generateVoiceAlert(signal, 'MEDIUM', recommendation);
          }
        }
        
        // Check auto-trader status
        const statusResponse = await fetch('/api/auto-trade/status');
        const status = statusResponse.ok ? await statusResponse.json() : {};
        
        self.monitorTrades(status);
        
      } catch (e) {
        console.error('Monitoring error:', e);
      }
    }, 30000); // Check every 30 seconds
  },

  startDeepAnalysisScheduler: function() {
    const self = this;
    if (this.deepTimer) {
      clearInterval(this.deepTimer);
    }

    async function runIfDue(force) {
      if (!self.monitoring) return;
      const now = Date.now();
      if (!force && self.nextDeepAt && now < self.nextDeepAt) {
        return;
      }
      self.nextDeepAt = now + (self.deepHours * 60 * 60 * 1000);
      await self.runDeepMarketAnalysis();
    }

    // Respect configured cadence without forcing immediate deep-analysis on refresh.
    if (!this.nextDeepAt || this.nextDeepAt <= Date.now()) {
      this.nextDeepAt = Date.now() + (this.deepHours * 60 * 60 * 1000);
    }

    this.deepTimer = setInterval(function() {
      runIfDue(false);
    }, this.deepPollMs);
  },

  fetchRecommendation: async function(symbol, timeframe, tzOffsetMin) {
    try {
      const recResponse = await fetch('/api/auto-trade/recommend', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: symbol,
          timeframe: timeframe || '15m',
          tz_offset_min: tzOffsetMin,
          request_approval: false
        })
      });
      if (!recResponse.ok) return null;
      const recPayload = await recResponse.json();
      return recPayload.recommendation || null;
    } catch (e) {
      return null;
    }
  },

  formatEntryDetailLine: function(symbol, rec) {
    const report = rec && typeof rec === 'object' ? rec : {};
    const recommendation = report.recommendation || {};
    const entryPlan = report.entry_plan || {};
    const quality = report.entry_quality || {};
    const market = report.market || {};

    const action = String(recommendation.action || 'WAIT').toUpperCase();
    const grade = String(quality.grade || '--').toUpperCase();
    const score = Number(quality.score || 0).toFixed(1);
    const entry = entryPlan.entry != null ? entryPlan.entry : 'n/a';
    const sl = entryPlan.stop_loss != null ? entryPlan.stop_loss : 'n/a';
    const tp1 = entryPlan.take_profit_1 != null ? entryPlan.take_profit_1 : 'n/a';
    const tp2 = entryPlan.take_profit_2 != null ? entryPlan.take_profit_2 : 'n/a';
    const rr = entryPlan.risk_reward != null ? entryPlan.risk_reward : 'n/a';
    const conf = entryPlan.calibrated_confidence_pct != null ? Number(entryPlan.calibrated_confidence_pct).toFixed(0) : '0';
    const risk = String(market.event_risk_level || 'unknown');
    return symbol + ' ' + action + ' | Entry ' + entry + ' | SL ' + sl + ' | TP1 ' + tp1 + ' | TP2 ' + tp2 + ' | RR ' + rr + ' | Quality ' + grade + ' ' + score + ' | Confidence ' + conf + '% | Risk ' + risk;
  },

  buildProfessionalEntryAlert: function(symbol, rec, sourceTag) {
    const report = rec && typeof rec === 'object' ? rec : {};
    const recommendation = report.recommendation || {};
    const entryPlan = report.entry_plan || {};
    const quality = report.entry_quality || {};
    const market = report.market || {};

    const action = String(recommendation.action || 'WAIT').toUpperCase();
    const qualityScore = Number(quality.score || 0);
    const confidence = Number(entryPlan.calibrated_confidence_pct || 0);
    const qualityGate = qualityScore >= 72;
    const confidenceGate = confidence >= 58;

    if (!((action === 'BUY' || action === 'SELL') && qualityGate && confidenceGate)) {
      return null;
    }

    const signature = [
      symbol,
      action,
      String(entryPlan.entry || 'n/a'),
      String(entryPlan.stop_loss || 'n/a'),
      String(entryPlan.take_profit_1 || 'n/a'),
      String(Math.round(qualityScore)),
      String(Math.round(confidence))
    ].join('|');

    const msg = 'Professional ' + action + ' entry detected for ' + symbol + '. Entry ' + (entryPlan.entry != null ? entryPlan.entry : 'n/a') +
      ', stop loss ' + (entryPlan.stop_loss != null ? entryPlan.stop_loss : 'n/a') +
      ', take profit one ' + (entryPlan.take_profit_1 != null ? entryPlan.take_profit_1 : 'n/a') +
      ', take profit two ' + (entryPlan.take_profit_2 != null ? entryPlan.take_profit_2 : 'n/a') +
      '. Risk reward ' + (entryPlan.risk_reward != null ? entryPlan.risk_reward : 'n/a') +
      '. Entry quality ' + (quality.grade || '--') + ' score ' + qualityScore.toFixed(1) +
      ', calibrated confidence ' + confidence.toFixed(0) + ' percent, event risk ' + String(market.event_risk_level || 'unknown') +
      '. Please approve before execution. Source ' + sourceTag + '.';

    return { message: msg, signature: signature };
  },

  maybeAlertProfessionalEntry: function(symbol, rec, sourceTag) {
    const alertPayload = this.buildProfessionalEntryAlert(symbol, rec, sourceTag || 'scan');
    if (!alertPayload) return false;

    const now = Date.now();
    const prev = this.lastEntryAlertBySymbol[symbol] || {};
    const recentDuplicate = prev.signature === alertPayload.signature && (now - Number(prev.ts || 0)) < this.alertCooldownMs;
    if (recentDuplicate) {
      return false;
    }

    this.lastEntryAlertBySymbol[symbol] = {
      signature: alertPayload.signature,
      ts: now
    };

    this.alerts.push(alertPayload.message);
    if (this.alerts.length > 20) {
      this.alerts = this.alerts.slice(-20);
    }
    if (Date.now() >= this.startupGuardUntil) {
      this.speak(alertPayload.message);
    }
    return true;
  },

  runDeepMarketAnalysis: async function() {
    const tzOffsetMin = new Date().getTimezoneOffset();
    const summaryLines = [];

    for (const symbol of this.assets) {
      const rec = await this.fetchRecommendation(symbol, '15m', tzOffsetMin);
      if (!rec) {
        summaryLines.push(symbol + ' deep analysis unavailable right now.');
        continue;
      }

      this.lastDeepAnalysisBySymbol[symbol] = {
        at: new Date().toISOString(),
        report: rec
      };

      summaryLines.push(this.formatEntryDetailLine(symbol, rec));
      this.maybeAlertProfessionalEntry(symbol, rec, 'deep_analysis');
    }

    if (summaryLines.length) {
      const brief = 'Deep market analysis completed for both assets. ' + summaryLines.join('. ');
      if (Date.now() >= this.startupGuardUntil && typeof showResponse === 'function') {
        showResponse('DEEP MARKET ANALYSIS\n\n' + summaryLines.join('\n'), 'success', 'Auto Deep Analysis');
      }
      if (Date.now() >= this.startupGuardUntil) {
        this.speak(brief);
      }
      console.log('[JARVIS Deep Analysis]', brief);
    }
  },
  
  // Monitor active trades and provide alerts
  monitorTrades: function(status) {
    const trades = status.trades || [];
    const session = status.session || {};
    
    if (session.active && trades.length > 0) {
      // Check for unrealized P&L
      for (const trade of trades) {
        if (trade.status === 'open') {
          const pnl = trade.current_price - trade.entry;
          const pnlPercent = (pnl / trade.entry) * 100;
          
          // Alert if trade is in profit
          if (pnlPercent > 2) {
            this.speak(`Congratulations! Your ${trade.symbol} trade is in profit. ${pnlPercent.toFixed(2)} percent gain.`);
          }
          
          // Alert if trade is near stop loss
          if (pnlPercent < -1.5) {
            this.speak(`Warning! Your ${trade.symbol} trade approaching stop loss. Current loss ${pnlPercent.toFixed(2)} percent.`);
          }
        }
      }
    }
  },
  
  // Generate voice alerts for trading signals
  generateVoiceAlert: function(signal, strength, recommendation) {
    const symbol = signal.symbol || 'UNKNOWN';
    const action = signal.signal === 'BUY' ? 'BUY signal' : 'SELL signal';
    const confidence = Math.round(signal.confidence * 100);
    const rec = recommendation && typeof recommendation === 'object' ? recommendation : {};
    const recAction = (((rec.recommendation || {}).action) || 'WAIT').toUpperCase();
    const quality = rec.entry_quality || {};
    const qualityGrade = quality.grade || '--';
    const qualityScore = quality.score || 0;
    const riskLevel = ((rec.market || {}).event_risk_level) || 'unknown';
    
    let alertMessage = '';
    
    if (strength === 'HIGH') {
      if (recAction === 'BUY' || recAction === 'SELL') {
        const entryPlan = rec.entry_plan || {};
        alertMessage = `Strong ${recAction} setup for ${symbol}. Confidence ${confidence} percent. Entry ${entryPlan.entry ?? 'n/a'}, stop ${entryPlan.stop_loss ?? 'n/a'}, take profit one ${entryPlan.take_profit_1 ?? 'n/a'}. Entry quality ${qualityGrade}, score ${qualityScore}. Risk ${riskLevel}. Please approve before opening trade.`;
      } else {
        alertMessage = `Signal detected for ${symbol} with ${confidence} percent confidence, but quality is not ready. Wait for better entry.`;
      }
    } else if (strength === 'MEDIUM') {
      alertMessage = `${action} for ${symbol} with ${confidence} percent confidence. Monitoring for quality confirmation.`;
    }
    
    if (alertMessage && !this.alerts.includes(alertMessage)) {
      this.alerts.push(alertMessage);
      if (Date.now() >= this.startupGuardUntil) {
        this.speak(alertMessage);
      }
      
      // Keep only last 10 alerts in memory
      if (this.alerts.length > 10) {
        this.alerts.shift();
      }
    }
  },
  
  // AI Learning - analyze performance and make recommendations
  analyzePerformance: async function() {
    try {
      const response = await fetch('/api/jarvis/improvements');
      if (response.ok) {
        const improvements = await response.json();
        
        if (improvements && improvements.length > 0) {
          for (const improvement of improvements) {
            let message = '';
            
            if (improvement.type === 'increase_confidence_threshold') {
              message = `Learning update: Increasing confidence threshold to ${improvement.to} for better trade quality.`;
            } else if (improvement.type === 'auto_setup_detection') {
              message = 'Learning update: Auto-setup detection mode enabled for faster trade identification.';
            } else if (improvement.type === 'enable_autonomous_mode') {
              message = 'Learning milestone: Autonomous trading mode is now ready.';
            }
            
            if (message) {
              this.speak(message);
            }
          }
        }
      }
    } catch (e) {
      console.error('Analysis error:', e);
    }
  },
  
  // Text-to-Speech function
  speak: function(text) {
    const msg = String(text || '').trim();
    if (!msg) return;

    // Never interrupt startup briefings or early voice queue.
    if (Date.now() < this.startupGuardUntil) {
      return;
    }

    if (typeof window.speakResponse === 'function') {
      window.speakResponse(msg, false, { interrupt: false });
      console.log('JARVIS says:', msg);
      return;
    }

    if ('speechSynthesis' in window) {
      const utterance = new SpeechSynthesisUtterance(msg);
      utterance.rate = 0.95;
      utterance.pitch = 1;
      utterance.volume = 0.9;
      utterance.lang = 'en-US';
      window.speechSynthesis.speak(utterance);
      console.log('JARVIS says:', msg);
    }
  },
  
  // Stop monitoring
  stop: function() {
    this.monitoring = false;
    if (this.monitorTimer) {
      clearInterval(this.monitorTimer);
      this.monitorTimer = null;
    }
    if (this.deepTimer) {
      clearInterval(this.deepTimer);
      this.deepTimer = null;
    }
    this.speak('JARVIS monitoring system stopped.');
    console.log('Auto-learning stopped');
  },
  
  // Get learning insights
  getInsights: async function() {
    try {
      const response = await fetch('/api/jarvis/insights');
      if (response.ok) {
        const insights = await response.json();
        return insights;
      }
    } catch (e) {
      console.error('Insights error:', e);
    }
    return null;
  }
};

// Auto-start on page load if user hasn't disabled it
window.addEventListener('load', function() {
  setTimeout(function() {
    // Check if auto-learning is enabled in localStorage
    const autoLearningEnabled = localStorage.getItem('jarvis_auto_learning') !== 'false';
    
    if (autoLearningEnabled) {
      JARVIS_AutoLearning.init({ silent: true });
    }
  }, 3000);
});

// Allow user to control auto-learning via console
window.jarvisAutoLearningControl = {
  start: () => JARVIS_AutoLearning.init(),
  stop: () => JARVIS_AutoLearning.stop(),
  enable: () => localStorage.setItem('jarvis_auto_learning', 'true'),
  disable: () => localStorage.setItem('jarvis_auto_learning', 'false'),
  insights: () => JARVIS_AutoLearning.getInsights(),
  deepNow: () => JARVIS_AutoLearning.runDeepMarketAnalysis(),
  setDeep2h: () => JARVIS_AutoLearning.setDeepIntervalHours(2),
  setDeep4h: () => JARVIS_AutoLearning.setDeepIntervalHours(4),
  deepStatus: () => ({
    deepHours: JARVIS_AutoLearning.deepHours,
    nextInMinutes: JARVIS_AutoLearning.nextDeepAnalysisInMinutes(),
    lastBySymbol: JARVIS_AutoLearning.lastDeepAnalysisBySymbol
  })
};
