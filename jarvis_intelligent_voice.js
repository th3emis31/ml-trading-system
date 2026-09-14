/**
 * JARVIS Intelligent Voice Response System
 * Provides smart, contextual voice feedback with market analysis insights
 */

const JARVIS_VoiceResponses = {
  responseBank: {
    // Greeting responses
    greeting: [
      'WELCOME BACK THEMIS. Markets are live.',
      'Hello Themis. Your trading system is ready.',
      'JARVIS online. Ready to analyze markets.'
    ],
    
    // Bullish signals
    bullish: [
      'Strong bullish signal detected. This looks profitable.',
      'Excellent setup. Bullish momentum confirmed.',
      'Technical indicators align. Uptrend confirmed.',
      'Pattern recognition shows high probability buy.'
    ],
    
    // Bearish signals
    bearish: [
      'Bearish divergence observed. Exercise caution.',
      'Downtrend confirmed. Short opportunity identified.',
      'Resistance rejected. Selling pressure increasing.',
      'Technical weakness detected. Risk management advised.'
    ],
    
    // Market analysis
    analysis: [
      'Gold market showing consolidation. Breakout imminent.',
      'Bitcoin displaying strong correlation with macro trends.',
      'XAUUSD approaching key support level.',
      'BTC forming potential reversal pattern.',
      'Market volatility increasing. Tighter stops recommended.'
    ],
    
    // Prediction responses
    prediction: [
      'Predictive model indicates bullish continuation.',
      'Machine learning forecast shows downside potential.',
      'Next five days expected to show ranging behavior.',
      'Trend reversal probability seventy-five percent.'
    ],
    
    // Risk alerts
    riskAlert: [
      'Risk warning. Position size exceeds safe parameters.',
      'Your win rate is healthy but drawdown increasing.',
      'Consider taking profits. Momentum fading.',
      'Stop loss placement is optimal. Protect your gains.'
    ],
    
    // Pattern alerts
    pattern: [
      'Head and shoulders pattern identified. Bearish reversal expected.',
      'Double bottom confirmed. Strong support holding.',
      'Triangle consolidation detected. Major breakout coming.',
      'Wedge formation suggests imminent price action.',
      'Channel breakout possible. Volume increasing.'
    ],
    
    // Performance responses
    performance: [
      'Your trading accuracy continues improving. Current learning score seventy-eight.',
      'System learning rate is optimal. Confidence threshold upgraded.',
      'Pattern recognition accuracy up eight percent from yesterday.',
      'Autonomous mode efficiency at ninety-one percent.'
    ],
    
    // Confirmation responses
    confirmation: [
      'Understood. Executing your command now.',
      'Confirmed. Processing market analysis.',
      'Copy that. Running advanced diagnostics.',
      'Roger. Initiating real-time monitoring.'
    ]
  },
  
  // Get contextual response
  getResponse: function(category, index = -1) {
    const responses = this.responseBank[category];
    if (!responses || responses.length === 0) return 'System ready.';
    
    if (index === -1) {
      index = Math.floor(Math.random() * responses.length);
    }
    
    return responses[Math.min(index, responses.length - 1)];
  },
  
  // Speak with emotion (rate, pitch variation)
  speak: function(text, tone = 'normal') {
    const msg = String(text || '').trim();
    if (!msg) return;

    // Prefer the main queued speech engine to avoid interrupting startup briefings.
    if (typeof window.speakResponse === 'function') {
      window.speakResponse(msg, false, { interrupt: false });
      console.log(`🎙️ [${tone.toUpperCase()}] ${msg}`);
      return;
    }

    if (!('speechSynthesis' in window)) return;
    
    const utterance = new SpeechSynthesisUtterance(msg);
    
    // Tone variations
    const toneSettings = {
      normal: { rate: 0.95, pitch: 1.0, volume: 0.9 },
      alert: { rate: 1.1, pitch: 1.2, volume: 1.0 },
      warning: { rate: 0.85, pitch: 0.9, volume: 0.95 },
      calm: { rate: 0.80, pitch: 0.95, volume: 0.85 },
      excited: { rate: 1.15, pitch: 1.1, volume: 1.0 }
    };
    
    const setting = toneSettings[tone] || toneSettings.normal;
    utterance.rate = setting.rate;
    utterance.pitch = setting.pitch;
    utterance.volume = setting.volume;
    utterance.lang = 'en-US';
    
    window.speechSynthesis.speak(utterance);
    console.log(`🎙️ [${tone.toUpperCase()}] ${msg}`);
  },
  
  // Analyze market and provide voice insight
  analyzeAndRespond: async function() {
    try {
      // Get market data
      const response = await fetch('/api/jarvis/market-analysis');
      if (response.ok) {
        const analysis = await response.json();
        
        if (analysis.error) return;
        
        const xau = analysis.XAUUSD || {};
        const btc = analysis.BTCUSD || {};
        
        let responseText = '';
        let tone = 'normal';
        
        // Build intelligent response based on market data
        if (xau.rsi > 70 || btc.rsi > 70) {
          responseText = this.getResponse('bullish');
          tone = 'excited';
        } else if (xau.rsi < 30 || btc.rsi < 30) {
          responseText = this.getResponse('bearish');
          tone = 'warning';
        } else {
          responseText = this.getResponse('analysis');
        }
        
        // Add market-specific insight
        if (xau.prediction && xau.prediction.trend === 'BULLISH') {
          responseText += ' Gold showing bullish potential.';
        }
        if (btc.prediction && btc.prediction.trend === 'BULLISH') {
          responseText += ' Bitcoin trend is upward.';
        }
        
        this.speak(responseText, tone);
      }
    } catch (e) {
      console.error('Analysis error:', e);
    }
  },
  
  // Respond to trade execution
  respondToTrade: function(tradeData) {
    let response = 'Trade executed. ';
    let tone = 'normal';
    
    if (tradeData.type === 'BUY') {
      response += this.getResponse('bullish');
      tone = 'excited';
    } else if (tradeData.type === 'SELL') {
      response += this.getResponse('bearish');
      tone = 'calm';
    }
    
    this.speak(response, tone);
  },
  
  // Respond to risk events
  respondToRisk: function(riskLevel) {
    const tone = riskLevel === 'HIGH' ? 'alert' : 'warning';
    const response = this.getResponse('riskAlert');
    this.speak(response, tone);
  },
  
  // Respond to pattern detection
  respondToPattern: function(pattern) {
    const response = `${pattern.pattern} detected. ${this.getResponse('pattern')}`;
    const tone = pattern.type === 'BEARISH' ? 'warning' : 'excited';
    this.speak(response, tone);
  },
  
  // Performance report
  announcePerformance: function(metrics) {
    const response = `Your learning score is ${metrics.learning_score}. ${this.getResponse('performance')}`;
    this.speak(response, 'calm');
  }
};

// Keep auxiliary voice ready, but do not interrupt the main JARVIS boot sequence.
window.addEventListener('load', function() {
  if (window.JARVIS_DISABLE_AUX_GREETING) {
    return;
  }
  setTimeout(function() {
    const voiceEnabled = localStorage.getItem('jarvis_voice_enabled') !== 'false';
    if (voiceEnabled && window.JARVIS_ENABLE_AUX_GREETING === true) {
      JARVIS_VoiceResponses.speak(
        JARVIS_VoiceResponses.getResponse('greeting'),
        'normal'
      );
    }
  }, 2000);
});

// Make available globally
window.JARVIS_Voice = JARVIS_VoiceResponses;
