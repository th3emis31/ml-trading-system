#!/usr/bin/env python3
"""
JARVIS Version Control System
Tracks all important changes and creates recovery snapshots
"""

import json
import logging
from datetime import datetime
from pathlib import Path
import hashlib

log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_dir / "jarvis_version_control.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("jarvis.version_control")


class JARVISVersionControl:
    """Track changes and maintain version history"""
    
    def __init__(self):
        self.project_root = Path(__file__).parent
        self.version_file = self.project_root / ".jarvis_version"
        self.changelog_file = self.project_root / "JARVIS_CHANGELOG.md"
        self.snapshots_dir = self.project_root / "backups" / "snapshots"
        self.snapshots_dir.mkdir(exist_ok=True, parents=True)
        
        # Critical files to track
        self.tracked_files = [
            "jarvis_ai_engine.py",
            "jarvis_api_routes.py",
            "jarvis_advanced_analytics.py",
            "app.py",
            "run.py",
            "requirements.txt",
            "jarvis_voice_profile.json",
            "jarvis_user_profile.json",
        ]
    
    def get_file_hash(self, file_path):
        """Get hash of file contents"""
        try:
            if not Path(file_path).exists():
                return None
            with open(file_path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except:
            return None
    
    def create_snapshot(self, description):
        """Create a version snapshot"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            snapshot = {
                "timestamp": timestamp,
                "datetime": datetime.now().isoformat(),
                "description": description,
                "files": {}
            }
            
            logger.info(f"Creating snapshot: {description}")
            
            for file_name in self.tracked_files:
                file_path = self.project_root / file_name
                file_hash = self.get_file_hash(file_path)
                snapshot["files"][file_name] = {
                    "hash": file_hash,
                    "exists": file_path.exists(),
                    "size": file_path.stat().st_size if file_path.exists() else 0
                }
            
            snapshot_file = self.snapshots_dir / f"snapshot_{timestamp}.json"
            with open(snapshot_file, 'w') as f:
                json.dump(snapshot, f, indent=2)
            
            logger.info(f"✓ Snapshot created: snapshot_{timestamp}.json")
            self._update_changelog(timestamp, description)
            
            return snapshot_file
            
        except Exception as e:
            logger.error(f"Failed to create snapshot: {e}")
            raise
    
    def _update_changelog(self, timestamp, description):
        """Update CHANGELOG.md"""
        try:
            entry = f"\n### [{timestamp}]\n- {description}\n"
            
            if self.changelog_file.exists():
                with open(self.changelog_file, 'a') as f:
                    f.write(entry)
            else:
                with open(self.changelog_file, 'w') as f:
                    f.write("# JARVIS System Changelog\n")
                    f.write(entry)
            
            logger.info(f"Changelog updated: {description}")
        except Exception as e:
            logger.warning(f"Failed to update changelog: {e}")
    
    def list_snapshots(self):
        """List all available snapshots"""
        snapshots = sorted(self.snapshots_dir.glob("snapshot_*.json"), reverse=True)
        
        logger.info("Available snapshots:")
        for i, snapshot in enumerate(snapshots, 1):
            with open(snapshot) as f:
                data = json.load(f)
            logger.info(f"  {i}. {data['timestamp']} - {data['description']}")
        
        return snapshots
    
    def compare_snapshots(self, snap1_id, snap2_id):
        """Compare two snapshots to see what changed"""
        try:
            snap1_file = self.snapshots_dir / f"snapshot_{snap1_id}.json"
            snap2_file = self.snapshots_dir / f"snapshot_{snap2_id}.json"
            
            with open(snap1_file) as f:
                snap1 = json.load(f)
            with open(snap2_file) as f:
                snap2 = json.load(f)
            
            changes = {
                "added": [],
                "removed": [],
                "modified": [],
                "unchanged": []
            }
            
            for file_name in snap1["files"]:
                hash1 = snap1["files"][file_name]["hash"]
                hash2 = snap2["files"].get(file_name, {}).get("hash")
                
                if hash1 != hash2:
                    changes["modified"].append(file_name)
                else:
                    changes["unchanged"].append(file_name)
            
            for file_name in snap2["files"]:
                if file_name not in snap1["files"]:
                    changes["added"].append(file_name)
            
            logger.info("Changes between snapshots:")
            logger.info(f"  Added: {len(changes['added'])}")
            logger.info(f"  Removed: {len(changes['removed'])}")
            logger.info(f"  Modified: {len(changes['modified'])}")
            logger.info(f"  Unchanged: {len(changes['unchanged'])}")
            
            return changes
            
        except Exception as e:
            logger.error(f"Failed to compare snapshots: {e}")
            raise


if __name__ == "__main__":
    vc = JARVISVersionControl()
    
    # Create initial snapshot
    vc.create_snapshot("System initialized with auto-backup and monitoring")
    
    # List snapshots
    vc.list_snapshots()
