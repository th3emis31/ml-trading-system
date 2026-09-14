"""
JARVIS Permanent Watchdog Keeper
Ensures Flask never stops. Auto-restarts if it crashes.
Runs in background and monitors Flask continuously.
"""

import subprocess
import time
import os
import sys
from pathlib import Path

class JARVISWatchdog:
    def __init__(self):
        self.app_dir = Path(__file__).parent
        self.run_file = self.app_dir / "run.py"
        self.flask_process = None
        self.running = True
        self.restart_delay = 2
        self.max_restart_delay = 30
        
    def start_flask(self):
        """Start the Flask server"""
        print("[WATCHDOG] Starting JARVIS Flask Server...")
        try:
            self.flask_process = subprocess.Popen(
                [sys.executable, str(self.run_file)],
                cwd=str(self.app_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            print(f"[WATCHDOG] ✅ Flask started (PID: {self.flask_process.pid})")
            return True
        except Exception as e:
            print(f"[WATCHDOG] ❌ Failed to start Flask: {e}")
            return False
    
    def check_flask(self):
        """Check if Flask is still running"""
        if self.flask_process is None:
            return False
        
        # Check if process is still alive
        if self.flask_process.poll() is None:
            return True  # Still running
        else:
            print("[WATCHDOG] ⚠️  Flask crashed!")
            return False
    
    def keep_alive(self):
        """Main watchdog loop - keeps Flask running forever"""
        print("[WATCHDOG] Starting JARVIS Permanent Watchdog...")
        print("[WATCHDOG] System will monitor Flask and auto-restart if needed")
        print("[WATCHDOG] Press Ctrl+C to stop the watchdog")
        print("")

        while self.running:
            try:
                # Start Flask if not running
                if self.flask_process is None or not self.check_flask():
                    if self.flask_process is not None:
                        print(f"[WATCHDOG] Attempting restart...")
                        try:
                            self.flask_process.terminate()
                        except:
                            pass
                        try:
                            self.flask_process.wait(timeout=3)
                        except:
                            try:
                                self.flask_process.kill()
                            except:
                                pass

                    if not self.start_flask():
                        print(f"[WATCHDOG] Retry in {self.restart_delay}s...")
                        time.sleep(self.restart_delay)
                        self.restart_delay = min(self.restart_delay * 2, self.max_restart_delay)
                        continue
                    self.restart_delay = 2
                
                # Check every 5 seconds
                time.sleep(5)
                
            except KeyboardInterrupt:
                print("\n[WATCHDOG] Received stop signal. Cleaning up...")
                if self.flask_process:
                    self.flask_process.terminate()
                    try:
                        self.flask_process.wait(timeout=3)
                    except:
                        self.flask_process.kill()
                break
            except Exception as e:
                print(f"[WATCHDOG] ⚠️  Error: {e}")
                time.sleep(self.restart_delay)
                self.restart_delay = min(self.restart_delay * 2, self.max_restart_delay)

if __name__ == "__main__":
    watchdog = JARVISWatchdog()
    watchdog.keep_alive()
