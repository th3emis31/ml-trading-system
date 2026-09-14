"""
JARVIS Text-Based Voice Alternative
Accepts typed commands when microphone/speech isn't working
"""

def get_text_voice_page():
    """Return HTML page for text-based voice interface (no speech API needed)"""
    
    html = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JARVIS - Text Voice Interface (Firewall Bypass)</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Courier New', monospace;
            background: linear-gradient(135deg, #0a0e27 0%, #16213e 100%);
            color: #0099ff;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        
        .container {
            background: rgba(10, 14, 39, 0.9);
            border: 2px solid #0099ff;
            border-radius: 12px;
            padding: 40px;
            max-width: 600px;
            width: 100%;
            box-shadow: 0 0 60px rgba(0, 153, 255, 0.3);
        }
        
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        
        .logo {
            font-size: 48px;
            font-weight: bold;
            letter-spacing: 4px;
            margin-bottom: 10px;
            animation: pulse 2s infinite;
        }
        
        @keyframes pulse {
            0%, 100% { text-shadow: 0 0 10px #0099ff; }
            50% { text-shadow: 0 0 30px #0099ff; }
        }
        
        .subtitle {
            color: #a0aec0;
            font-size: 14px;
            margin-bottom: 5px;
        }
        
        .mode-badge {
            display: inline-block;
            background: #ff3333;
            color: white;
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 12px;
            margin-top: 10px;
        }
        
        .status-box {
            background: rgba(0, 153, 255, 0.1);
            border: 1px solid #0099ff;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 13px;
            line-height: 1.6;
        }
        
        .status-item {
            display: flex;
            align-items: center;
            margin-bottom: 8px;
        }
        
        .status-item:last-child {
            margin-bottom: 0;
        }
        
        .status-icon {
            margin-right: 10px;
            font-size: 16px;
        }
        
        .input-group {
            margin-bottom: 20px;
        }
        
        label {
            display: block;
            margin-bottom: 8px;
            color: #0099ff;
            font-size: 14px;
            font-weight: bold;
        }
        
        .input-row {
            display: flex;
            gap: 10px;
        }
        
        input[type="text"] {
            flex: 1;
            padding: 12px 15px;
            background: rgba(0, 153, 255, 0.05);
            border: 1px solid #0099ff;
            color: #0099ff;
            font-family: 'Courier New', monospace;
            font-size: 14px;
            border-radius: 6px;
            transition: all 0.3s;
        }
        
        input[type="text"]:focus {
            outline: none;
            background: rgba(0, 153, 255, 0.15);
            box-shadow: 0 0 20px rgba(0, 153, 255, 0.3);
        }
        
        input[type="text"]::placeholder {
            color: #a0aec0;
        }
        
        button {
            padding: 12px 20px;
            background: linear-gradient(135deg, #0099ff, #00ccff);
            border: none;
            color: white;
            font-weight: bold;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.3s;
            font-family: 'Courier New', monospace;
        }
        
        button:hover {
            box-shadow: 0 0 20px rgba(0, 153, 255, 0.6);
            transform: translateY(-2px);
        }
        
        button:active {
            transform: translateY(0);
        }
        
        .response-box {
            background: rgba(34, 197, 94, 0.1);
            border: 1px solid #22c55e;
            padding: 15px;
            border-radius: 8px;
            margin-top: 20px;
            display: none;
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
        
        .response-label {
            color: #22c55e;
            font-size: 12px;
            margin-bottom: 8px;
            font-weight: bold;
        }
        
        .response-text {
            color: #22c55e;
            font-size: 14px;
            line-height: 1.6;
            word-wrap: break-word;
        }
        
        .error-box {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid #ef4444;
            padding: 15px;
            border-radius: 8px;
            margin-top: 20px;
            display: none;
            color: #ef4444;
            font-size: 14px;
        }
        
        .error-box.show {
            display: block;
        }
        
        .examples {
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid rgba(0, 153, 255, 0.2);
        }
        
        .examples-title {
            color: #0099ff;
            font-size: 13px;
            font-weight: bold;
            margin-bottom: 10px;
        }
        
        .example-item {
            background: rgba(0, 153, 255, 0.05);
            padding: 10px;
            margin-bottom: 8px;
            border-radius: 4px;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s;
            color: #a0aec0;
        }
        
        .example-item:hover {
            background: rgba(0, 153, 255, 0.15);
            color: #0099ff;
        }
        
        .loading {
            display: none;
            text-align: center;
            color: #0099ff;
            margin-top: 15px;
        }
        
        .loading.show {
            display: block;
        }
        
        .spinner {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 2px solid rgba(0, 153, 255, 0.2);
            border-top: 2px solid #0099ff;
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
            padding-top: 20px;
            border-top: 1px solid rgba(0, 153, 255, 0.2);
            color: #a0aec0;
            font-size: 12px;
        }
        
        .back-link {
            color: #0099ff;
            text-decoration: none;
            cursor: pointer;
            margin-top: 10px;
        }
        
        .back-link:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">J.A.R.V.I.S</div>
            <div class="subtitle">JARVIS Voice Control System</div>
            <div class="mode-badge">TEXT MODE - NO SPEECH API NEEDED</div>
        </div>
        
        <div class="status-box">
            <div class="status-item">
                <span class="status-icon">✓</span>
                <span>Text interface active (firewall bypass)</span>
            </div>
            <div class="status-item">
                <span class="status-icon">✓</span>
                <span>No Google API required</span>
            </div>
            <div class="status-item">
                <span class="status-icon">✓</span>
                <span>All JARVIS commands available</span>
            </div>
            <div class="status-item">
                <span class="status-icon">⚠</span>
                <span>Network error: Using text fallback</span>
            </div>
        </div>
        
        <div class="input-group">
            <label for="command-input">📝 Type Your Command:</label>
            <div class="input-row">
                <input 
                    type="text" 
                    id="command-input" 
                    placeholder='e.g., "Hey JARVIS, analyze EURUSD"'
                    onkeypress="handleKeyPress(event)"
                >
                <button onclick="sendCommand()">Send</button>
            </div>
        </div>
        
        <div class="error-box" id="error-box"></div>
        <div class="response-box" id="response-box">
            <div class="response-label">📢 JARVIS Response:</div>
            <div class="response-text" id="response-text"></div>
        </div>
        <div class="loading" id="loading">
            <span class="spinner"></span>Processing your command...
        </div>
        
        <div class="examples">
            <div class="examples-title">📌 Try These Commands:</div>
            <div class="example-item" onclick="setCommand('Hey JARVIS, hello')">🎤 Hey JARVIS, hello</div>
            <div class="example-item" onclick="setCommand('analyze EURUSD')">📊 Analyze EURUSD</div>
            <div class="example-item" onclick="setCommand('price of gold')">💰 Price of gold</div>
            <div class="example-item" onclick="setCommand('trading signals')">🔔 Trading signals</div>
            <div class="example-item" onclick="setCommand('market analysis')">📈 Market analysis</div>
            <div class="example-item" onclick="setCommand('what should I trade')">🎯 What should I trade</div>
        </div>
        
        <div class="footer">
            <div>Running JARVIS on port 5001</div>
            <div style="margin-top: 10px;">
                <a class="back-link" href="/">← Back to Dashboard</a>
            </div>
            <div style="margin-top: 10px; font-size: 11px; color: #666;">
                💡 Tip: To fix speech API, run: fix_firewall_jarvis_voice.bat as Administrator
            </div>
        </div>
    </div>
    
    <script>
        const API_BASE = 'http://127.0.0.1:5001';
        
        function setCommand(text) {
            document.getElementById('command-input').value = text;
        }
        
        function handleKeyPress(event) {
            if (event.key === 'Enter') {
                sendCommand();
            }
        }
        
        async function sendCommand() {
            const input = document.getElementById('command-input');
            const command = input.value.trim();
            
            if (!command) {
                showError('Please type a command');
                return;
            }
            
            const responseBox = document.getElementById('response-box');
            const errorBox = document.getElementById('error-box');
            const loading = document.getElementById('loading');
            
            // Clear previous responses
            responseBox.classList.remove('show');
            errorBox.classList.remove('show');
            loading.classList.add('show');
            
            try {
                // Send command to JARVIS
                const response = await fetch('/api/jarvis/process-command', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        command: command,
                        source: 'text_interface'
                    })
                });
                
                loading.classList.remove('show');
                
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                
                const data = await response.json();
                
                if (data.status === 'ok') {
                    showResponse(data.response || data.message || 'Command processed');
                    input.value = '';
                } else {
                    showError(data.message || 'Command failed');
                }
            } catch (error) {
                loading.classList.remove('show');
                showError(`Error: ${error.message}`);
            }
        }
        
        function showResponse(text) {
            const responseBox = document.getElementById('response-box');
            const responseText = document.getElementById('response-text');
            const errorBox = document.getElementById('error-box');
            
            errorBox.classList.remove('show');
            responseText.innerHTML = escapeHtml(text);
            responseBox.classList.add('show');
        }
        
        function showError(text) {
            const errorBox = document.getElementById('error-box');
            const responseBox = document.getElementById('response-box');
            
            responseBox.classList.remove('show');
            errorBox.textContent = text;
            errorBox.classList.add('show');
        }
        
        function escapeHtml(text) {
            const map = {
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#039;'
            };
            return text.replace(/[&<>"']/g, m => map[m]);
        }
    </script>
</body>
</html>
"""
    
    return html


# Flask endpoint to register in app.py
FLASK_ENDPOINT = '''
@app.route('/jarvis-voice-text', methods=['GET'])
def jarvis_voice_text():
    """Text-based voice interface (firewall bypass)"""
    from jarvis_text_voice_interface import get_text_voice_page
    return get_text_voice_page()
'''
