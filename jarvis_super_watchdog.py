#!/usr/bin/env python3
"""
JARVIS Super Watchdog - Better crash recovery
Finds any available port and keeps system running
"""

import os, sys, time, subprocess, socket, logging
from datetime import datetime
from pathlib import Path

log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(log_dir / "jarvis_super_watchdog.log"), logging.StreamHandler()]
)
logger = logging.getLogger("jarvis.super_watchdog")

class JARVISSuperWatchdog:
    def __init__(self):
        self.app_dir = Path(__file__).parent
        self.python_exe = "python"
        self.server_process = None
        self.restart_count = 0
        self.consecutive_failures = 0
        self.restart_delay = 2
        self.max_restart_delay = 30
        
    def find_available_port(self, start_port=5000):
        """Find an available port"""
        for port in range(start_port, start_port + 100):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                sock.connect(('127.0.0.1', port))
                sock.close()
                continue  # Port in use
            except:
                return port  # Port available
        return 5000
    
    def is_port_alive(self, port):
        """Check if something is listening on port"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            return result == 0
        except:
            return False
    
    def start_server(self):
        """Start server on available port"""
        try:
            logger.info(f"Starting Flask server (restart #{self.restart_count + 1})...")
            
            self.server_process = subprocess.Popen(
                [self.python_exe, "run.py"],
                cwd=str(self.app_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )
            
            logger.info(f"Server process started (PID: {self.server_process.pid})")
            self.restart_count += 1
            
            # Find which port it's on
            max_wait = 30
            start_time = time.time()
            port = None
            
            for port_candidate in range(5000, 5100):
                while time.time() - start_time < max_wait:
                    if self.is_port_alive(port_candidate):
                        port = port_candidate
                        break
                    time.sleep(1)
                if port:
                    break
            
            if port:
                logger.info(f"✅ Server responding on port {port}")
                logger.info(f"Access at: http://127.0.0.1:{port}")
                logger.info(f"Voice at: http://127.0.0.1:{port}/jarvis-voice")
                self.consecutive_failures = 0
                self.restart_delay = 2
                return port
            else:
                logger.warning("Server slow to start...")
                return None
                
        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            self.consecutive_failures += 1
            return None
    
    def stop_server(self):
        """Stop server"""
        if self.server_process:
            try:
                self.server_process.terminate()
                self.server_process.wait(timeout=5)
            except:
                try:
                    self.server_process.kill()
                except:
                    pass
            finally:
                self.server_process = None
    
    def run(self):
        """Main loop"""
        logger.info("=" * 60)
        logger.info("JARVIS SUPER WATCHDOG STARTED")
        logger.info("Protecting your system 24/7...")
        logger.info("=" * 60)
        
        if not self.start_server():
            logger.error("Failed to start initially")
            return False
        
        check_interval = 5
        
        while True:
            try:
                time.sleep(check_interval)
                
                # Check if process crashed
                if self.server_process and self.server_process.poll() is not None:
                    logger.error("⚠️ Server crashed!")
                    self.consecutive_failures += 1
                    self.stop_server()
                    time.sleep(self.restart_delay)
                    self.restart_delay = min(self.restart_delay * 2, self.max_restart_delay)
                    self.start_server()
                    
                # Check if any port is listening
                elif not any(self.is_port_alive(p) for p in range(5000, 5100)):
                    logger.warning("No server responding!")
                    self.consecutive_failures += 1
                    self.stop_server()
                    time.sleep(self.restart_delay)
                    self.restart_delay = min(self.restart_delay * 2, self.max_restart_delay)
                    self.start_server()
                else:
                    self.consecutive_failures = 0
                    self.restart_delay = 2
                    
            except KeyboardInterrupt:
                logger.info("Shutdown requested")
                break
            except Exception as e:
                logger.error(f"Error: {e}")
                time.sleep(self.restart_delay)
                self.restart_delay = min(self.restart_delay * 2, self.max_restart_delay)
        
        self.stop_server()


if __name__ == "__main__":
    watchdog = JARVISSuperWatchdog()
    watchdog.run()
