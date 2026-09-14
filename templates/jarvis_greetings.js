/**
 * JARVIS AI Auto-Greeting System
 * Automatically greets the user when the page loads
 * Shows JARVIS welcome messages with voice
 */

// Initialize JARVIS voice greeting
(function() {
  // Voice synthesis function
  window.jarvisSpeak = function(text) {
    const msg = String(text || '').trim();
    if (!msg) return;

    if (typeof window.speakResponse === 'function') {
      window.speakResponse(msg, false, { interrupt: false });
      return;
    }

    if ('speechSynthesis' in window) {
      const utterance = new SpeechSynthesisUtterance(msg);
      utterance.rate = 0.95;
      utterance.pitch = 1;
      utterance.volume = 0.8;
      utterance.lang = 'en-US';
      
      window.speechSynthesis.speak(utterance);
    }
  };
  
  // Auto-greet on page load
  window.addEventListener('load', function() {
    // Keep disabled by default; can be enabled explicitly per page.
    if (window.JARVIS_ENABLE_TEMPLATE_GREETINGS !== true) {
      return;
    }

    // Wait a bit for page to fully load
    setTimeout(function() {
      // Greet 1: Welcome
      window.jarvisSpeak('WELCOME BACK THEMIS');
      
      // Greet 2: What can I do
      setTimeout(function() {
        window.jarvisSpeak('WHAT CAN I DO TODAY FOR YOU');
      }, 3000);
      
      // Greet 3: System update
      setTimeout(function() {
        window.jarvisSpeak('UPDATING FOR THE SYSTEM');
      }, 6000);
    }, 1000);
  });
  
  // Also greet on visibility change (if user tabs away and back)
  document.addEventListener('visibilitychange', function() {
    if (window.JARVIS_ENABLE_TEMPLATE_GREETINGS !== true) {
      return;
    }
    if (!document.hidden) {
      setTimeout(function() {
        window.jarvisSpeak('WELCOME BACK. SYSTEMS ONLINE');
      }, 500);
    }
  });
})();
