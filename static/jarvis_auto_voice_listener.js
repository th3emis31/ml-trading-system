/**
 * JARVIS Enhanced Auto-Voice Listener (Browser)
 * - Auto-starts listening on page load
 * - Auto-restarts after each recognition session
 * - Persistent listening state
 * - Real-time status updates
 * 
 * SAFE: This is an ADDITIVE enhancement - doesn't delete existing code
 */

class JARVISAutoVoiceListener {
    constructor() {
        this.recognition = null;
        this.isListening = false;
        this.autoRestartEnabled = true;
        this.listeningEnabled = true;
        this.autoRestartAttempts = 0;
        this.maxAutoRestartAttempts = 5;
        this.reconnectDelay = 1000; // 1 second
        this.maxReconnectDelay = 30000; // 30 seconds
        
        this.listeningStartTime = null;
        this.totalSessions = 0;
        this.totalCommands = 0;
        this.realtimeEnabled = true;
        this.activeRealtimeSessionId = null;
        this.isSpeaking = false;
        this.lastAssistantReply = '';
        
        // Callbacks for events
        this.onListeningStart = null;
        this.onListeningStop = null;
        this.onAutoRestart = null;
        this.onCommandRecognized = null;
        this.onError = null;
        
        console.log('[JARVIS Auto-Voice] Initializing...');
        this.initialize();
    }
    
    initialize() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        
        if (!SpeechRecognition) {
            console.error('[JARVIS Auto-Voice] Web Speech API not supported');
            this.notifyError('Web Speech API not supported in this browser');
            return false;
        }
        
        this.recognition = new SpeechRecognition();
        this.setupRecognition();
        this.loadPersistentState();
        
