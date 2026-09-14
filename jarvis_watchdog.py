#!/usr/bin/env python3
"""
JARVIS Watchdog - Keeps the trading system running 24/7
Automatically restarts the Flask server if it crashes
"""

import os
import sys
import time
import subprocess
import socket
import logging
from datetime import datetime
from pathlib import Path

# Setup logging
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)
log_file = log_dir / "jarvis_watchdog.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("jarvis.watchdog")

class JARVISWatchdog:
    def __init__(self):
        self.app_dir = Path(__file__).parent
        self.python_exe = self._find_python()
        self.port = 5000
        self.server_process = None
        self.restart_count = 0
        self.max_consecutive_failures = 5
        self.consecutive_failures = 0
        
    def _find_python(self):
        """Find Python executable"""
        candidates = [
            "C:\\Users\\User\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
            "C:\\Users\\User\\AppData\\Local\\Programs\\Python\\Python311\\python.exe",
            "C:\\Program Files\\Python312\\python.exe",
            "python3",
            "python"
        ]
        
        for python in candidates:
            try:
                result = subprocess.run([python, "--version"], capture_output=True, timeout=5)
                if result.returncode == 0:
                    logger.info(f"Using Python: {python}")
                    return python
            except:
                continue
        
        logger.error("Python not found!")
        sys.exit(1)
    
    def is_server_healthy(self):
        """Check if Flask server is responding"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex(('127.0.0.1', self.port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            return False
    
    def start_server(self):
        """Start the Flask server"""
        try:
            logger.info(f"Starting JARVIS server (restart #{self.restart_count + 1})...")
            
            self.server_process = subprocess.Popen(
                [self.python_exe, "run.py"],
                cwd=str(self.app_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP  # Windows: new process group
            )
            
            logger.info(f"Server process started (PID: {self.server_process.pid})")
            self.restart_count += 1
            
            # Wait for server to be healthy
            max_wait = 30
            start_time = time.time()
            while time.time() - start_time < max_wait:
                if self.is_server_healthy():
                    logger.info("✅ Server is healthy and responding")
                    self.consecutive_failures = 0
                    return True
                time.sleep(1)
            
            logger.warning("Server started but slow to respond (still waiting...)")
            self.consecutive_failures = 0
            return True
            
        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            self.consecutive_failures += 1
            return False
    
    def stop_server(self):
        """Stop the Flask server gracefully"""
        if self.server_process:
            try:
                logger.info("Stopping server gracefully...")
                self.server_process.terminate()
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("Server didn't stop gracefully, forcing...")
                self.server_process.kill()
            except Exception as e:
                logger.error(f"Error stopping server: {e}")
            finally:
                self.server_process = None
    
    def run(self):
        """Main watchdog loop"""
        logger.info("=" * 60)
        logger.info("JARVIS WATCHDOG STARTED")
        logger.info("Monitoring Flask server on port 5000")
        logger.info("=" * 60)
        
        try:
            # Start initial server
            if not self.start_server():
                logger.error("Failed to start server initially!")
                return False
            
            # Main monitoring loop
            check_interval = 5  # Check every 5 seconds
            last_alert_time = 0
            
            while True:
                try:
                    time.sleep(check_interval)
                    
                    # Check if process still exists
                    if self.server_process and self.server_process.poll() is not None:
                        logger.error("⚠️  Server process crashed!")
                        self.consecutive_failures += 1
                        self.stop_server()
                        
                        if self.consecutive_failures >= self.max_consecutive_failures:
                            logger.error(f"❌ Server crashed {self.consecutive_failures} times. Giving up.")
                            return False
                        
                        # Restart after crash
                        time.sleep(2)
                        if not self.start_server():
                            continue
                    
                    # Check if server is responding
                    elif not self.is_server_healthy():
                        current_time = time.time()
                        if current_time - last_alert_time > 30:  # Alert every 30 seconds
                            logger.warning("⚠️  Server not responding on port 5000")
                            last_alert_time = current_time
                        
                        self.consecutive_failures += 1
                        if self.consecutive_failures >= 3:
                            logger.error("Server unresponsive - restarting...")
                            self.stop_server()
                            time.sleep(2)
                            self.start_server()
                    else:
                        # Server is healthy
                        self.consecutive_failures = 0
                        if last_alert_time > 0:
                            logger.info("✅ Server recovered and responding normally")
                            last_alert_time = 0
                
                except KeyboardInterrupt:
                    logger.info("Watchdog interrupted by user")
                    break
                except Exception as e:
                    logger.error(f"Error in watchdog loop: {e}")
                    time.sleep(5)
        
        finally:
            logger.info("Shutting down watchdog...")
            self.stop_server()
            logger.info("Watchdog stopped.")

if __name__ == "__main__":
    watchdog = JARVISWatchdog()
    try:
        watchdog.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
