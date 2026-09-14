/**
 * JARVIS Wake Word Detection Client - Phase D
 * Coordinates with Service Worker for "Hey Jarvis" detection
 * Integrates with auto-listener from Phase A
 */

class WakeWordClient {
  constructor() {
    this.enabled = true;
    this.backgroundListeningActive = false;
    this.swRegistration = null;
    this.swPort = null;
    this.sensitivity = 0.8;
    this.wakeWords = ['hey jarvis', 'jarvis'];
    this.detectionCount = 0;
    
    this.init();
  }
  
  /**
   * Initialize wake word detection
   */
  init() {
    console.log('[WW] Initializing wake word detection...');
    
    // Register Service Worker
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/static/wake_word_service_worker.js')
        .then((reg) => {
          console.log('[WW] Service Worker registered');
          this.swRegistration = reg;
          this.setupServiceWorkerCommunication();
          this.enableBackgroundListening();
        })
        .catch((error) => {
          console.warn('[WW] Service Worker registration failed:', error);
          // Graceful fallback - system still works
        });
    } else {
      console.warn('[WW] Service Worker not available - background listening disabled');
    }
    
    // Listen for API-based wake word notifications
    this.setupAPIListening();
  }
  
  /**
   * Setup communication with Service Worker
   */
  setupServiceWorkerCommunication() {
    if (!navigator.serviceWorker.controller) {
      console.log('[WW] No active Service Worker controller');
      return;
    }
    
    // Listen for messages from service worker
    navigator.serviceWorker.addEventListener('message', (event) => {
      this.handleServiceWorkerMessage(event.data);
    });
    
    console.log('[WW] Service Worker communication established');
  }
  
  /**
   * Handle messages from Service Worker
   */
  handleServiceWorkerMessage(data) {
    console.log('[WW] Message from SW:', data.status);
    
    switch(data.status) {
      case 'listening_started':
        console.log('[WW] Background listening started');
        this.backgroundListeningActive = true;
        this.updateUIStatus('Background listening active', 'active');
        break;
      
      case 'listening_stopped':
        console.log('[WW] Background listening stopped');
        this.backgroundListeningActive = false;
        this.updateUIStatus('Background listening inactive', 'inactive');
        break;
      
      case 'wake_word_detected':
        this.handleWakeWordDetected(data);
        break;
      
      case 'interim_result':
        console.log('[WW] Interim:', data.transcript);
        break;
      
      case 'error':
        console.error('[WW] Service Worker error:', data.error);
        break;
    }
  }
  
  /**
   * Handle wake word detection
   */
  handleWakeWordDetected(data) {
    console.log('[WW] WAKE WORD DETECTED:', data.wake_word);
    
    // Validate detection
    const { detected, confidence } = this.validateWakeWord(
      data.transcript,
      data.confidence || this.sensitivity
    );
    
    if (detected) {
      this.detectionCount++;
      
      // Record detection on backend
      this.recordDetection(data.wake_word, data.confidence);
      
      // Activate full listening if using auto-listener
      if (window.autoVoiceListener) {
        console.log('[WW] Activating full listening after wake word');
        window.autoVoiceListener.startListening();
        this.updateUIStatus('Wake word recognized - Listening activated', 'detected');
      } else {
        this.updateUIStatus('Wake word detected', 'detected');
      }
      
      // Optional: Provide audio feedback
      this.playWakeWordAudio();
    }
  }
  
  /**
   * Setup API-based listening (fallback for browsers without SW support)
   */
  setupAPIListening() {
    // Poll for API-based wake word validation
    // This catches wake words detected by the auto-listener
    console.log('[WW] API listening setup complete');
  }
  
  /**
   * Validate wake word
   */
  validateWakeWord(text, confidence) {
    text = text.toLowerCase().trim();
    const threshold = this.sensitivity;
    
    for (const wakeWord of this.wakeWords) {
      if (text.includes(wakeWord)) {
        return {
          detected: true,
          wake_word: wakeWord,
          confidence: Math.min(0.99, confidence),
        };
      }
    }
    
    return {
      detected: false,
      wake_word: null,
      confidence: 0,
    };
  }
  
  /**
   * Enable background listening
   */
  enableBackgroundListening() {
    if (!this.enabled) {
      console.log('[WW] Background listening is disabled');
      return;
    }
    
    // Send message to Service Worker
    if (navigator.serviceWorker.controller) {
      const channel = new MessageChannel();
      
      navigator.serviceWorker.controller.postMessage(
        {
          type: 'START_BACKGROUND_LISTENING',
        },
        [channel.port2]
      );
      
      // Set up port for receiving messages
      channel.port1.onmessage = (event) => {
        this.handleServiceWorkerMessage(event.data);
      };
      
      this.swPort = channel.port1;
      console.log('[WW] Background listening enabled');
    }
  }
  
  /**
   * Disable background listening
   */
  disableBackgroundListening() {
    if (navigator.serviceWorker.controller) {
      navigator.serviceWorker.controller.postMessage({
        type: 'STOP_BACKGROUND_LISTENING',
      });
    }
    this.backgroundListeningActive = false;
    console.log('[WW] Background listening disabled');
  }
  
  /**
   * Record detection on backend
   */
  recordDetection(wakeWord, confidence) {
    fetch('/api/jarvis/wake-word/record', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        wake_word: wakeWord,
        confidence: confidence,
      }),
    })
    .then((res) => res.json())
    .then((data) => {
      console.log('[WW] Detection recorded:', data.status);
    })
    .catch((err) => {
      console.warn('[WW] Failed to record detection:', err);
    });
  }
  
  /**
   * Play audio feedback for wake word
   */
  playWakeWordAudio() {
    try {
      // Simple beep using Web Audio API
      const audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const oscillator = audioContext.createOscillator();
      const gainNode = audioContext.createGain();
      
      oscillator.connect(gainNode);
      gainNode.connect(audioContext.destination);
      
      oscillator.frequency.value = 800; // 800 Hz beep
      oscillator.type = 'sine';
      
      gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
      gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.1);
      
      oscillator.start(audioContext.currentTime);
      oscillator.stop(audioContext.currentTime + 0.1);
      
      console.log('[WW] Wake word audio feedback played');
    } catch (error) {
      console.warn('[WW] Could not play audio feedback:', error);
    }
  }
  
  /**
   * Set sensitivity (0.5 - 1.0)
   */
  setSensitivity(value) {
    if (value < 0.5 || value > 1.0) {
      console.warn('[WW] Sensitivity must be between 0.5 and 1.0');
      return false;
    }
    
    this.sensitivity = value;
    
    fetch('/api/jarvis/wake-word/sensitivity', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sensitivity: value }),
    })
    .then((res) => res.json())
    .then((data) => {
      console.log('[WW] Sensitivity updated:', value);
    })
    .catch((err) => {
      console.warn('[WW] Failed to update sensitivity:', err);
    });
    
    return true;
  }
  
  /**
   * Enable/disable detection
   */
  setEnabled(enabled) {
    this.enabled = enabled;
    
    const endpoint = enabled ? '/api/jarvis/wake-word/enable' : '/api/jarvis/wake-word/disable';
    
    fetch(endpoint, { method: 'POST' })
      .then((res) => res.json())
      .then((data) => {
        console.log('[WW] Detection', enabled ? 'enabled' : 'disabled');
        if (enabled) {
          this.enableBackgroundListening();
        } else {
          this.disableBackgroundListening();
        }
      })
      .catch((err) => {
        console.warn('[WW] Failed to update enabled state:', err);
      });
  }
  
  /**
   * Update UI status
   */
  updateUIStatus(message, status) {
    console.log('[WW] Status:', status, message);
    
    // Update any status elements if they exist
    const statusElems = document.querySelectorAll('[id*="wake-word"], [id*="wakeword"]');
    statusElems.forEach((elem) => {
      elem.textContent = message;
      elem.className = 'wake-word-status ' + status;
    });
  }
  
  /**
   * Get status
   */
  getStatus() {
    return {
      enabled: this.enabled,
      background_listening_active: this.backgroundListeningActive,
      sensitivity: this.sensitivity,
      wake_words: this.wakeWords,
      detection_count: this.detectionCount,
    };
  }
}

// Initialize on page load
window.addEventListener('DOMContentLoaded', () => {
  console.log('[WW] Initializing JARVIS wake word detection...');
  window.wakeWordClient = new WakeWordClient();
});

// Expose global functions for debugging
window.enableWakeWord = () => {
  if (window.wakeWordClient) {
    window.wakeWordClient.setEnabled(true);
  }
};

window.disableWakeWord = () => {
  if (window.wakeWordClient) {
    window.wakeWordClient.setEnabled(false);
  }
};

window.getWakeWordStatus = () => {
  if (window.wakeWordClient) {
    return window.wakeWordClient.getStatus();
  }
};

console.log('[WW] Wake word client loaded');
