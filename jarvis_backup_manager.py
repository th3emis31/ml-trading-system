#!/usr/bin/env python3
"""
JARVIS Complete Backup System
Backs up ALL critical data: models, databases, configs, voice profiles, trading history
"""

import os
import json
import shutil
import sqlite3
import zipfile
import logging
from datetime import datetime
from pathlib import Path

# Setup
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_dir / "jarvis_backup.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("jarvis.backup")

class JARVISBackupManager:
    def __init__(self):
        self.project_root = Path(__file__).parent
        self.backup_dir = self.project_root / "backups"
        self.backup_dir.mkdir(exist_ok=True)
        
        # Critical data folders to backup
        self.critical_paths = {
            "models": self.project_root / "models",
            "memory": self.project_root / "memory",
            "config": self.project_root / "config",
            "voice": self.project_root / "voice",
            "trading": self.project_root / "trading",
            "data": self.project_root / "data",
        }
        
        # Critical files
        self.critical_files = [
            "jarvis_voice_profile.json",
            "jarvis_user_profile.json",
            "jarvis_learning_data.json",
            "requirements.txt",
        ]
    
    def backup_all(self):
        """Create complete backup of all JARVIS data"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"jarvis_backup_{timestamp}.zip"
            backup_path = self.backup_dir / backup_name
            
            logger.info("=" * 60)
            logger.info("STARTING COMPLETE JARVIS BACKUP")
            logger.info("=" * 60)
            
            with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Backup critical folders
                for folder_name, folder_path in self.critical_paths.items():
                    if folder_path.exists():
                        logger.info(f"Backing up {folder_name}...")
                        self._zip_folder(zf, folder_path, folder_name)
                    else:
                        logger.warning(f"Folder not found: {folder_name}")
                
                # Backup critical files
                for file_name in self.critical_files:
                    file_path = self.project_root / file_name
                    if file_path.exists():
                        logger.info(f"Backing up {file_name}...")
                        zf.write(file_path, file_name)
                    else:
                        logger.debug(f"File not found: {file_name}")
            
            backup_size_mb = backup_path.stat().st_size / (1024 * 1024)
            logger.info(f"✅ Backup created: {backup_name} ({backup_size_mb:.2f} MB)")
            logger.info(f"Location: {backup_path}")
            
            # Create manifest
            self._create_manifest(backup_path, timestamp)
            
            # Cleanup old backups (keep last 7)
            self._cleanup_old_backups()
            
            return str(backup_path)
            
        except Exception as e:
            logger.error(f"❌ Backup failed: {e}")
            raise
    
    def _zip_folder(self, zf, folder_path, arcname):
        """Recursively add folder to zip"""
        for item in folder_path.rglob("*"):
            if item.is_file():
                relative_path = item.relative_to(folder_path)
                arcname_path = f"{arcname}/{relative_path}"
                zf.write(item, arcname_path)
    
    def _create_manifest(self, backup_path, timestamp):
        """Create manifest file documenting backup"""
        manifest = {
            "timestamp": timestamp,
            "backup_file": backup_path.name,
            "backup_size_mb": backup_path.stat().st_size / (1024 * 1024),
            "files_backed_up": {
                "folders": list(self.critical_paths.keys()),
                "files": self.critical_files,
            },
            "system_info": {
                "project_root": str(self.project_root),
                "created": datetime.now().isoformat(),
            }
        }
        
        manifest_path = self.backup_dir / f"manifest_{timestamp}.json"
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)
        
        logger.info(f"Manifest created: {manifest_path.name}")
    
    def _cleanup_old_backups(self):
        """Keep only last 7 backups"""
        backups = sorted(self.backup_dir.glob("jarvis_backup_*.zip"))
        if len(backups) > 7:
            for old_backup in backups[:-7]:
                logger.info(f"Removing old backup: {old_backup.name}")
                old_backup.unlink()
    
    def restore_backup(self, backup_file):
        """Restore from a backup"""
        try:
            backup_path = self.backup_dir / backup_file
            if not backup_path.exists():
                raise FileNotFoundError(f"Backup not found: {backup_file}")
            
            logger.info(f"Restoring from backup: {backup_file}")
            
            with zipfile.ZipFile(backup_path, 'r') as zf:
                zf.extractall(self.project_root)
            
            logger.info("✅ Restore completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Restore failed: {e}")
            return False
    
    def list_backups(self):
        """List all available backups"""
        backups = list(self.backup_dir.glob("jarvis_backup_*.zip"))
        backups.sort(reverse=True)
        
        logger.info("Available backups:")
        for i, backup in enumerate(backups, 1):
            size_mb = backup.stat().st_size / (1024 * 1024)
            mtime = datetime.fromtimestamp(backup.stat().st_mtime)
            logger.info(f"  {i}. {backup.name} ({size_mb:.2f} MB) - {mtime}")
        
        return backups
    
    def backup_databases(self):
        """Backup all database files"""
        logger.info("Backing up databases...")
        
        db_files = list(self.project_root.rglob("*.db")) + \
                   list(self.project_root.rglob("*.sqlite")) + \
                   list(self.project_root.rglob("*.sqlite3"))
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        for db_file in db_files:
            try:
                backup_name = f"{db_file.name}.backup_{timestamp}"
                backup_dest = self.backup_dir / backup_name
                shutil.copy2(db_file, backup_dest)
                logger.info(f"✓ Backed up: {db_file.name}")
            except Exception as e:
                logger.warning(f"Failed to backup {db_file.name}: {e}")


def run_scheduled_backup():
    """Run daily backup"""
    backup_mgr = JARVISBackupManager()
    backup_mgr.backup_all()
    backup_mgr.backup_databases()


if __name__ == "__main__":
    backup_mgr = JARVISBackupManager()
    backup_mgr.backup_all()
    backup_mgr.list_backups()
