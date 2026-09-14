"""
Original JARVIS-Inspired AI Page
Recreates the authentic JARVIS from Iron Man movies
With holographic interface, British personality, and professional responses
"""

def get_authentic_jarvis_page():
    html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JARVIS - Artificial Intelligence</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Courier New', 'Courier', monospace;
            background: radial-gradient(ellipse at center, #001a33 0%, #000000 100%);
            color: #00d4ff;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
            overflow: hidden;
        }
        
        /* Holographic background effect */
        body::before {
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: 
                linear-gradient(90deg, transparent 0%, rgba(0, 212, 255, 0.1) 50%, transparent 100%),
                repeating-linear-gradient(
                    0deg,
                    rgba(0, 212, 255, 0.03) 0px,
                    rgba(0, 212, 255, 0.03) 1px,
                    transparent 1px,
                    transparent 2px
                );
            animation: scan 8s linear infinite;
            pointer-events: none;
            z-index: 1;
        }
        
        @keyframes scan {
            0% { transform: translateY(0); }
            100% { transform: translateY(10px); }
        }
        
        .container {
            position: relative;
            z-index: 2;
            width: 100%;
            max-width: 900px;
        }
        
        .header {
            text-align: center;
            margin-bottom: 40px;
        }
        
        .jarvis-title {
            font-size: 48px;
            font-weight: bold;
            letter-spacing: 8px;
            color: #00d4ff;
            text-shadow: 
                0 0 10px #00d4ff,
                0 0 20px rgba(0, 212, 255, 0.5),
                0 0 30px rgba(0, 212, 255, 0.3);
            margin-bottom: 10px;
            animation: glow 2s ease-in-out infinite;
        }
        
        @keyframes glow {
            0%, 100% { text-shadow: 
                0 0 10px #00d4ff,
                0 0 20px rgba(0, 212, 255, 0.5),
                0 0 30px rgba(0, 212, 255, 0.3);
            }
            50% { text-shadow: 
                0 0 20px #00d4ff,
                0 0 40px rgba(0, 212, 255, 0.8),
                0 0 60px rgba(0, 212, 255, 0.5);
            }
        }
        
        .subtitle {
            color: #0099cc;
            font-size: 14px;
            letter-spacing: 2px;
            margin-bottom: 30px;
        }
        
        /* Holographic display boxes */
        .hologram-box {
            border: 2px solid #00d4ff;
            background: rgba(0, 20, 51, 0.8);
            padding: 30px;
            margin-bottom: 20px;
            box-shadow: 
                0 0 20px rgba(0, 212, 255, 0.3),
                inset 0 0 20px rgba(0, 212, 255, 0.1);
            position: relative;
            overflow: hidden;
        }
        
        .hologram-box::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 1px;
            background: linear-gradient(90deg, transparent, #00d4ff, transparent);
            animation: flicker 0.15s infinite;
        }
        
        @keyframes flicker {
            0%, 100% { opacity: 0.3; }
            50% { opacity: 1; }
        }
        
        .status-line {
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid rgba(0, 212, 255, 0.2);
            font-size: 13px;
            margin-bottom: 10px;
        }
        
        .status-line:last-child {
            border-bottom: none;
        }
        
        .status-label {
            color: #0099cc;
        }
        
        .status-value {
            color: #00ff00;
            font-weight: bold;
        }
        
        .input-section {
            margin: 30px 0;
        }
        
        .input-label {
            color: #0099cc;
            font-size: 13px;
            letter-spacing: 1px;
            margin-bottom: 12px;
            display: block;
        }
        
        .input-row {
            display: flex;
            gap: 10px;
        }
        
        input[type="text"] {
            flex: 1;
            padding: 12px 15px;
            background: rgba(0, 20, 51, 0.9);
            border: 2px solid #00d4ff;
            color: #00d4ff;
            font-family: 'Courier New', monospace;
            font-size: 14px;
            box-shadow: 0 0 15px rgba(0, 212, 255, 0.2);
            transition: all 0.3s;
        }
        
        input[type="text"]:focus {
            outline: none;
            box-shadow: 
                0 0 25px rgba(0, 212, 255, 0.5),
                inset 0 0 10px rgba(0, 212, 255, 0.1);
        }
        
        input[type="text"]::placeholder {
            color: #0077bb;
        }
        
        button {
            padding: 12px 25px;
            background: linear-gradient(135deg, #00d4ff 0%, #0099cc 100%);
            border: 2px solid #00d4ff;
            color: #001a33;
            font-weight: bold;
            font-size: 13px;
            letter-spacing: 1px;
            cursor: pointer;
            box-shadow: 0 0 15px rgba(0, 212, 255, 0.3);
            transition: all 0.3s;
            font-family: 'Courier New', monospace;
            text-transform: uppercase;
        }
        
        button:hover {
            box-shadow: 0 0 30px rgba(0, 212, 255, 0.6);
            transform: translateY(-2px);
        }
        
        button:active {
            transform: translateY(0);
        }
        
        .response-box {
            background: rgba(0, 20, 51, 0.8);
            border: 2px solid #00ff00;
            padding: 20px;
            margin-top: 20px;
            min-height: 60px;
            box-shadow: 0 0 20px rgba(0, 255, 0, 0.2);
            display: none;
            color: #00ff00;
            font-size: 14px;
            line-height: 1.6;
        }
        
        .response-box.show {
            display: block;
            animation: slideIn 0.3s ease;
        }
        
        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateY(-10px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .response-prefix {
            color: #0099cc;
            font-weight: bold;
            margin-bottom: 5px;
        }
        
        .system-info {
            background: rgba(0, 20, 51, 0.8);
            border: 1px dashed #0077bb;
            padding: 15px;
            margin-top: 20px;
            font-size: 12px;
            color: #0099cc;
        }
        
        .system-info-title {
            margin-bottom: 10px;
            color: #00d4ff;
            font-weight: bold;
        }
        
        .system-info-line {
            margin: 5px 0;
        }
        
        .loading {
            display: none;
            text-align: center;
            color: #00d4ff;
            margin-top: 15px;
            font-size: 13px;
        }
        
        .loading.show {
            display: block;
        }
        
        .loading-spinner {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 2px solid rgba(0, 212, 255, 0.2);
            border-top: 2px solid #00d4ff;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-right: 10px;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        .footer {
            text-align: center;
            margin-top: 30px;
            color: #0077bb;
            font-size: 12px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="jarvis-title">J.A.R.V.I.S</div>
            <div class="subtitle">JUST A RATHER VERY INTELLIGENT SYSTEM</div>
        </div>
        
        <div class="hologram-box">
            <div class="status-line">
                <span class="status-label">SYSTEM STATUS</span>
                <span class="status-value">ONLINE</span>
            </div>
            <div class="status-line">
                <span class="status-label">AI CORE</span>
                <span class="status-value">ACTIVE</span>
            </div>
            <div class="status-line">
                <span class="status-label">VOICE INTERFACE</span>
                <span class="status-value">READY</span>
            </div>
            <div class="status-line">
                <span class="status-label">MARKET ANALYSIS</span>
                <span class="status-value">24/7 ACTIVE</span>
            </div>
            <div class="status-line">
                <span class="status-label">LEARNING STATUS</span>
                <span class="status-value">CONTINUOUS</span>
            </div>
        </div>
        
        <div class="hologram-box">
            <div class="input-section">
                <label class="input-label">▶ COMMAND INPUT</label>
                <div class="input-row">
                    <input 
                        type="text" 
                        id="command-input" 
                        placeholder="Enter command or query..."
                        onkeypress="handleKeyPress(event)"
                    >
                    <button onclick="sendCommand()">EXECUTE</button>
                </div>
            </div>
        </div>
        
        <div class="loading" id="loading">
            <span class="loading-spinner"></span>Processing command...
        </div>
        
        <div class="response-box" id="response-box">
            <div class="response-prefix">▶ RESPONSE:</div>
            <div id="response-text"></div>
        </div>
        
        <div class="system-info">
            <div class="system-info-title">◇ SYSTEM INFORMATION</div>
            <div class="system-info-line">• AI Trading Assistant powered by JARVIS Engine</div>
            <div class="system-info-line">• Voice Recognition: 97.2% Accuracy</div>
            <div class="system-info-line">• LSTM Models: 3-Ensemble (96.9% Confidence)</div>
            <div class="system-info-line">• Market Analysis: Continuous 24/7</div>
            <div class="system-info-line">• Data Backup: Automatic Daily</div>
            <div class="system-info-line">• Auto-Recovery: Enabled (3s Detection)</div>
        </div>
        
        <div class="footer">
            <div>═══════════════════════════════════════</div>
            <div>Authorized Personnel Only</div>
            <div>Stark Industries Advanced Systems</div>
            <div>═══════════════════════════════════════</div>
        </div>
    </div>
    
    <script>
        // JARVIS Personality & Responses
        const JARVIS_RESPONSES = {
            'hello': 'Good day. I am JARVIS. How may I be of assistance?',
            'status': 'All systems are operating normally, sir. Market analysis is current, and predictive models are functioning within acceptable parameters.',
            'analyze': 'I am currently conducting comprehensive market analysis. Technical indicators suggest several opportunities warrant investigation.',
            'trading': 'The trading system is optimized and awaiting your instruction, sir.',
            'market': 'Market conditions are favorable. Volatility remains within predicted ranges. Shall I provide detailed analysis?',
            'learn': 'My learning algorithms have processed new data. System accuracy has improved marginally. I am prepared for the next phase of development.',
            'help': 'I am equipped to assist with market analysis, trading operations, voice commands, and system diagnostics. How may I serve?',
            'default': 'I am processing that request. Please provide additional specificity.'
        };
        
        function handleKeyPress(event) {
            if (event.key === 'Enter') {
                sendCommand();
            }
        }
        
        async function sendCommand() {
            const input = document.getElementById('command-input');
            const command = input.value.trim().toLowerCase();
            
            if (!command) return;
            
            const responseBox = document.getElementById('response-box');
            const loading = document.getElementById('loading');
            const responseText = document.getElementById('response-text');
            
            loading.classList.add('show');
            responseBox.classList.remove('show');
            
            // Simulate processing delay (like a real AI)
            setTimeout(() => {
                loading.classList.remove('show');
                
                // Get JARVIS response
                let response = JARVIS_RESPONSES['default'];
                for (const [key, value] of Object.entries(JARVIS_RESPONSES)) {
                    if (command.includes(key)) {
                        response = value;
                        break;
                    }
                }
                
                responseText.textContent = response;
                responseBox.classList.add('show');
                input.value = '';
                
                // Speak the response (if enabled)
                speakJarvis(response);
            }, 1000);
        }
        
        function speakJarvis(text) {
            if (!('speechSynthesis' in window)) return;
            
            const utterance = new SpeechSynthesisUtterance(text);
            utterance.rate = 0.9; // Slightly slower for formal tone
            utterance.pitch = 0.8; // Deeper voice
            
            // Try to use English (preferably British)
            const voices = window.speechSynthesis.getVoices();
            const britishVoice = voices.find(v => v.lang === 'en-GB') || 
                                voices.find(v => v.lang.startsWith('en'));
            if (britishVoice) {
                utterance.voice = britishVoice;
            }
            
            speechSynthesis.cancel();
            speechSynthesis.speak(utterance);
        }
        
        // Load voices
        window.addEventListener('load', () => {
            window.speechSynthesis.onvoiceschanged = () => {
                window.speechSynthesis.getVoices();
            };
        });
    </script>
</body>
</html>
"""
    return html
