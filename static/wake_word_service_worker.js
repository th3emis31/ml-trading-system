/**
 * JARVIS Wake Word Service Worker
 * Enables background listening for "Hey Jarvis" wake word
 */

// Service Worker activate and install
self.addEventListener('install', (event) => {
  console.log('[SW] Wake word service worker installing...');
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  console.log('[SW] Wake word service worker activating...');
  event.waitUntil(clients.claim());
});

// Message handler from main thread
self.addEventListener('message', (event) => {
  const { type, data } = event.data;
  
  console.log('[SW] Received message:', type);
  
  switch(type) {
    case 'START_BACKGROUND_LISTENING':
      startBackgroundListening(event.ports[0]);
      break;
    
    case 'STOP_BACKGROUND_LISTENING':
      stopBackgroundListening();
      break;
    
    case 'VALIDATE_WAKE_WORD':
      validateWakeWord(data, event.ports[0]);
      break;
    
    case 'STATUS':
      reportStatus(event.ports[0]);
      break;
    
    default:
      console.log('[SW] Unknown message type:', type);
  }
});

let backgroundListeningActive = false;
let recognition = null;
let clientPort = null;

/**
 * Start background listening for wake words
 */
function startBackgroundListening(port) {
  clientPort = port;
  
  if (backgroundListeningActive) {
    console.log('[SW] Background listening already active');
    port.postMessage({
      status: 'already_running',
      message: 'Background listening is already active',
    });
    return;
  }
  
  try {
    // Try to use Web Speech API for recognition
    const SpeechRecognition = self.webkitSpeechRecognition || self.SpeechRecognition;
    
    if (!SpeechRecognition) {
      throw new Error('Web Speech API not available in Service Worker');
    }
    
    recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';
    
    recognition.onstart = () => {
      console.log('[SW] Background listening started');
      backgroundListeningActive = true;
      if (clientPort) {
        clientPort.postMessage({
          status: 'listening_started',
          message: 'Background listening is now active',
        });
      }
    };
    
    recognition.onresult = (event) => {
      let interimTranscript = '';
      let finalTranscript = '';
      
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const transcript = event.results[i][0].transcript;
        
        if (event.results[i].isFinal) {
          finalTranscript += transcript + ' ';
        } else {
          interimTranscript += transcript;
        }
      }
      
      // Check for wake words in final transcript
      if (finalTranscript) {
        checkForWakeWords(finalTranscript.toLowerCase());
      }
      
      // Report interim results
      if (interimTranscript) {
        if (clientPort) {
          clientPort.postMessage({
            status: 'interim_result',
            transcript: interimTranscript,
          });
        }
      }
    };
    
    recognition.onerror = (event) => {
      console.error('[SW] Recognition error:', event.error);
      if (clientPort) {
        clientPort.postMessage({
          status: 'error',
          error: event.error,
        });
      }
    };
    
    recognition.onend = () => {
      console.log('[SW] Background listening ended');
      backgroundListeningActive = false;
      
      // Restart listening if still needed
      if (backgroundListeningActive) {
        try {
          recognition.start();
        } catch (e) {
          console.error('[SW] Failed to restart recognition:', e);
        }
      }
      
      if (clientPort) {
        clientPort.postMessage({
          status: 'listening_stopped',
          message: 'Background listening stopped',
        });
      }
    };
    
    // Start recognition
    recognition.start();
    
  } catch (error) {
    console.error('[SW] Failed to start background listening:', error);
    if (clientPort) {
      clientPort.postMessage({
        status: 'error',
        message: error.message,
      });
    }
  }
}

/**
 * Stop background listening
 */
function stopBackgroundListening() {
  if (recognition && backgroundListeningActive) {
    try {
      recognition.stop();
      backgroundListeningActive = false;
      console.log('[SW] Background listening stopped');
      
      if (clientPort) {
        clientPort.postMessage({
          status: 'success',
          message: 'Background listening stopped',
        });
      }
    } catch (error) {
      console.error('[SW] Error stopping listening:', error);
      if (clientPort) {
        clientPort.postMessage({
          status: 'error',
          message: error.message,
        });
      }
    }
  }
}

/**
 * Check transcript for wake words
 */
function checkForWakeWords(transcript) {
  const wakeWords = ['hey jarvis', 'jarvis'];
  const minConfidence = 0.7;
  
  for (const wakeWord of wakeWords) {
    if (transcript.includes(wakeWord)) {
      console.log('[SW] Wake word detected:', wakeWord);
      
      // Notify client
      if (clientPort) {
        clientPort.postMessage({
          status: 'wake_word_detected',
          wake_word: wakeWord,
          transcript: transcript,
          confidence: minConfidence,
        });
      }
      
      break;
    }
  }
}

/**
 * Validate wake word (called from main thread)
 */
function validateWakeWord(data, port) {
  const { text, sensitivity } = data;
  const wakeWords = ['hey jarvis', 'jarvis'];
  
  let detected = false;
  let detectedWord = null;
  
  for (const wakeWord of wakeWords) {
    if (text.toLowerCase().includes(wakeWord)) {
      detected = true;
      detectedWord = wakeWord;
      break;
    }
  }
  
  port.postMessage({
    status: 'validation_complete',
    detected: detected,
    wake_word: detectedWord,
    transcript: text,
  });
}

/**
 * Report service worker status
 */
function reportStatus(port) {
  port.postMessage({
    status: 'status_report',
    listening_active: backgroundListeningActive,
    api_available: !!recognition,
  });
}

console.log('[SW] Wake word service worker loaded');
