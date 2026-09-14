# 🆘 JARVIS DATA RECOVERY GUIDE

## Lost Data? Don't Panic! Here's How to Recover

---

## ⚡ Quick Recovery (5 Minutes)

### Scenario 1: Server Won't Start
```powershell
# 1. Check if data files exist
ls C:\Users\User\ml_trading_system\models
ls C:\Users\User\ml_trading_system\memory
ls C:\Users\User\ml_trading_system\data

# 2. Check backups
ls C:\Users\User\ml_trading_system\backups

# 3. Restore from backup
python -c "
from jarvis_backup_manager import JARVISBackupManager
mgr = JARVISBackupManager()
mgr.list_backups()
mgr.restore_backup('jarvis_backup_LATEST.zip')
"

# 4. Restart
C:\Users\User\ml_trading_system\jarvis_startup.bat
```

### Scenario 2: Lost AI Models
```powershell
# Check if backup exists
ls C:\Users\User\ml_trading_system\backups\*backup*.zip

# If yes, restore that specific backup
# If no, models can be retrained (slower but works)
```

### Scenario 3: Lost Trading History
```powershell
# Check database backups
ls C:\Users\User\ml_trading_system\backups\*.db.backup*

# Check main backup
python jarvis_backup_manager.py
```

---

## 📋 WHAT'S BACKED UP AUTOMATICALLY

| Data Type | Location | Backup Status | Recovery Time |
|-----------|----------|----------------|---------------|
| AI Models | `models/` | ✅ Auto-backed up daily | Instant |
| Voice Profile | `jarvis_voice_profile.json` | ✅ Auto-backed up | Instant |
| Trading History | `data/` | ✅ Auto-backed up | Instant |
| Chat Memory | `memory/` | ✅ Auto-backed up | Instant |
| User Profile | `config/` | ✅ Auto-backed up | Instant |
| Databases | `*.db` files | ✅ Auto-backed up | Instant |

---

## 🔧 STEP-BY-STEP RECOVERY

### Full System Recovery

```powershell
# Step 1: Stop JARVIS
taskkill /F /IM python.exe

# Step 2: List available backups
cd C:\Users\User\ml_trading_system
python -c "
from jarvis_backup_manager import JARVISBackupManager
mgr = JARVISBackupManager()
mgr.list_backups()
"

# Step 3: Choose the backup date you want to restore
# (Usually the most recent one before you lost data)

# Step 4: Restore from backup
python -c "
from jarvis_backup_manager import JARVISBackupManager
mgr = JARVISBackupManager()
# Replace filename with your chosen backup:
mgr.restore_backup('jarvis_backup_20260629_120000.zip')
"

# Step 5: Verify restoration
ls C:\Users\User\ml_trading_system\models
ls C:\Users\User\ml_trading_system\memory

# Step 6: Restart JARVIS
C:\Users\User\ml_trading_system\jarvis_startup.bat
```

---

## 🗂️ BACKUP LOCATION & STRUCTURE

```
C:\Users\User\ml_trading_system\
├── backups/                    ← ALL BACKUPS HERE
│   ├── jarvis_backup_20260629_081234.zip
│   ├── jarvis_backup_20260628_081234.zip
│   ├── jarvis_backup_20260627_081234.zip
│   ├── manifest_20260629_081234.json
│   └── snapshots/             ← Version history
│       ├── snapshot_20260629_120000.json
│       └── snapshot_20260628_120000.json
│
├── models/                    ← AI Models (backed up)
├── memory/                    ← Chat history (backed up)
├── data/                      ← Trading data (backed up)
├── jarvis_voice_profile.json  (backed up)
├── jarvis_user_profile.json   (backed up)
└── logs/
    └── jarvis_backup.log      ← Backup logs
```

---

## 📊 BACKUP STATUS CHECK

```powershell
# Check when last backup was created
$backups = ls C:\Users\User\ml_trading_system\backups\jarvis_backup*.zip | sort -Descending
$latest = $backups[0]
Write-Host "Latest backup: $($latest.Name)"
Write-Host "Created: $($latest.LastWriteTime)"
Write-Host "Size: $([math]::Round($latest.Length/1MB, 2)) MB"
```

---

## 🔐 SECURE YOUR BACKUPS

### Move Backups to External Drive (Weekly)
```powershell
# Copy backups to external USB (D: drive)
xcopy /E /I "C:\Users\User\ml_trading_system\backups" "D:\JARVIS_Backups"

# Verify copy
dir D:\JARVIS_Backups
```

### Upload to Cloud (Optional)
```powershell
# Google Drive Sync (if installed):
# Copy to: C:\Users\User\Google Drive\JARVIS_Backups

# OneDrive Sync (if installed):
# Copy to: C:\Users\User\OneDrive\JARVIS_Backups
```

---

## ⚠️ PREVENTION IS BETTER THAN RECOVERY

### Daily Backup Check
```powershell
# Run daily to ensure backups are being created
python jarvis_backup_manager.py
```

### Weekly External Backup
```powershell
# Copy to external drive every week
xcopy /E /I C:\Users\User\ml_trading_system\backups D:\JARVIS_Backups
```

### Monthly Recovery Test
```powershell
# Test that you CAN actually recover
# 1. Create copy of backups folder
# 2. Try restoring from copy
# 3. Verify everything works
```

---

## 🆘 IF SOMETHING GOES WRONG

### Issue: Backup Corrupted
```powershell
# Try older backup
python -c "
from jarvis_backup_manager import JARVISBackupManager
mgr = JARVISBackupManager()
mgr.restore_backup('jarvis_backup_20260627_081234.zip')  # Older date
"
```

### Issue: All Backups Lost
```
If local backups are gone but you have:
✓ External USB backup → Restore from USB
✓ Cloud backup → Download and restore
✓ System restore point → Use Windows System Restore
Otherwise: Rebuild system from requirements.txt
```

### Issue: Version Mismatch
```powershell
# Check version history
ls C:\Users\User\ml_trading_system\backups\snapshots\

# Each snapshot shows which files changed
# Can identify exactly when issue occurred
```

---

## 📞 RECOVERY CHECKLIST

When you lose data:
- [ ] Identify which backup date to restore from
- [ ] Stop JARVIS: `taskkill /F /IM python.exe`
- [ ] Backup current state (in case you need to revert)
- [ ] Restore from backup
- [ ] Verify all files are present
- [ ] Test critical functions
- [ ] Restart JARVIS
- [ ] Check logs for any errors

---

## 🎯 WHAT YOU CAN RECOVER

✅ **Instantly**:
- AI models and training data
- Voice profiles
- User preferences
- Trading history
- Chat memory
- Configuration files

⏱️ **Takes a few minutes**:
- Full system (50-500 MB depending on data)
- Database files
- All memory files

❌ **Cannot be recovered** (if no backup):
- Real-time decisions made during outage
- Live market data (can re-fetch)
- External API calls (can replay)

---

## 🔔 RECOMMENDED BACKUP SCHEDULE

| Frequency | Type | Storage | Keep |
|-----------|------|---------|------|
| Daily | Automatic | Local | 7 days |
| Weekly | Manual + External | USB Drive | Forever |
| Monthly | Manual + Cloud | Google Drive/OneDrive | Forever |

---

## ✅ YOU'RE PROTECTED!

With this recovery system:
- Daily automatic backups ✓
- Multiple backup versions ✓
- External backup capability ✓
- Quick recovery process ✓
- Version tracking ✓

**Nothing is truly lost if you have backups!**

---

**Last Updated**: 2026-06-29
**Recovery Plan Version**: 1.0
