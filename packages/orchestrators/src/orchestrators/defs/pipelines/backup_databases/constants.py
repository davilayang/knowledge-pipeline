# Tunables for the backup pipeline. Path-level config lives in orchestrators.config;
# anything here is specific to retention, capacity guards, and remote naming.

# Local snapshot retention — newest N partition dirs kept under BACKUP_DIR.
# Local is the recent-restore cache; Drive is the long-term archive.
MAX_LOCAL_BACKUPS = 14

# Drive retention — newest N partition dirs kept on the rclone remote.
MAX_DRIVE_BACKUPS = 90

# Drive capacity preflight — fail the run if used / total exceeds this.
DRIVE_USAGE_THRESHOLD = 0.90

# Drive layout. The remote name is read from the DRIVE_REMOTE env var at runtime
# (see resources.RcloneResource); this is just the path prefix under that remote.
DRIVE_ROOT = "knowledge-pipeline-backups"
