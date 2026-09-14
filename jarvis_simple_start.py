"""
Simple JARVIS Watchdog - Minimal Flask version for troubleshooting
Tries app.py first, if it fails uses minimal Flask
"""

import subprocess
import sys
import time
import os

def start_full_app():
    """Try to start full app.py"""
    print("[WATCHDOG] Attempting to start full JARVIS app...")
    try:
        proc = subprocess.Popen(
            [sys.executable, "app.py"],
            cwd=os.path.dirname(__file__),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print(f"[WATCHDOG] ✅ App started (PID: {proc.pid})")
        return proc
    except Exception as e:
        print(f"[WATCHDOG] ❌ Full app failed: {e}")
        return None

def start_minimal_app():
    """Start minimal Flask if full app fails"""
    print("[WATCHDOG] Starting minimal Flask server...")
    
    minimal_code = '''
from flask import Flask
app = Flask(__name__)

@app.route("/")
def home():
    return "<h1>🤖 JARVIS Emergency Mode</h1><p>System recovering...</p>"

@app.route("/health")
def health():
    return {"status": "alive"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
'''
    
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", minimal_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print(f"[WATCHDOG] ✅ Minimal Flask started (PID: {proc.pid})")
        return proc
    except Exception as e:
        print(f"[WATCHDOG] ❌ Minimal app failed: {e}")
        return None

def main():
    print("[WATCHDOG] JARVIS Simple Watchdog Starting...")
    print("")
    
    flask_process = None
    failures = 0
    max_failures = 3
    
    while True:
        try:
            # Check if process still running
            if flask_process is None or flask_process.poll() is not None:
                print("[WATCHDOG] Flask not running, starting...")
                
                # Try full app first
                flask_process = start_full_app()
                
                # If full app fails, use minimal
                if flask_process is None:
                    flask_process = start_minimal_app()
                    failures += 1
                else:
                    failures = 0
                
                if failures > max_failures:
                    print("[WATCHDOG] ❌ Too many failures. Giving up.")
                    break
            
            time.sleep(5)
            
        except KeyboardInterrupt:
            print("\n[WATCHDOG] Stopping...")
            if flask_process:
                flask_process.terminate()
                try:
                    flask_process.wait(timeout=3)
                except:
                    flask_process.kill()
            break
        except Exception as e:
            print(f"[WATCHDOG] Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
