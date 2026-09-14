/**
 * JARVIS Voice AI - Enhanced with Wake-Word & AI Tools
 * 
 * Features:
 * 1. Auto-activate microphone on page load
 * 2. Wake-word detection ("Hey JARVIS")
 * 3. Voice commands routed to AI tools
 * 4. Real-time confidence display
 * 5. Better error feedback
 */

const JARVISVoiceAI = {
    // Configuration
    config: {
        wakeword: 'hey jarvis',
        autoActivateOnLoad: true,
        confidenceThreshold: 0.6,
        autoResetAfterCommand: true,
        voiceTimeout: 10000  // 10 seconds
    },
    
    // State
    state: {
        isListening: false,
        isWoken: false,
        recognition: null,
        confidence: 0,
        transcript: '',
        interimTranscript: '',
        lastCommand: null,
        commandHistory: []
    },
    
    // Initialize on page load
    init: async function() {
        console.log('🎤 JARVIS Voice AI initializing with Wake-Word System...');
        
        try {
            await this.setupSpeechRecognition();
            await this.loadStatus();
            
            if (this.config.autoActivateOnLoad) {
                this.autoActivate();
            }
            
            this.setupUI();
            this.updateStatus('ready', '🎤 Listening for "Hey JARVIS"... Click to start or speak directly.');
            
        } catch (e) {
            console.error('Initialization failed:', e);
            this.updateStatus('error', '❌ Speech recognition not available. Check browser compatibility.');
        }
    },
    
    // Setup Speech Recognition API
    setupSpeechRecognition: function() {
        return new Promise((resolve, reject) => {
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            
            if (!SpeechRecognition) {
                reject(new Error('Speech Recognition not supported'));
                return;
            }
            
            this.state.recognition = new SpeechRecognition();
            this.state.recognition.continuous = true;
            this.state.recognition.interimResults = true;
            this.state.recognition.language = 'en-US';
            
            // Setup event handlers
            this.state.recognition.onstart = () => {
                console.log('🎤 Microphone activated');
                this.state.isListening = true;
                document.body.classList.add('voice-active');
                this.updateUI();
            };
            
            this.state.recognition.onresult = (event) => {
                this.handleVoiceResult(event);
            };
            
            this.state.recognition.onerror = (event) => {
                this.handleVoiceError(event);
            };
            
            this.state.recognition.onend = () => {
                console.log('🎤 Microphone deactivated');
                this.state.isListening = false;
                document.body.classList.remove('voice-active');
                
                // Auto-restart if still should be listening
                if (this.config.autoActivateOnLoad && !this.state.isWoken) {
                    setTimeout(() => this.autoActivate(), 1000);
                }
                
                this.updateUI();
            };
            
            resolve();
        });
    },
    
    // Handle voice recognition results
    handleVoiceResult: function(event) {
        this.state.interimTranscript = '';
        let confidence = 0;
        
        for (let i = event.resultIndex; i < event.results.length; i++) {
            const transcript = event.results[i][0].transcript;
            confidence = event.results[i][0].confidence;
            
            if (event.results[i].isFinal) {
                this.state.transcript = transcript;
                console.log(`✅ Final: "${transcript}" (confidence: ${(confidence * 100).toFixed(0)}%)`);
                
                // Process through wake-word system
                this.processVoiceInput(transcript, confidence);
                
            } else {
                this.state.interimTranscript += transcript;
                console.log(`🔄 Interim: "${transcript}"`);
            }
        }
        
        this.state.confidence = confidence;
        this.updateUI();
    },
    
    // Process voice input through wake-word and AI tools
    processVoiceInput: async function(transcript, confidence) {
        try {
            const response = await fetch('/api/voice/command', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    transcript: transcript,
                    confidence: confidence,
                    command_type: 'auto'
                })
            });
            
            const result = await response.json();
            console.log('Voice command result:', result);
            
            if (result.status === 'listening') {
                // Still waiting for wake-word
                this.updateStatus('listening', result.wakeword_result.message);
            } else if (result.status === 'success') {
                // Wake-word recognized and command processed
                this.state.isWoken = true;
                this.state.commandHistory.push({
                    transcript: transcript,
                    tool: result.tool,
                    timestamp: new Date().toISOString(),
                    response: result.response
                });
                
                this.displayCommandResult(result);
                
                // Speak response if available
                if (result.voice_response) {
                    this.speakResponse(result.voice_response);
                }
                
                // Auto-reset for next command
                if (this.config.autoResetAfterCommand) {
                    setTimeout(() => {
                        this.resetForNextCommand();
                    }, 2000);
                }
            }
        } catch (e) {
            console.error('Error processing voice input:', e);
            this.updateStatus('error', '❌ Error processing command: ' + e.message);
        }
    },
    
    // Handle voice recognition errors
    handleVoiceError: function(event) {
        const errorMessages = {
            'network': '🌐 Network error - check internet connection',
            'no-speech': '🔇 No speech detected - please speak louder',
            'audio-capture': '🎤 Microphone error - check permissions',
            'not-allowed': '❌ Microphone access denied - enable permissions',
            'service-not-allowed': '❌ Speech service not allowed',
            'bad-grammar': '⚠️ Grammar error - try speaking naturally'
        };
        
        const message = errorMessages[event.error] || `Error: ${event.error}`;
        console.error('Voice error:', event.error, message);
        
        this.updateStatus('error', message);
        
        // Attempt auto-recovery
        if (['network', 'no-speech', 'audio-capture'].includes(event.error)) {
            setTimeout(() => {
                if (this.config.autoActivateOnLoad && !this.state.isWoken) {
                    this.autoActivate();
                }
            }, 2000);
        }
    },
    
    // Auto-activate microphone on page load
    autoActivate: function() {
        console.log('🎤 Auto-activating microphone...');
        
        if (!this.state.recognition) {
            console.error('Recognition not initialized');
            return;
        }
        
        try {
            this.state.recognition.start();
        } catch (e) {
            console.error('Error starting recognition:', e);
            this.updateStatus('error', '❌ Failed to start microphone');
        }
    },
    
    // Load wake-word and system status
    loadStatus: async function() {
        try {
            const response = await fetch('/api/voice/wakeword/status');
            const result = await response.json();
            
            if (result.status === 'success') {
                const status = result.wakeword_status;
                console.log('Wake-word status:', status);
                this.config.wakeword = status.wakeword || 'hey jarvis';
            }
        } catch (e) {
            console.warn('Could not load wake-word status:', e);
        }
    },
    
    // Display command result in UI
    displayCommandResult: function(result) {
        console.log('📊 Displaying command result...');
        
        const resultContainer = document.getElementById('command-result');
        if (!resultContainer) return;
        
        let html = '<div class="command-result-box">';
        html += `<p class="command-heard">🎤 Heard: "${result.command}"</p>`;
        html += `<p class="command-tool">🔧 Tool: ${result.tool.replace(/_/g, ' ').toUpperCase()}</p>`;
        
        if (result.tool === 'trading_recommendations' && result.response.recommendations) {
            html += '<div class="recommendations">';
            result.response.recommendations.slice(0, 3).forEach(rec => {
                html += `
                    <div class="recommendation-item">
                        <span class="symbol">${rec.symbol}</span>
                        <span class="action">${rec.action}</span>
                        <span class="confidence">${(rec.confidence * 100).toFixed(0)}%</span>
                    </div>
                `;
            });
            html += '</div>';
        } else if (result.tool === 'market_analysis' && result.response.analysis) {
            const trends = result.response.analysis.trends;
            html += `
                <div class="market-summary">
                    <p>📈 Bullish: ${trends.bullish} | 📉 Bearish: ${trends.bearish} | ➡️ Neutral: ${trends.neutral}</p>
                    <p>Sentiment: ${trends.overall_market_sentiment}</p>
                </div>
            `;
        }
        
        html += '</div>';
        resultContainer.innerHTML = html;
        resultContainer.style.display = 'block';
    },
    
    // Speak response using browser TTS
    speakResponse: function(text) {
        if (!('speechSynthesis' in window)) {
            console.warn('Speech Synthesis not available');
            return;
        }
        
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 1.0;
        utterance.pitch = 1.0;
        utterance.volume = 1.0;
        
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(utterance);
        
        console.log('🔊 Speaking:', text);
    },
    
    // Reset for next command
    resetForNextCommand: function() {
        console.log('🔄 Resetting for next command...');
        this.state.isWoken = false;
        this.state.transcript = '';
        this.state.interimTranscript = '';
        
        this.updateStatus('ready', '🎤 Listening for "Hey JARVIS"...');
        this.autoActivate();
    },
    
    // Update UI elements
    updateUI: function() {
        const statusEl = document.getElementById('voice-status');
        const confidenceEl = document.getElementById('voice-confidence');
        const transcriptEl = document.getElementById('voice-transcript');
        
        if (statusEl) {
            statusEl.classList.toggle('active', this.state.isListening);
        }
        
        if (confidenceEl) {
            const percentage = Math.round(this.state.confidence * 100);
            confidenceEl.textContent = `Confidence: ${percentage}%`;
            confidenceEl.style.color = percentage >= 80 ? '#4CAF50' : percentage >= 60 ? '#FFC107' : '#F44336';
        }
        
        if (transcriptEl) {
            const display = this.state.interimTranscript || this.state.transcript || 'listening...';
            transcriptEl.textContent = display;
        }
    },
    
    // Update status message
    updateStatus: function(type, message) {
        const statusEl = document.getElementById('voice-status');
        if (!statusEl) return;
        
        statusEl.textContent = message;
        statusEl.className = `voice-status ${type}`;
        
        console.log(`[${type.toUpperCase()}] ${message}`);
    },
    
    // Setup UI controls
    setupUI: function() {
        const startBtn = document.getElementById('voice-start-btn');
        const stopBtn = document.getElementById('voice-stop-btn');
        const resetBtn = document.getElementById('voice-reset-btn');
        
        if (startBtn) {
            startBtn.addEventListener('click', () => this.autoActivate());
        }
        
        if (stopBtn) {
            stopBtn.addEventListener('click', () => this.stop());
        }
        
        if (resetBtn) {
            resetBtn.addEventListener('click', () => this.resetForNextCommand());
        }
    },
    
    // Stop listening
    stop: function() {
        if (this.state.recognition) {
            this.state.recognition.stop();
        }
    },
    
    // Get command history
    getHistory: function() {
        return this.state.commandHistory;
    },
    
    // Clear command history
    clearHistory: function() {
        this.state.commandHistory = [];
    }
};

// Initialize when page loads
// DISABLED: This conflicts with the main Speech Recognition in app.py
// Only ONE instance of SpeechRecognition can use the microphone at a time
// document.addEventListener('DOMContentLoaded', () => {
//     JARVISVoiceAI.init();
// });

// Handle page visibility changes (pause when hidden, resume when visible)
document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        JARVISVoiceAI.stop();
    } else {
        JARVISVoiceAI.autoActivate();
    }
});

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    JARVISVoiceAI.stop();
});
