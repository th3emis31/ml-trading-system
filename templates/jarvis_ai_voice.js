/**
 * JARVIS Expert AI Voice System
 * Implements speaker recognition, smart learning, and autonomous trade detection
 */

let JARVIS_AI = {
  voiceProfile: null,
  userProfile: null,
  isVerified: false,
  voiceSamples: [],
  recognition: null,
  synthVoice: null,
  
  init: async function() {
    console.log('JARVIS AI Engine initializing...');
    try {
      await this.loadProfiles();
      this.initVoiceRecognition();
      this.startAIMonitoring();
      console.log('JARVIS AI Ready - Click "Start Listening" to begin voice commands');
      const ui = document.getElementById('voice-status');
      if (ui) ui.textContent = 'Ready - Click "Start Listening" to begin';
    } catch (e) {
      console.error('Initialization error:', e);
      const ui = document.getElementById('voice-status');
      if (ui) ui.textContent = 'Error initializing. Check console.';
    }
  },
  
  loadProfiles: async function() {
    try {
      const response = await fetch('/api/jarvis/profile');
      if (response.ok) {
        const data = await response.json();
        this.userProfile = data;
        console.log('User profile loaded:', {
          trades: data.trades,
          winRate: data.win_rate + '%',
          learningScore: data.learning_score,
          patternsLearned: data.patterns_learned
        });
      } else {
        console.warn('Profile API returned:', response.status);
        this.userProfile = {
          trades: 0,
          win_rate: 0,
          learning_score: 0,
          patterns_learned: 0,
          voice_verified: false
        };
      }
    } catch (e) {
      console.error('Profile load failed (this is OK on first run):', e.message);
      this.userProfile = {
        trades: 0,
        win_rate: 0,
        learning_score: 0,
        patterns_learned: 0,
        voice_verified: false
      };
    }
  },
  
  initVoiceRecognition: function() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      console.warn('Speech Recognition not supported');
      return;
    }
    
    this.recognition = new SpeechRecognition();
    this.recognition.continuous = true;
    this.recognition.interimResults = true;
    this.recognition.language = 'en-US';
    
    this.recognition.onstart = () => {
      console.log('🎤 Voice authentication listening...');
      document.body.classList.add('voice-active');
    };
    
    this.recognition.onresult = (event) => {
      let interimTranscript = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        const confidence = event.results[i][0].confidence;
        
        if (event.results[i].isFinal) {
          this.verifyAndProcessCommand(transcript, confidence);
        } else {
          interimTranscript += transcript;
        }
      }
      this.updateVoiceUI(interimTranscript, 'listening');
    };
    
    this.recognition.onerror = (event) => {
      console.error('Voice error:', event.error);
      this.updateVoiceUI('Error: ' + event.error, 'error');
    };
    
    this.recognition.onend = () => {
      document.body.classList.remove('voice-active');
      console.log('Voice recognition ended');
      // Don't auto-restart - let user control with buttons
    };
  },
  
  verifyAndProcessCommand: async function(transcript, confidence) {
    console.log('Voice detected: "' + transcript + '" (' + (confidence*100).toFixed(1) + '%)');
    
    // Verify it's the user's voice
    const verification = await this.verifyVoice(confidence);
    
    if (!verification.verified) {
      this.speakResponse('Voice not recognized. ' + verification.reason);
      this.updateVoiceUI('Unrecognized voice', 'rejected');
      return;
    }
    
    this.isVerified = true;
    this.updateVoiceUI('Verified: "' + transcript + '"', 'verified');
    
    // Process the command with AI context
    await this.processAICommand(transcript);
  },
  
  verifyVoice: async function(confidence) {
    try {
      const response = await fetch('/api/jarvis/voice-verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confidence })
      });
      return await response.json();
    } catch (e) {
      console.error('Voice verification failed:', e);
      return { verified: false, reason: 'Verification service error', confidence: 0 };
    }
  },
  
  processAICommand: async function(transcript) {
    const cmd = transcript.toLowerCase();
    
    // AI learns command patterns
    if (cmd.includes('analyze')) {
      await this.analyzeChart();
    } else if (cmd.includes('recommend')) {
      await this.getRecommendation();
    } else if (cmd.includes('trade')) {
      await this.suggestTrade();
    } else if (cmd.includes('learn')) {
      await this.showLearningStatus();
    } else if (cmd.includes('improve')) {
      await this.showImprovementSuggestions();
    } else if (cmd.includes('insights')) {
      await this.showSystemInsights();
    } else {
      this.speakResponse('Command not recognized. Try: analyze, recommend, trade, learn, improve, insights');
    }
  },
  
  analyzeChart: async function() {
    try {
      const response = await fetch('/api/jarvis/analyze');
      const data = await response.json();
      const summary = `${data.pattern} pattern detected with ${(data.confidence*100).toFixed(0)}% confidence. ${data.recommendation}`;
      this.speakResponse(summary);
      this.showNotification('Chart Analysis', summary, 'info');
    } catch (e) {
      this.speakResponse('Analysis failed');
    }
  },
  
  getRecommendation: async function() {
    try {
      const response = await fetch('/api/jarvis/recommend');
      const rec = await response.json();
      const msg = `Suggested entry at ${rec.entry_price} with stop loss at ${rec.stop_loss} and target at ${rec.take_profit}. Confidence: ${(rec.confidence*100).toFixed(0)}%`;
      this.speakResponse(msg);
      this.showNotification('Smart Recommendation', msg, 'success');
    } catch (e) {
      this.speakResponse('Recommendation failed');
    }
  },
  
  suggestTrade: async function() {
    try {
      const response = await fetch('/api/jarvis/suggest-trade');
      const trade = await response.json();
      const msg = `Trade setup detected: ${trade.setup_type} pattern on ${trade.timeframe}. Entry: ${trade.entry}, SL: ${trade.sl}, TP: ${trade.tp}. Risk reward: ${trade.rr}`;
      this.speakResponse(msg);
      this.showNotification('Trade Setup', msg, 'success');
    } catch (e) {
      this.speakResponse('Trade suggestion failed');
    }
  },
  
  showLearningStatus: async function() {
    try {
      const response = await fetch('/api/jarvis/profile');
      const profile = await response.json();
      const msg = `Learning status: ${profile.learning_score} out of 100. You have ${profile.trades} trades with ${profile.win_rate}% win rate. ${profile.patterns_learned} patterns learned.`;
      this.speakResponse(msg);
      this.showNotification('Learning Progress', msg, 'info');
    } catch (e) {
      this.speakResponse('Learning status unavailable');
    }
  },
  
  showImprovementSuggestions: async function() {
    try {
      const response = await fetch('/api/jarvis/improvements');
      const suggestions = await response.json();
      let msg = 'System improvement suggestions: ';
      suggestions.forEach((s, i) => {
        msg += `${i+1}. ${s.type}: ${s.reason}. `;
      });
      this.speakResponse(msg);
      this.showNotification('Improvements', msg, 'info');
    } catch (e) {
      this.speakResponse('Improvements unavailable');
    }
  },
  
  showSystemInsights: async function() {
    try {
      const response = await fetch('/api/jarvis/insights');
      const insights = await response.json();
      let msg = 'System insights: ';
      insights.forEach((insight, i) => {
        msg += `${i+1}. ${insight}. `;
      });
      this.speakResponse(msg);
      this.showNotification('System Insights', msg, 'info');
    } catch (e) {
      this.speakResponse('Insights unavailable');
    }
  },
  
  logTrade: async function(symbol, entry, exit, pnl, timeframe = '1h') {
    try {
      await fetch('/api/jarvis/log-trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, entry, exit, pnl, timeframe })
      });
      console.log('✅ Trade logged for AI learning');
    } catch (e) {
      console.error('Trade logging failed:', e);
    }
  },
  
  speakResponse: function(text) {
    const msg = String(text || '').trim();
    if (!msg) return;

    if (typeof window.speakResponse === 'function') {
      window.speakResponse(msg, false, { interrupt: false });
      return;
    }

    if (!window.speechSynthesis) {
      console.warn('Speech synthesis not available');
      return;
    }
    try {
      const utterance = new SpeechSynthesisUtterance(msg);
      utterance.rate = 0.95;
      utterance.pitch = 1;
      utterance.onerror = (e) => console.error('Speech error:', e);
      window.speechSynthesis.speak(utterance);
    } catch (e) {
      console.error('Speech error:', e);
    }
  },
  
  updateVoiceUI: function(text, status) {
    const ui = document.getElementById('voice-status');
    if (!ui) return;
    
    ui.textContent = text;
    ui.className = 'voice-status ' + status;
    
    if (status === 'verified') {
      ui.style.color = '#22c55e';
      ui.style.textShadow = '0 0 10px rgba(34, 197, 94, 0.6)';
    } else if (status === 'rejected') {
      ui.style.color = '#ef4444';
      ui.style.textShadow = '0 0 10px rgba(239, 68, 68, 0.6)';
    } else if (status === 'listening') {
      ui.style.color = '#60a5fa';
      ui.style.textShadow = '0 0 10px rgba(96, 165, 250, 0.6)';
    }
  },
  
  showNotification: function(title, message, type = 'info') {
    const notif = document.createElement('div');
    notif.className = `ai-notification ai-${type}`;
    notif.innerHTML = `<strong>${title}</strong><p>${message}</p>`;
    document.body.appendChild(notif);
    
    setTimeout(() => notif.remove(), 5000);
  },
  
  startAIMonitoring: function() {
    // Don't auto-start voice - let user click button
    console.log('JARVIS AI monitoring started');
    
    // Monitor performance and auto-improve every 2 minutes
    setInterval(() => this.autoImprove(), 120000);
  },
  
  autoImprove: async function() {
    try {
      const response = await fetch('/api/jarvis/auto-improve');
      const result = await response.json();
      if (result.improvements_made) {
        console.log('🎯 AI Auto-Improvement:', result.improvements_made);
        this.showNotification('Auto-Improvement', result.improvements_made, 'success');
      }
    } catch (e) {
      console.error('Auto-improve failed:', e);
    }
  }
};

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => JARVIS_AI.init());
} else {
  JARVIS_AI.init();
}