        console.log('[JARVIS Auto-Voice] Ready');
        return true;
    }
    
    setupRecognition() {
        this.recognition.continuous = true;
        this.recognition.interimResults = true;
        // The Web Speech API property is `lang`; `language` was silently ignored.
        this.recognition.lang = 'en-US';
        
        // ON START
        this.recognition.onstart = () => {
            this._startPending = false;
            this.isListening = true;
            this.listeningStartTime = new Date();
            this.totalSessions++;
            this.autoRestartAttempts = 0;
            this.reconnectDelay = 1000;
            
            console.log(`[JARVIS Auto-Voice] 🎤 Listening started (Session #${this.totalSessions})`);
            
            if (this.onListeningStart) {
                this.onListeningStart();
            }
            
            this.updateUI('Listening...', 'listening');
            this.savePersistentState();
            this.startRealtimeSession();
        };
        
        // ON RESULT
        this.recognition.onresult = (event) => {
            let interimTranscript = '';
            let finalTranscript = '';
            
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const transcript = event.results[i][0].transcript;
                const confidence = event.results[i][0].confidence;
                
                if (event.results[i].isFinal) {
                    finalTranscript += transcript + ' ';
                    
                    // Command recognized!
                    this.totalCommands++;
                    console.log(`[JARVIS Auto-Voice] ✓ Command #${this.totalCommands}: "${transcript}" (${(confidence*100).toFixed(0)}%)`);
                    
                    if (this.onCommandRecognized) {
                        this.onCommandRecognized({
                            transcript: transcript,
                            confidence: confidence,
                            isFinal: true
                        });
                    }
                    this.submitRecognizedCommand(transcript, confidence);
                } else {
                    interimTranscript += transcript;
                }
            }
            
            if (finalTranscript) {
                this.updateTranscript(finalTranscript, '');
            } else if (interimTranscript) {
                this.updateTranscript('', interimTranscript);
            }
        };
        
        // ON ERROR
        this.recognition.onerror = (event) => {
            this._startPending = false;
            // no-speech: the browser closed a session after silence. aborted: we
            // stopped it ourselves. Both are routine; onend restarts listening.
            if (event.error === 'no-speech' || event.error === 'aborted') {
                console.debug(`[JARVIS Auto-Voice] Session ended: ${event.error}`);
                return;
            }
            // Microphone permission refused: retrying just loops on the same error.
            if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
                this.autoRestartEnabled = false;
            }
            this.notifyError(`Voice error: ${event.error}`);

            if (this.onError) {
                this.onError(event.error);
            }
        };
        
        // ON END
        this.recognition.onend = () => {
            this._startPending = false;
            this.isListening = false;
            
            if (this.listeningStartTime) {
                const duration = (new Date() - this.listeningStartTime) / 1000;
                console.log(`[JARVIS Auto-Voice] ⏹️ Listening ended (Duration: ${duration.toFixed(1)}s)`);
            }
            
            if (this.onListeningStop) {
                this.onListeningStop();
            }
            
            this.updateUI('Ready to listen', 'ready');
            
            // AUTO-RESTART
            if (this.listeningEnabled && this.autoRestartEnabled) {
                this.scheduleAutoRestart();
            }
        };
    }
    
    scheduleAutoRestart() {
        if (!this.listeningEnabled || !this.autoRestartEnabled) {
            return;
        }
        
        if (this.autoRestartAttempts >= this.maxAutoRestartAttempts) {
            console.error(`[JARVIS Auto-Voice] Max restart attempts (${this.maxAutoRestartAttempts}) reached`);
            this.notifyError('Max auto-restart attempts reached');
            return;
        }
        
        const delay = Math.min(this.reconnectDelay, this.maxReconnectDelay);
        this.autoRestartAttempts++;
        
        console.log(`[JARVIS Auto-Voice] ⟳ Auto-restart in ${delay/1000}s (Attempt ${this.autoRestartAttempts}/${this.maxAutoRestartAttempts})`);
        this.updateUI(`Auto-restarting in ${(delay/1000).toFixed(1)}s...`, 'restarting');
        
        setTimeout(() => {
            if (this.listeningEnabled && this.autoRestartEnabled) {
                console.log('[JARVIS Auto-Voice] 🔄 Attempting auto-restart...');
                
                if (this.onAutoRestart) {
                    this.onAutoRestart();
                }
                
                this.startListening();
            }
        }, delay);
        
        // Exponential backoff
        this.reconnectDelay = Math.min(delay * 1.5, this.maxReconnectDelay);
    }
    
    startListening() {
        if (!this.recognition) {
            console.error('[JARVIS Auto-Voice] Recognition not initialized');
            return;
        }
        
        // Several callers (page auto-start, wake word, enable toggle) may ask to
        // start; isListening only flips in onstart, so also track a pending start.
        if (this.isListening || this._startPending) {
            console.debug('[JARVIS Auto-Voice] Start ignored: already listening');
            return;
        }

        try {
            this._startPending = true;
            this.recognition.start();
            console.log('[JARVIS Auto-Voice] START command sent');
        } catch (e) {
            if (e.message.includes('already started')) {
                console.log('[JARVIS Auto-Voice] Already listening (caught exception)');
            } else {
                console.error('[JARVIS Auto-Voice] Error starting:', e);
                this.notifyError(`Error starting voice: ${e.message}`);
            }
        }
    }
    
    stopListening() {
        if (this.recognition && this.isListening) {
            this.recognition.stop();
            this.isListening = false;
            console.log('[JARVIS Auto-Voice] STOP command sent');
            this.updateUI('Stopped', 'stopped');
        }
        this.endRealtimeSession(false);
    }

    async startRealtimeSession() {
        if (!this.realtimeEnabled || this.activeRealtimeSessionId) {
            return;
        }
        try {
            const response = await fetch('/api/voice/realtime/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    client_id: 'jarvis_unified_browser',
                    mode: 'conversation',
                    locale: this.recognition && this.recognition.language ? this.recognition.language : 'en-US'
                }),
            });
            const data = await response.json();
            if (response.ok && data && data.session && data.session.session_id) {
                this.activeRealtimeSessionId = String(data.session.session_id);
                console.log('[JARVIS Auto-Voice] Realtime session started:', this.activeRealtimeSessionId);
            } else {
                console.warn('[JARVIS Auto-Voice] Realtime session start failed, fallback mode enabled');
            }
        } catch (e) {
            console.warn('[JARVIS Auto-Voice] Realtime start error:', e);
        }
    }

    async endRealtimeSession(processFinal) {
        if (!this.activeRealtimeSessionId) {
            return;
        }
        const currentId = this.activeRealtimeSessionId;
        this.activeRealtimeSessionId = null;
        try {
            await fetch('/api/voice/realtime/stop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session_id: currentId,
                    process_final: !!processFinal
                }),
            });
        } catch (e) {
            console.warn('[JARVIS Auto-Voice] Realtime stop error:', e);
        }
    }

    async submitRecognizedCommand(transcript, confidence) {
        const text = String(transcript || '').trim();
        if (!text) {
            return;
        }
        try {
            let data = null;
            if (this.activeRealtimeSessionId) {
                const rtResp = await fetch('/api/voice/realtime/chunk', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        session_id: this.activeRealtimeSessionId,
                        transcript: text,
                        confidence: typeof confidence === 'number' ? confidence : null,
                        is_final: true
                    }),
                });
                data = await rtResp.json();
            }

            if (!data || data.error) {
                const fallbackResp = await fetch('/api/voice/process', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ transcript: text }),
                });
                data = await fallbackResp.json();
            }

            const speakText = data && data.speak && data.speak.text
                ? String(data.speak.text)
                : this.extractAssistantText(data);
            if (speakText) {
                this.lastAssistantReply = speakText;
                this.speakText(speakText);
                this.updateUI(`Heard: ${text} | JARVIS: ${speakText}`, 'verified');
            } else {
                this.updateUI(`Heard: ${text}`, 'verified');
            }
        } catch (e) {
            console.error('[JARVIS Auto-Voice] Command submit error:', e);
            this.notifyError(`Command processing failed: ${e.message}`);
        }
    }

    extractAssistantText(payload) {
        if (!payload || typeof payload !== 'object') {
            return '';
        }
        const result = payload.result && typeof payload.result === 'object' ? payload.result : payload;
        const candidates = ['speech', 'response', 'message', 'reply'];
        for (const key of candidates) {
            if (typeof result[key] === 'string' && result[key].trim()) {
                return result[key].trim();
            }
        }
        return '';
    }

    stopSpeaking() {
        if (!window.speechSynthesis) {
            return;
        }
        window.speechSynthesis.cancel();
        this.isSpeaking = false;
    }

    speakText(text) {
        const content = String(text || '').trim();
        if (!content || !window.speechSynthesis || !window.SpeechSynthesisUtterance) {
            return;
        }
        this.stopSpeaking();
        const utterance = new SpeechSynthesisUtterance(content);
        utterance.rate = 1.0;
        utterance.pitch = 1.0;
        utterance.volume = 1.0;
        utterance.onstart = () => {
            this.isSpeaking = true;
            this.updateUI('JARVIS speaking...', 'listening');
        };
        utterance.onend = () => {
            this.isSpeaking = false;
            if (this.isListening) {
                this.updateUI('Listening...', 'listening');
            } else {
                this.updateUI('Ready to listen', 'ready');
            }
        };
        utterance.onerror = () => {
            this.isSpeaking = false;
        };
        window.speechSynthesis.speak(utterance);
    }
    
    enableListening() {
        this.listeningEnabled = true;
        console.log('[JARVIS Auto-Voice] ✓ Listening ENABLED');
        this.savePersistentState();
        if (!this.isListening) {
            this.startListening();
        }
    }
    
    disableListening() {
        this.listeningEnabled = false;
        this.stopListening();
        console.log('[JARVIS Auto-Voice] ✗ Listening DISABLED');
        this.savePersistentState();
    }
    
    toggleAutoRestart() {
        this.autoRestartEnabled = !this.autoRestartEnabled;
        const state = this.autoRestartEnabled ? 'ENABLED' : 'DISABLED';
        console.log(`[JARVIS Auto-Voice] ⟳ Auto-restart ${state}`);
        this.savePersistentState();
    }
    
    updateUI(message, status = 'info') {
        const statusElement = document.getElementById('voice-auto-status');
        if (statusElement) {
            statusElement.textContent = message;
            statusElement.className = `voice-status status-${status}`;
        }
        
        document.body.classList.remove('voice-listening', 'voice-ready', 'voice-stopped', 'voice-error', 'voice-restarting');
        document.body.classList.add(`voice-${status}`);
    }
    
    updateTranscript(finalText, interimText) {
        const transcriptElement = document.getElementById('voice-auto-transcript');
        if (transcriptElement) {
            let html = '';
            if (finalText) {
                html += `<span class="final">${finalText}</span>`;
            }
            if (interimText) {
                html += ` <span class="interim">${interimText}</span>`;
            }
            if (html) {
                transcriptElement.innerHTML = html;
            }
        }
    }
    
    notifyError(message) {
        console.error('[JARVIS Auto-Voice] ' + message);
        this.updateUI(`⚠️ ${message}`, 'error');
    }
    
    getStatus() {
        return {
            listening_enabled: this.listeningEnabled,
            is_currently_listening: this.isListening,
            auto_restart_enabled: this.autoRestartEnabled,
            restart_attempts: this.autoRestartAttempts,
            total_sessions: this.totalSessions,
            total_commands: this.totalCommands,
            listening_started: this.listeningStartTime ? this.listeningStartTime.toISOString() : null
        };
    }
    
    savePersistentState() {
        try {
            const state = {
                listeningEnabled: this.listeningEnabled,
                autoRestartEnabled: this.autoRestartEnabled,
                realtimeEnabled: this.realtimeEnabled,
                totalSessions: this.totalSessions,
                totalCommands: this.totalCommands,
                timestamp: new Date().toISOString()
            };
            localStorage.setItem('jarvis_auto_voice_state', JSON.stringify(state));
        } catch (e) {
            console.warn('[JARVIS Auto-Voice] Could not save state:', e);
        }
    }
    
    loadPersistentState() {
        try {
            const stored = localStorage.getItem('jarvis_auto_voice_state');
            if (stored) {
                const state = JSON.parse(stored);
                this.listeningEnabled = state.listeningEnabled !== false;
                this.autoRestartEnabled = state.autoRestartEnabled !== false;
                this.realtimeEnabled = state.realtimeEnabled !== false;
                this.totalSessions = state.totalSessions || 0;
                this.totalCommands = state.totalCommands || 0;
                console.log(`[JARVIS Auto-Voice] Loaded state: ${this.totalSessions} sessions, ${this.totalCommands} commands`);
            }
        } catch (e) {
            console.warn('[JARVIS Auto-Voice] Could not load state:', e);
        }
    }
    
    resetStats() {
        this.totalSessions = 0;
        this.totalCommands = 0;
        this.autoRestartAttempts = 0;
        console.log('[JARVIS Auto-Voice] 🔄 Statistics reset');
        this.savePersistentState();
    }
}

// Global instance
let jarvisAutoVoiceListener = null;

function initJARVISAutoVoiceListener() {
    if (!jarvisAutoVoiceListener) {
        jarvisAutoVoiceListener = new JARVISAutoVoiceListener();
        window.autoVoiceListener = jarvisAutoVoiceListener;
    }
    return jarvisAutoVoiceListener;
}

// Auto-initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    console.log('[JARVIS Auto-Voice] DOM loaded - initializing...');
    const listener = initJARVISAutoVoiceListener();
    
    // Auto-start listening
    setTimeout(() => {
        listener.startListening();
        console.log('[JARVIS Auto-Voice] Auto-start initiated');
    }, 500);
});
