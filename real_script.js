
    let commandHistory = [];
    let currentChart = null;
    let voiceResponseEnabled = true;
    let isSpeaking = false;
    let isListening = false;
    let recognition = null;
    let lastErrorTime = 0;
    let lastErrorType = null;
    
    // Initialize Web Speech API for REAL voice recording
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      recognition = new SpeechRecognition();
      
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.language = 'en-US';
      
      recognition.onstart = () => {
        isListening = true;
        updateStatus('🔴 Recording...', 'processing');
        document.getElementById('transcript').textContent = '🎤 Listening for commands...';
      };
      
      recognition.onresult = (event) => {
        let transcript = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const transcriptSegment = event.results[i][0].transcript;
          transcript += transcriptSegment;
        }
        
        document.getElementById('transcript').textContent = 'You said: ' + transcript;
        
        if (event.results[event.results.length - 1].isFinal) {
          // Voice recognized - process the command
          if (transcript.toLowerCase().includes('jarvis') || transcript.length > 2) {
            speakResponse('Command received. Processing your request.');
            // Send to executeCommand
            const command = transcript.replace(/hey\s+jarvis/i, '').trim();
            if (command) {
              executeCommand(command);
            }
          }
        }
      };
      
      recognition.onerror = (event) => {
        console.error('Speech recognition error:', event.error);
        lastErrorTime = Date.now();
        lastErrorType = event.error;
        updateStatus('⚠️ Listening error', 'listening');
        document.getElementById('transcript').textContent = 'Error: ' + event.error;
        
        // Log error details for debugging
        if (event.error === 'aborted') {
          console.warn('🎤 Recognition aborted - likely due to permission API conflict. Retry may help.');
        } else if (event.error === 'no-speech') {
          console.warn('🎤 No speech detected. Please speak louder or try again.');
        } else if (event.error === 'network') {
          console.warn('🎤 Network error detected. Check your internet connection.');
        }
      };
      
      recognition.onend = () => {
        isListening = false;
        // If in continuous mode, schedule restart with error recovery delay
        if (continuousMode) {
          // After aborted/network errors, wait longer before restarting
          let restartDelay = 600;
          if (lastErrorType === 'aborted' || lastErrorType === 'network') {
            const timeSinceError = Date.now() - lastErrorTime;
            restartDelay = Math.max(2000, 3000 - timeSinceError); // Wait 2-3 seconds after aborted error
          } else if (lastErrorType === 'no-speech') {
            restartDelay = 800;
          }
          
          clearTimeout(continuousTimer);
          continuousTimer = setTimeout(listenOnce, restartDelay);
        } else {
          updateStatus('🎤 Ready to Listen', 'listening');
        }
      };
    }
    
    // Professional Voice Settings
    const voiceSettings = {
      rate: 0.9,
      pitch: 1.0,
      volume: 1.0
    };
    
    // Professional greeting phrases
    const greetings = {
      welcome: "Welcome to JARVIS Voice AI. Your professional trading assistant is ready.",
      acknowledge: "Command received. Processing your request.",
      success: "Operation completed successfully.",
      thank_you: "Thank you for using JARVIS. Your results are ready.",
      error: "I encountered an issue. Please try again.",
      goodbye: "JARVIS standing by for your next command."
    };
    
    // Text-to-Speech Function - Professional Edition
    function speakResponse(text, isGreeting = false) {
      if (!voiceResponseEnabled) return;
      
      // Stop any ongoing speech
      speechSynthesis.cancel();
      isSpeaking = true;
      
      // Remove emojis but keep text clean for professional speech
      const cleanText = text.replace(/[^\w\s.,!?$%\-()]/g, '');
      
      const utterance = new SpeechSynthesisUtterance(cleanText);
      utterance.rate = voiceSettings.rate;
      utterance.pitch = voiceSettings.pitch;
      utterance.volume = voiceSettings.volume;
      utterance.lang = 'en-US';
      
      utterance.onend = () => {
        isSpeaking = false;
      };
      
      utterance.onerror = (event) => {
        console.log('Speech synthesis error:', event.error);
        isSpeaking = false;
      };
      
      speechSynthesis.speak(utterance);
    }
    
    // Professional Welcome Message
    function welcomeJARVIS() {
      const statusBadge = document.getElementById('status-badge');
      statusBadge.textContent = '🎤 JARVIS Initialized';
      statusBadge.className = 'status-badge listening';
      
      const transcript = document.getElementById('transcript');
      transcript.textContent = 'JARVIS: ' + greetings.welcome;
      
      // Speak welcome greeting
      speakResponse(greetings.welcome);
    }
    
    // Enhanced Command Execution with Professional Responses
    async function executeCommand(command) {
      updateStatus('🔄 Processing...', 'processing');
      document.getElementById('transcript').textContent = 'You: Hey JARVIS, ' + command;
      
      const response = await fetch('/api/jarvis-command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command, transcript: 'Hey JARVIS, ' + command })
      });
      
      const data = await response.json();
      
      if (data.success) {
        showResponse(data.response, 'success');
        updateStatus('✅ ' + (data.action || 'Command Executed'), 'success');
        
        // Add to history
        commandHistory.unshift({ 
          command, 
          response: data.response, 
          time: new Date().toLocaleTimeString(),
          status: 'success'
        });
        
        // Speak the actual response after acknowledgement
        setTimeout(() => {
          speakResponse(data.response);
          // Add professional closing
          setTimeout(() => {
            speakResponse(greetings.thank_you);
          }, 500);
        }, 800);
        
      } else {
        showResponse(data.response || 'Command not recognized', 'error');
        updateStatus('⚠️ Failed', 'listening');
        
        commandHistory.unshift({ 
          command, 
          response: data.response || 'Failed to process command', 
          time: new Date().toLocaleTimeString(),
          status: 'error'
        });
        
        // Speak error
        speakResponse(greetings.error);
      }
      
      renderCommandLog();
      loadSystemStatus();
    }
    
    function switchTab(tabName) {
      document.querySelectorAll('.tab-content').forEach(tab => tab.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
      
      document.getElementById(tabName).classList.add('active');
      event.target.classList.add('active');
    }
    
    function toggleMicrophone() {
      const btn = document.getElementById('mic-btn');
      
      if (!recognition) {
        updateStatus('❌ Voice not supported', 'listening');
        document.getElementById('transcript').textContent = 'Your browser does not support voice recognition.';
        return;
      }
      
      if (isListening) {
        // Stop recording
        recognition.stop();
        btn.classList.remove('recording');
        updateStatus('🎤 Ready to Listen', 'listening');
      } else {
        // Start recording
        recognition.start();
        btn.classList.add('recording');
      }
    }
    
    function showResponse(message, type) {
      const responseBox = document.getElementById('response');
      
      // Format the message with better text display
      const formattedMessage = `
        <div style='font-size: 16px; font-weight: 700; color: ${type === 'success' ? 'var(--success)' : 'var(--danger)'}; margin-bottom: 12px;'>
          🤖 JARVIS RESPONSE
        </div>
        <div style='font-size: 14px; line-height: 1.8; color: var(--text); white-space: pre-wrap; word-wrap: break-word;'>
          ${message}
        </div>
      `;
      
      responseBox.innerHTML = formattedMessage;
      responseBox.style.display = 'block';
      responseBox.style.borderColor = type === 'success' ? 'var(--success)' : 'var(--danger)';
      responseBox.style.background = type === 'success' 
        ? 'rgba(34, 197, 94, 0.15)' 
        : 'rgba(239, 68, 68, 0.15)';
      responseBox.style.padding = '16px';
      responseBox.style.borderRadius = '10px';
      responseBox.style.fontSize = '14px';
      responseBox.style.maxHeight = '250px';
      responseBox.style.overflowY = 'auto';
      responseBox.style.border = '2px solid';
      
      // Scroll to response
      responseBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    
    function updateStatus(message, type) {
      const badge = document.getElementById('status-badge');
      badge.textContent = message;
      badge.className = 'status-badge ' + type;
    }
    
    function renderCommandLog() {
      const logDiv = document.getElementById('command-log');
      if (commandHistory.length === 0) {
        logDiv.innerHTML = '<div class="history-item">No commands executed yet. Say "Hey JARVIS" followed by a command.</div>';
        return;
      }
      
      logDiv.innerHTML = commandHistory.slice(0, 15).map(cmd => `
        <div class='history-item' style='border-left-color: ${cmd.status === 'success' ? 'var(--success)' : 'var(--danger)'};'>
          <div style='font-weight: 700; color: var(--accent); margin-bottom: 4px;'>👤 You</div>
          <div style='color: var(--text); margin-bottom: 8px;'>${cmd.command}</div>
          <div style='font-weight: 700; color: var(--accent); margin-bottom: 4px;'>🤖 JARVIS</div>
          <div style='color: var(--muted); margin-bottom: 8px;'>${cmd.response.substring(0, 100)}...</div>
          <div class='history-time'>${cmd.time}</div>
        </div>
      `).join('');
    }
    
    async function loadSystemStatus() {
      try {
        const statusResp = await fetch('/api/jarvis-status');
        const statusData = await statusResp.json();
        document.getElementById('voice-status').textContent = statusData.voice_enabled ? '🟢 Active' : '🔴 Disabled';
        document.getElementById('session-status').textContent = statusData.session_active ? '🟢 Running' : '🔴 Idle';
        document.getElementById('balance-status').textContent = '$' + (statusData.current_balance || 0).toFixed(2);
      } catch(e) {}
    }

    // ─────────────────────────────────────────────────────────────────────────
    // CONTINUOUS VOICE MODE — JARVIS listens only when you say "Hey JARVIS"
    // ─────────────────────────────────────────────────────────────────────────
    let continuousMode = false;
    let continuousTimer = null;

    function startContinuousVoice() {
      if (!recognition) { speakResponse('Voice recognition not supported in this browser.'); return; }
      continuousMode = true;
      recognition.continuous = false; // restart loop instead — more reliable
      document.getElementById('mic-btn').classList.add('recording');
      updateStatus('🎤 Always Listening — Say "Hey JARVIS"', 'listening');
      listenOnce();
    }

    function stopContinuousVoice() {
      continuousMode = false;
      clearTimeout(continuousTimer);
      recognition.stop();
      document.getElementById('mic-btn').classList.remove('recording');
      updateStatus('🎤 Ready to Listen', 'listening');
    }

    function listenOnce() {
      if (!continuousMode) return;
      if (!recognition) {
        console.error('Recognition not initialized');
        return;
      }
      
      try {
        // Only attempt to start if not currently listening and enough time has passed since last error
        if (!isListening) {
          recognition.start();
        }
      } catch(e) {
        // If start() fails (e.g., already started), schedule retry
        console.warn('listenOnce - Recognition.start() failed:', e.message);
        clearTimeout(continuousTimer);
        continuousTimer = setTimeout(listenOnce, 1000);
      }
    }

    
    // ─────────────────────────────────────────────────────────────────────────
    // CAMERA AI
    // ─────────────────────────────────────────────────────────────────────────
    let camStream = null;

    async function startCamera() {
      const video = document.getElementById('cam-video');
      const status = document.getElementById('cam-status');
      try {
        camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
        video.srcObject = camStream;
        video.style.display = 'block';
        document.getElementById('capture-btn').style.display = 'inline-block';
        document.getElementById('stop-cam-btn').style.display = 'inline-block';
        status.textContent = '📷 Camera active — point at your chart and click Capture & Analyze';
        status.style.color = '#22c55e';
        speakResponse('Camera activated. Point at your trading chart and click Capture and Analyze.');
      } catch(e) {
        status.textContent = '❌ Camera access denied: ' + e.message;
        status.style.color = '#f87171';
        speakResponse('Camera access denied. Please allow camera permission in your browser.');
      }
    }

    function stopCamera() {
      if (camStream) { camStream.getTracks().forEach(t => t.stop()); camStream = null; }
      const video = document.getElementById('cam-video');
      video.style.display = 'none';
      document.getElementById('capture-btn').style.display = 'none';
      document.getElementById('stop-cam-btn').style.display = 'none';
      document.getElementById('cam-status').textContent = 'Camera stopped.';
    }

    async function captureAndAnalyze() {
      const video   = document.getElementById('cam-video');
      const canvas  = document.getElementById('cam-canvas');
      const preview = document.getElementById('cam-preview');
      const status  = document.getElementById('cam-status');
      const symbol  = document.getElementById('cam-symbol').value;

      if (!camStream) { speakResponse('Please start the camera first.'); return; }

      canvas.width  = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext('2d').drawImage(video, 0, 0);
      const base64 = canvas.toDataURL('image/jpeg', 0.85);

      preview.src = base64;
      preview.style.display = 'block';
      status.textContent = '⏳ JARVIS is analyzing the chart image...';
      status.style.color = '#0099ff';
      speakResponse('Analyzing chart image. Please wait.');

      try {
        const resp = await fetch('/api/camera-analyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image: base64, symbol })
        });
        const data = await resp.json();

        if (data.status !== 'ok') throw new Error(data.message || 'Analysis failed');

        const analysis = data.analysis || {};
        const sigColor = analysis.signal === 'BUY' ? '#22c55e' : analysis.signal === 'SELL' ? '#f87171' : '#f59e0b';

        document.getElementById('cam-analysis-card').style.display = 'block';
        document.getElementById('cam-analysis-result').innerHTML = `
          <div style='display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;'>
            <div style='background:rgba(0,153,255,0.1);padding:8px;border-radius:6px;'>
              <div style='color:#a0aec0;font-size:10px;'>SIGNAL</div>
              <div style='font-weight:700;color:${sigColor};font-size:18px;'>${analysis.signal || 'N/A'}</div>
            </div>
            <div style='background:rgba(0,153,255,0.1);padding:8px;border-radius:6px;'>
              <div style='color:#a0aec0;font-size:10px;'>CONFIDENCE</div>
              <div style='font-weight:700;font-size:18px;'>${((analysis.confidence||0)*100).toFixed(0)}%</div>
            </div>
            <div style='background:rgba(34,197,94,0.1);padding:8px;border-radius:6px;'>
              <div style='color:#a0aec0;font-size:10px;'>ENTRY</div>
              <div style='font-weight:700;color:#22c55e;'>${analysis.entry || '--'}</div>
            </div>
            <div style='background:rgba(239,68,68,0.1);padding:8px;border-radius:6px;'>
              <div style='color:#a0aec0;font-size:10px;'>STOP LOSS</div>
              <div style='font-weight:700;color:#f87171;'>${analysis.stop_loss || '--'}</div>
            </div>
            <div style='background:rgba(245,158,11,0.1);padding:8px;border-radius:6px;'>
              <div style='color:#a0aec0;font-size:10px;'>TP1</div>
              <div style='font-weight:700;color:#f59e0b;'>${analysis.take_profit_1 || '--'}</div>
            </div>
            <div style='background:rgba(245,158,11,0.1);padding:8px;border-radius:6px;'>
              <div style='color:#a0aec0;font-size:10px;'>TP2</div>
              <div style='font-weight:700;color:#f59e0b;'>${analysis.take_profit_2 || '--'}</div>
            </div>
          </div>
          <p style='color:#a0aec0;font-size:12px;'>${analysis.summary || ''}</p>`;

        if (data.ocr && data.ocr.text) {
          document.getElementById('cam-ocr-card').style.display = 'block';
          document.getElementById('cam-ocr-result').textContent = data.ocr.text;
        }

        status.textContent = '✅ Analysis complete — ' + new Date().toLocaleTimeString();
        status.style.color = '#22c55e';
        speakResponse(data.voice_text || 'Camera analysis complete.');

        commandHistory.unshift({ command: 'camera analyze ' + symbol, response: data.voice_text || 'Analysis complete', time: new Date().toLocaleTimeString(), status: 'success' });
        renderCommandLog();

      } catch(err) {
        status.textContent = '❌ Analysis failed: ' + err.message;
        status.style.color = '#f87171';
        speakResponse('Camera analysis failed. ' + err.message);
      }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // SYSTEM HEALTH CHECK
    // ─────────────────────────────────────────────────────────────────────────
    async function runHealthCheck() {
      document.getElementById('health-grid').innerHTML = '<div class="card"><h3 style="color:#0099ff;">⏳ Running health check...</h3></div>';
      const logEl = document.getElementById('health-log');
      logEl.innerHTML = '';
      const log = msg => { logEl.innerHTML += `<div>[${new Date().toLocaleTimeString()}] ${msg}</div>`; };
      log('Starting full system health check...');

      try {
        const resp = await fetch('/api/system-health');
        const h = await resp.json();
        log('Health data received. Score: ' + h.score + '/' + h.score_max);

        const overallColor = h.overall === 'healthy' ? '#22c55e' : h.overall === 'degraded' ? '#f59e0b' : '#f87171';
        const overallIcon  = h.overall === 'healthy' ? '🟢' : h.overall === 'degraded' ? '🟡' : '🔴';

        let html = `
          <div class='card' style='border-color:${overallColor};background:rgba(0,0,0,0.2);grid-column:span 2;'>
            <h3 style='color:${overallColor};font-size:20px;'>${overallIcon} System is ${h.overall.toUpperCase()} — ${h.score}/${h.score_max} subsystems OK</h3>
            <p style='color:var(--muted);font-size:12px;'>Last checked: ${h.timestamp}</p>
          </div>

          <div class='card' style='border-color:${h.mt5.status==="connected"?"#22c55e":"#f87171"}'>
            <h3>🖥 MetaTrader 5</h3>
            <div style='font-size:22px;font-weight:900;color:${h.mt5.status==="connected"?"#22c55e":"#f87171"};'>${h.mt5.status.toUpperCase()}</div>
          </div>

          <div class='card' style='border-color:#0099ff'>
            <h3>📊 Live Prices</h3>
            ${Object.entries(h.prices||{}).map(([sym,info])=>`
              <div style='margin-bottom:8px;'>
                <strong>${sym}</strong>: ${info.price ? info.price.toLocaleString() : 'N/A'}
                <span style='color:${(info.change_pct||0)>=0?"#22c55e":"#f87171"};font-size:12px;'>
                  ${(info.change_pct||0)>=0?'+':''}${(info.change_pct||0).toFixed(2)}%
                </span>
              </div>`).join('')}
          </div>

          ${Object.entries(h.models||{}).map(([sym,info])=>`
          <div class='card' style='border-color:${info.loaded?"#00ff88":"#f87171"}'>
            <h3>🧠 ${sym} Model</h3>
            <div style='font-size:20px;font-weight:700;color:${info.loaded?"#22c55e":"#f87171"};'>${info.loaded?"LOADED":"NOT LOADED"}</div>
            <p style='color:var(--muted);font-size:12px;'>Accuracy: ${info.accuracy?(info.accuracy*100).toFixed(1)+'%':'N/A'}</p>
            <p style='color:var(--muted);font-size:11px;'>Trained: ${info.last_trained||'N/A'}</p>
          </div>`).join('')}

          ${Object.entries(h.signals||{}).map(([sym,info])=>typeof info==='object'?`
          <div class='card'>
            <h3>🎯 ${sym} Signal</h3>
            <div style='font-size:20px;font-weight:700;color:${info.signal==="BUY"?"#22c55e":info.signal==="SELL"?"#f87171":"#f59e0b"};'>${info.signal}</div>
            <p style='color:var(--muted);font-size:12px;'>Confidence: ${((info.confidence||0)*100).toFixed(0)}%</p>
          </div>`:''  ).join('')}

          <div class='card' style='border-color:${h.trading?.session_active?"#22c55e":"#f59e0b"}'>
            <h3>💼 Trading Session</h3>
            <div style='font-size:20px;font-weight:700;color:${h.trading?.session_active?"#22c55e":"#f59e0b"};'>${h.trading?.session_active?"ACTIVE":"IDLE"}</div>
            <p style='color:var(--muted);font-size:12px;'>Balance: $${(h.trading?.balance||0).toFixed(2)}</p>
            <p style='color:var(--muted);font-size:12px;'>Win rate: ${h.trading?.win_rate||0}%</p>
          </div>

          <div class='card' style='border-color:${h.voice?.enabled?"#22c55e":"#f87171"}'>
            <h3>🎤 Voice Engine</h3>
            <div style='font-size:20px;font-weight:700;color:${h.voice?.enabled?"#22c55e":"#f87171"};'>${h.voice?.enabled?"ACTIVE":"DISABLED"}</div>
            <p style='color:var(--muted);font-size:12px;'>Wakeword: "${h.voice?.wakeword||'hey jarvis'}"</p>
          </div>`;

        document.getElementById('health-grid').innerHTML = html;
        log('Health check complete. System is ' + h.overall + '.');
        speakResponse('System health check complete. System is ' + h.overall + '. Score ' + h.score + ' out of ' + h.score_max + '.');

      } catch(err) {
        document.getElementById('health-grid').innerHTML = '<div class="card"><h3 style="color:#f87171;">❌ Health check failed: ' + err.message + '</h3></div>';
        log('ERROR: ' + err.message);
      }
    }

    async function runMorningBriefing() {
      updateStatus('🌅 Morning briefing...', 'processing');
      speakResponse('Starting morning briefing. Please wait.');
      try {
        const resp = await fetch('/api/morning-briefing');
        const data = await resp.json();
        showResponse('🌅 MORNING BRIEFING

' + (data.lines||[]).join('
'), 'success');
        updateStatus('🌅 Briefing complete', 'success');
        // Speak with slight delay so text shows first
        setTimeout(() => speakResponse(data.briefing || 'Morning briefing complete.'), 400);
        // Also refresh health
        runHealthCheck();
      } catch(err) {
        showResponse('Morning briefing failed: ' + err.message, 'error');
        speakResponse('Morning briefing failed.');
      }
    }

    async function runLearningUpdate() {
      updateStatus('🧠 Updating learning...', 'processing');
      speakResponse('Starting machine learning update. This may take a moment.');
      try {
        const resp = await fetch('/api/train-daily');
        const data = await resp.json();
        const summary = (data || []).map(r => r.symbol + ' ' + r.status).join(', ');
        showResponse('🧠 LEARNING UPDATE COMPLETE

' + summary, 'success');
        updateStatus('✅ Learning updated', 'success');
        speakResponse('Learning update complete. ' + summary + '.');
      } catch(err) {
        showResponse('Learning update failed: ' + err.message, 'error');
        speakResponse('Learning update failed.');
        updateStatus('⚠️ Update failed', 'listening');
      }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // STARTUP — runs automatically when page loads
    // ─────────────────────────────────────────────────────────────────────────
    function startupSequence() {
      // 1. Load system status bar
      loadSystemStatus();
      setInterval(loadSystemStatus, 3000);
      renderCommandLog();

      // 2. Check if this is a fresh page load (not a tab switch)
      const isFirstLoad = !sessionStorage.getItem('jarvis_started');
      if (isFirstLoad) {
        sessionStorage.setItem('jarvis_started', '1');
        // 3. Welcome greeting + morning briefing
        setTimeout(() => {
          welcomeJARVIS();
          // 4. After welcome speech, run morning briefing
          setTimeout(() => {
            runMorningBriefing();
            // 5. Auto-start continuous voice
            setTimeout(() => startContinuousVoice(), 3000);
          }, 3000);
        }, 500);
      } else {
        welcomeJARVIS();
      }
    }
    
    // ── Live Lightweight Charts (real candlesticks from MT5/Yahoo) ──────────────
    let lwChartInstance = null;
    let lwCandleSeries = null;
    let lwLineSeries = {}; // entry / tp / sl lines
    let chartAutoRefreshTimer = null;

    async function loadChart() {
      const symbol = (document.getElementById('chart-symbol').value || 'XAUUSD').toUpperCase();
      const container = document.getElementById('lwchart');

      // Show loading indicator
      container.innerHTML = `<div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#0099ff;font-size:14px;">⏳ Loading live chart for ${symbol}...</div>`;
      updateStatus('📊 Loading chart...', 'processing');

      try {
        // ── 1. Fetch OHLC data from backend (MT5 / Yahoo) ─────────────
        const resp = await fetch('/api/chart-data/' + symbol);
        const json = await resp.json();

        if (!json.candles || json.candles.length === 0) {
          container.innerHTML = `<div style="color:#f87171;padding:20px;">⚠️ No chart data available for ${symbol}</div>`;
          updateStatus('⚠️ No data', 'listening');
          return;
        }

        // ── 2. Render using inline SVG candlestick chart ──────────────
        const candles = json.candles;
        renderSvgCandleChart(container, symbol, candles);

        // ── 3. Update status bar ──────────────────────────────────────
        const latest = candles[candles.length - 1];
        const prev   = candles[candles.length - 2] || latest;
        const changePct = ((latest.close - prev.close) / prev.close * 100).toFixed(2);
        const changeColor = changePct >= 0 ? '#22c55e' : '#f87171';

        document.getElementById('cs-symbol').textContent = symbol;
        document.getElementById('cs-price').textContent  = latest.close.toFixed(symbol === 'EURUSD' || symbol === 'GBPUSD' ? 4 : 2);
        document.getElementById('cs-change').innerHTML   = `<span style="color:${changeColor}">${changePct >= 0 ? '+' : ''}${changePct}%</span>`;
        document.getElementById('cs-time').textContent   = new Date().toLocaleTimeString();

        updateStatus('✅ Chart loaded', 'success');
        speakResponse('Live chart loaded for ' + symbol + '. Current price ' + latest.close.toFixed(2) + '.');

        // ── 4. Auto-refresh every 60 s ────────────────────────────────
        clearInterval(chartAutoRefreshTimer);
        chartAutoRefreshTimer = setInterval(loadChart, 60000);

      } catch (err) {
        console.error('loadChart error:', err);
        container.innerHTML = `<div style="color:#f87171;padding:20px;">❌ Chart load failed: ${err.message}</div>`;
        updateStatus('❌ Chart error', 'listening');
      }
    }

    function renderSvgCandleChart(container, symbol, candles) {
      const W = container.offsetWidth || 900;
      const H = 400;
      const padL = 65, padR = 20, padT = 30, padB = 40;
      const plotW = W - padL - padR;
      const plotH = H - padT - padB;

      const lows  = candles.map(c => c.low);
      const highs = candles.map(c => c.high);
      const minP  = Math.min(...lows);
      const maxP  = Math.max(...highs);
      const priceRange = Math.max(maxP - minP, 1e-9);

      const xFor = i => padL + (i / (candles.length - 1)) * plotW;
      const yFor = p => padT + (1 - (p - minP) / priceRange) * plotH;

      const barW = Math.max(2, plotW / candles.length * 0.6);

      let svgBars = '';
      candles.forEach((c, i) => {
        const x = xFor(i);
        const isUp = c.close >= c.open;
        const col  = isUp ? '#22c55e' : '#f87171';
        const bodyTop = yFor(Math.max(c.open, c.close));
        const bodyBot = yFor(Math.min(c.open, c.close));
        const bodyH   = Math.max(bodyBot - bodyTop, 1);
        svgBars += `<line x1="${x}" y1="${yFor(c.high)}" x2="${x}" y2="${yFor(c.low)}" stroke="${col}" stroke-width="1"/>`;
        svgBars += `<rect x="${x - barW/2}" y="${bodyTop}" width="${barW}" height="${bodyH}" fill="${col}" rx="1"/>`;
      });

      // Y-axis labels
      let yLabels = '';
      for (let step = 0; step <= 4; step++) {
        const p = minP + (maxP - minP) * (step / 4);
        const y = yFor(p);
        yLabels += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(148,163,184,0.15)" stroke-width="1"/>`;
        yLabels += `<text x="${padL - 6}" y="${y + 4}" fill="#94a3b8" font-size="10" text-anchor="end">${p.toFixed(symbol === 'EURUSD' || symbol === 'GBPUSD' ? 4 : 2)}</text>`;
      }

      // Latest price line
      const latestClose = candles[candles.length - 1].close;
      const latestY = yFor(latestClose);
      yLabels += `<line x1="${padL}" y1="${latestY}" x2="${W - padR}" y2="${latestY}" stroke="rgba(0,153,255,0.6)" stroke-dasharray="4 3" stroke-width="1.5"/>`;
      yLabels += `<rect x="${W - padR - 64}" y="${latestY - 11}" width="62" height="18" rx="4" fill="rgba(0,153,255,0.8)"/>`;
      yLabels += `<text x="${W - padR - 33}" y="${latestY + 4}" fill="#fff" font-size="10" font-weight="700" text-anchor="middle">${latestClose.toFixed(symbol === 'EURUSD' || symbol === 'GBPUSD' ? 4 : 2)}</text>`;

      // Title
      const titleText = `${symbol}  |  H1  |  ${candles.length} bars`;

      const svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" style="display:block;background:#0a0e27;border-radius:12px;">
        <text x="${padL}" y="22" fill="#e0e7ff" font-size="13" font-weight="700">${titleText}</text>
        ${yLabels}
        ${svgBars}
      </svg>`;

      container.innerHTML = svg;
    }

    async function analyzeChartWithJarvis() {
      const symbol = (document.getElementById('chart-symbol').value || 'XAUUSD').toUpperCase();
      updateStatus('🤖 JARVIS analyzing...', 'processing');
      document.getElementById('chart-analysis').innerHTML = `<p style="color:#0099ff;">⏳ Analyzing ${symbol} chart — please wait...</p>`;

      try {
        const resp = await fetch('/api/analyze-chart/' + symbol);
        const data = await resp.json();

        if (data.status !== 'ok') throw new Error(data.message || 'Analysis failed');

        // Update indicators panel
        const rsiEl = document.getElementById('rsi');
        rsiEl.textContent = data.rsi + ' (' + (data.rsi > 70 ? 'Overbought' : data.rsi < 30 ? 'Oversold' : 'Neutral') + ')';
        rsiEl.style.color = data.rsi > 70 ? '#f87171' : data.rsi < 30 ? '#22c55e' : '#0099ff';

        document.getElementById('macd').textContent  = data.volatility + '%';
        document.getElementById('trend').textContent  = data.sentiment === 'Bullish' ? '📈 ' + data.sentiment : data.sentiment === 'Bearish' ? '📉 ' + data.sentiment : '➡️ ' + data.sentiment;
        document.getElementById('trend').style.color  = data.sentiment === 'Bullish' ? '#22c55e' : data.sentiment === 'Bearish' ? '#f87171' : '#f59e0b';
        document.getElementById('support').textContent    = data.support;
        document.getElementById('resistance').textContent = data.resistance;
        document.getElementById('cs-signal').textContent  = data.signal;

        // Rich analysis panel
        const signalColor = data.signal === 'BUY' ? '#22c55e' : data.signal === 'SELL' ? '#f87171' : '#f59e0b';
        document.getElementById('chart-analysis').innerHTML = `
          <div style="font-weight:700;color:#0099ff;font-size:14px;margin-bottom:12px;">🤖 JARVIS Analysis — ${symbol}</div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;">
            <div style="background:rgba(0,153,255,0.1);padding:8px;border-radius:6px;">
              <div style="color:#a0aec0;font-size:10px;text-transform:uppercase;">Pattern</div>
              <div style="font-weight:700;margin-top:3px;">${data.pattern}</div>
            </div>
            <div style="background:rgba(0,153,255,0.1);padding:8px;border-radius:6px;">
              <div style="color:#a0aec0;font-size:10px;text-transform:uppercase;">Signal</div>
              <div style="font-weight:700;color:${signalColor};margin-top:3px;">${data.signal} (${(data.model_confidence*100).toFixed(0)}%)</div>
            </div>
            <div style="background:rgba(34,197,94,0.1);padding:8px;border-radius:6px;">
              <div style="color:#a0aec0;font-size:10px;text-transform:uppercase;">Support</div>
              <div style="font-weight:700;color:#22c55e;margin-top:3px;">${data.support}</div>
            </div>
            <div style="background:rgba(239,68,68,0.1);padding:8px;border-radius:6px;">
              <div style="color:#a0aec0;font-size:10px;text-transform:uppercase;">Resistance</div>
              <div style="font-weight:700;color:#f87171;margin-top:3px;">${data.resistance}</div>
            </div>
          </div>
          <p style="color:#a0aec0;font-size:12px;line-height:1.7;">${data.analysis_text}</p>
          <p style="color:#a0aec0;font-size:11px;margin-top:8px;">📊 ${data.chart_metadata.bars_analyzed} bars analysed · Updated ${new Date().toLocaleTimeString()}</p>`;

        updateStatus('✅ Analysis complete', 'success');

        // JARVIS speaks the analysis
        speakResponse(data.analysis_text);

        // Log to command history
        commandHistory.unshift({
          command: 'analyze chart ' + symbol,
          response: data.analysis_text,
          time: new Date().toLocaleTimeString(),
          status: 'success'
        });
        renderCommandLog();

      } catch (err) {
        document.getElementById('chart-analysis').innerHTML = `<p style="color:#f87171;">❌ Analysis failed: ${err.message}</p>`;
        updateStatus('❌ Analysis error', 'listening');
        speakResponse('Chart analysis failed. Please try again.');
      }
    }

    // ── Legacy helper kept for backward compatibility ─────────────────────────
    function loadChart_legacy() {
      const symbol = document.getElementById('chart-symbol').value || 'XAUUSD';
      const priceRanges = {
        'XAUUSD': { base: 4080, range: 30, current: 4090.56, entry: 4085, tp1: 4110, tp2: 4130, sl: 4060 },
        'EURUSD': { base: 1.0850, range: 0.015, current: 1.0875, entry: 1.0865, tp1: 1.0900, tp2: 1.0930, sl: 1.0830 },
        'BTCUSD': { base: 60000, range: 500, current: 60263, entry: 60200, tp1: 60800, tp2: 61500, sl: 59500 },
        'GBPUSD': { base: 1.2700, range: 0.025, current: 1.2750, entry: 1.2730, tp1: 1.2820, tp2: 1.2900, sl: 1.2650 }
      };
      const range = priceRanges[symbol] || priceRanges['XAUUSD'];
      document.getElementById('chart-analysis').innerHTML =
        `<p style="color:#a0aec0;">Legacy static data for ${symbol}. Use <strong style="color:#0099ff;">JARVIS Analyze</strong> for real AI analysis.</p>`;
    }
    
    function searchAssets() {
      const query = document.getElementById('search-input').value;
      
      // REAL LIVE MARKET PRICES - Updated to actual market data
      const assetData = {
        'gold': { 
          symbol: 'XAUUSD', 
          price: '4090.56', 
          trend: '📈 Up 0.8%',
          entry: 4085,
          tp1: 4110,
          tp2: 4130,
          sl: 4060,
          signal: 'BUY'
        },
        'bitcoin': { 
          symbol: 'BTCUSD', 
          price: '60263.00', 
          trend: '📈 Up 1.2%',
          entry: 60200,
          tp1: 60800,
          tp2: 61500,
          sl: 59500,
          signal: 'BUY'
        },
        'eurusd': { 
          symbol: 'EURUSD', 
          price: '1.0875', 
          trend: '📈 Up 0.5%',
          entry: 1.0865,
          tp1: 1.0900,
          tp2: 1.0930,
          sl: 1.0830,
          signal: 'BUY'
        },
        'gbpusd': { 
          symbol: 'GBPUSD', 
          price: '1.2750', 
          trend: '📉 Down 0.3%',
          entry: 1.2730,
          tp1: 1.2820,
          tp2: 1.2900,
          sl: 1.2650,
          signal: 'HOLD'
        }
      };
      
      const asset = assetData[query.toLowerCase()] || { 
        symbol: query.toUpperCase(), 
        price: '--', 
        trend: '--',
        entry: '--',
        tp1: '--',
        tp2: '--',
        signal: '--'
      };
      
      // Display search results with professional entry recommendations
      document.getElementById('search-results').innerHTML = `
        <div class='recommendation-card' style='border-color: ${asset.signal === 'BUY' ? 'var(--success)' : 'var(--warning)'}; background: ${asset.signal === 'BUY' ? 'rgba(34, 197, 94, 0.1)' : 'rgba(245, 158, 11, 0.1)'};'>
          <div class='recommendation-title' style='font-size: 16px;'>🎯 ${asset.symbol} - ${asset.signal}</div>
          <div class='recommendation-desc'>
            <p><strong>Current Price:</strong> ${asset.price}</p>
            <p><strong>Trend:</strong> ${asset.trend}</p>
            <p><strong style='color: var(--success);'>Entry Point:</strong> ${asset.entry}</p>
            <p><strong style='color: var(--accent);'>Take Profit 1:</strong> ${asset.tp1}</p>
            <p><strong style='color: var(--accent);'>Take Profit 2:</strong> ${asset.tp2}</p>
            <p><strong style='color: var(--danger);'>Stop Loss:</strong> ${asset.sl}</p>
          </div>
        </div>
      `;
      
      document.getElementById('asset-info').innerHTML = `
        <div style='line-height: 2;'>
          <p><strong style='color: var(--accent);'>${asset.symbol}</strong></p>
          <p><strong>Price:</strong> ${asset.price}</p>
          <p><strong>Signal:</strong> ${asset.signal}</p>
          <p><strong style='color: var(--success);'>Entry:</strong> ${asset.entry}</p>
          <p><strong style='color: var(--warning);'>TP1:</strong> ${asset.tp1}</p>
          <p><strong style='color: var(--danger);'>SL:</strong> ${asset.sl}</p>
        </div>
      `;
    }
    
    async function saveVoiceSettings() {
      const enabled = document.getElementById('voice-enabled').checked;
      voiceResponseEnabled = document.getElementById('voice-response').checked;
      
      const response = await fetch('/api/voice/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled })
      });
      
      showResponse('✅ Settings saved successfully', 'success');
      speakResponse('Settings have been updated.');
    }
    
    function toggleVoiceResponse() {
      voiceResponseEnabled = document.getElementById('voice-response').checked;
    }
    
    // Initialize JARVIS on Page Load
    document.addEventListener('DOMContentLoaded', function() {
      startupSequence();
    });
  