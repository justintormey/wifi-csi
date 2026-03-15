#!/usr/bin/env bash
# backup-fingerprints.sh — Backup and restore fingerprint databases.
#
# Usage:
#   ./backup-fingerprints.sh backup              # Create timestamped backup
#   ./backup-fingerprints.sh restore <archive>   # Restore from archive
#   ./backup-fingerprints.sh list                # List available backups
#
# Fingerprint DBs are .npz files stored in /opt/wifi-csi/data/fingerprints/.
# Backups go to /opt/wifi-csi/data/backups/.

set -euo pipefail

DATA_DIR="/opt/wifi-csi/data"
FP_DIR="$DATA_DIR/fingerprints"
BACKUP_DIR="$DATA_DIR/backups"
MAX_BACKUPS=10

usage() {
    echo "Usage: $0 {backup|restore <archive>|list}"
    exit 1
}

do_backup() {
    if [ ! -d "$FP_DIR" ] || [ -z "$(ls -A "$FP_DIR" 2>/dev/null)" ]; then
        echo "No fingerprint databases found in $FP_DIR — nothing to back up."
        exit 0
    fi

    mkdir -p "$BACKUP_DIR"
    local ts
    ts=$(date +%Y%m%d-%H%M%S)
    local archive="$BACKUP_DIR/fingerprints-$ts.tar.gz"

    tar -czf "$archive" -C "$DATA_DIR" fingerprints/
    echo "Backup created: $archive ($(du -h "$archive" | cut -f1))"

    # Prune old backups beyond MAX_BACKUPS
    local count
    count=$(ls -1 "$BACKUP_DIR"/fingerprints-*.tar.gz 2>/dev/null | wc -l)
    if [ "$count" -gt "$MAX_BACKUPS" ]; then
        ls -1t "$BACKUP_DIR"/fingerprints-*.tar.gz | tail -n +"$((MAX_BACKUPS + 1))" | xargs rm -f
        echo "Pruned old backups (kept $MAX_BACKUPS most recent)."
    fi
}

do_restore() {
    local archive="$1"

    if [ ! -f "$archive" ]; then
        # Try relative to BACKUP_DIR
        archive="$BACKUP_DIR/$1"
    fi

    if [ ! -f "$archive" ]; then
        echo "Error: Archive not found: $1"
        exit 1
    fi

    echo "Restoring from: $archive"
    echo "This will overwrite $FP_DIR. Continue? [y/N]"
    read -r confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "Aborted."
        exit 0
    fi

    # Backup current before overwriting
    if [ -d "$FP_DIR" ] && [ -n "$(ls -A "$FP_DIR" 2>/dev/null)" ]; then
        do_backup
        echo "Current fingerprints backed up before restore."
    fi

    rm -rf "$FP_DIR"
    tar -xzf "$archive" -C "$DATA_DIR"
    echo "Restore complete. Restart backend to pick up new fingerprints:"
    echo "  sudo systemctl restart wifi-csi-backend"
}

do_list() {
    if [ ! -d "$BACKUP_DIR" ]; then
        echo "No backups directory found."
        exit 0
    fi
    echo "Available backups:"
    ls -lh "$BACKUP_DIR"/fingerprints-*.tar.gz 2>/dev/null || echo "  (none)"
}

case "${1:-}" in
    backup)  do_backup ;;
    restore) [ -z "${2:-}" ] && usage; do_restore "$2" ;;
    list)    do_list ;;
    *)       usage ;;
esac
