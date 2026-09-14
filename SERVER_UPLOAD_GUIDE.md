# JARVIS Server Upload Guide

## Step 1: Prepare Your Files

All your files are in:
```
C:\Users\User\ml_trading_system
```

## Step 2: Upload via SSH (using SCP - Secure Copy)

### Option A: Using Windows Command Prompt/PowerShell

**Copy ALL files to server:**
```
scp -r C:\Users\User\ml_trading_system username@your-server-ip:/home/username/ml_trading_system
```

**Example:**
```
scp -r C:\Users\User\ml_trading_system user@192.168.1.100:/home/user/ml_trading_system
```

### Option B: Using a GUI Tool (Easier)

1. **Download WinSCP** (free) from: https://winscp.net/
2. Open WinSCP
3. Enter:
   - Hostname: your-server-ip or domain
   - Username: your-ssh-username
   - Password: your-ssh-password
4. Click Connect
5. Drag and drop all files from C:\Users\User\ml_trading_system to /home/user/

## Step 3: Run on Server

SSH into your server:
```
ssh username@your-server-ip
```

Navigate to folder:
```
cd ml_trading_system
```

Start JARVIS:
```
python app.py
```

Or use a background process (runs forever):
```
nohup python app.py > jarvis.log 2>&1 &
```

## Step 4: Access from Anywhere

Once running on server:
- Web: http://your-server-ip:5000
- Dashboard: http://your-server-ip:5000/jarvis-brain
- Voice: http://your-server-ip:5000/jarvis-voice

## Important Notes:

✅ Upload ENTIRE folder with all subdirectories
✅ Server must have Python 3.8+ installed
✅ Server must have TensorFlow and all dependencies
✅ Port 5000 must be open in server firewall
✅ Keep terminal open OR use "nohup" to run in background
