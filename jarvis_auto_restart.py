#!/usr/bin/env python3
"""
JARVIS Auto-Restart Watchdog
Monitors Flask and automatically restarts if it crashes or hangs
"""
import subprocess
import time
import sys
import os
from pathlib import Path

LOG_FILE = Path(__file__).parent / 'jarvis_watchdog.log'

def log_msg(msg):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f'[{timestamp}] {msg}'
    print(log_entry)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(log_entry + '\n')
    except:
        pass

def kill_port_5000():
    """Force kill any process using port 5000"""
    try:
        os.system('taskkill /F /PID $(netstat -ano | findstr :5000 | awk "{print $5}") 2>nul')
    except:
        pass
    time.sleep(1)

def start_flask():
    """Start Flask server"""
    log_msg('🚀 Starting Flask...')
    try:
        cmd = [sys.executable, 'app.py']
        proc = subprocess.Popen(
            cmd,
            cwd=str(Path(__file__).parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        return proc
    except Exception as e:
        log_msg(f'❌ ERROR starting Flask: {e}')
        return None

def is_flask_responding():
    """Check if Flask is responding to requests"""
    try:
        import urllib.request
        response = urllib.request.urlopen('http://127.0.0.1:5000/api/jarvis-status', timeout=3)
        return response.status == 200
    except:
        return False

def main():
    log_msg('=' * 60)
    log_msg('🤖 JARVIS Watchdog Started')
    log_msg('=' * 60)
    
    crash_count = 0
    
    while True:
        try:
            # Start Flask
            flask_proc = start_flask()
            if not flask_proc:
                log_msg('⚠️  Failed to start Flask, retrying in 5s...')
                time.sleep(5)
                continue
            
            # Wait for Flask to boot
            log_msg('⏳ Waiting for Flask to boot...')
            time.sleep(10)
            
            # Monitor Flask
            check_count = 0
            while True:
                check_count += 1
                
                # Check if process is still running
                if flask_proc.poll() is not None:
                    log_msg(f'❌ Flask crashed! (exit code: {flask_proc.poll()})')
                    crash_count += 1
                    break
                
                # Check if Flask is responding every 30 seconds
                if check_count % 6 == 0:
                    if not is_flask_responding():
                        log_msg(f'❌ Flask NOT RESPONDING (timeout after {check_count*5}s)')
                        try:
                            flask_proc.terminate()
                            time.sleep(2)
                            flask_proc.kill()
                        except:
                            pass
                        crash_count += 1
                        break
                    else:
                        log_msg(f'✅ Flask OK (check #{check_count})')
                
                time.sleep(5)
            
            log_msg(f'🔄 Restarting Flask... (crash #{crash_count})')
            time.sleep(3)
            
        except KeyboardInterrupt:
            log_msg('⏹️  Watchdog stopped by user')
            try:
                flask_proc.terminate()
            except:
                pass
            sys.exit(0)
        except Exception as e:
            log_msg(f'⚠️  Watchdog error: {e}')
            time.sleep(5)

if __name__ == '__main__':
    main()
