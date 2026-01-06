"""
JSONL audit trail for kgraph pipeline.

Logs all significant events with timestamps, categories, and details.
Supports retention policy for automatic cleanup.
"""

import json
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

# Module-level logger instance
_logger: Optional["AuditLogger"] = None


class AuditLogger:
    """
    JSONL-based audit logger.

    Writes one JSON object per line to enable efficient appending
    and streaming reads.
    """

    CATEGORIES = {
        "session": ["start", "resume", "complete", "pause"],
        "batch": ["start", "complete", "failed", "skip"],
        "agent": ["invoke", "complete", "failed", "timeout"],
        "research": ["entity_researched", "cache_hit", "cache_miss"],
        "reconciliation": ["auto_decide", "llm_decide", "batch_complete"],
        "staging": ["stage", "ready", "reject"],
        "apply": ["merge", "update", "create", "failed", "batch_complete"],
        "question": ["add", "answer", "skip", "defer", "expire"],
        "error": ["exception", "validation", "timeout"],
    }

    def __init__(
        self,
        log_path: Path,
        retention_days: int = 30,
        session_id: Optional[str] = None,
    ):
        """
        Initialize audit logger.

        Args:
            log_path: Path to audit log file
            retention_days: Days to retain entries (0 = forever)
            session_id: Current session ID (auto-generated if None)
        """
        self.log_path = log_path
        self.retention_days = retention_days
        self.session_id = session_id or datetime.now().strftime("%Y%m%d-%H%M%S")

        # Ensure parent directory exists
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Run cleanup on init
        if retention_days > 0:
            self._cleanup_old_entries()

    def log(
        self,
        category: str,
        action: str,
        details: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        """
        Log an audit entry.

        Args:
            category: Category (session, batch, agent, etc.)
            action: Action within category
            details: Optional additional details
            duration_ms: Optional duration in milliseconds
        """
        entry = {
            "ts": datetime.now().isoformat(),
            "session_id": self.session_id,
            "category": category,
            "action": action,
        }

        if details:
            entry["details"] = details

        if duration_ms is not None:
            entry["duration_ms"] = duration_ms

        self._write_entry(entry)

    def log_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log an error with full traceback.

        Args:
            error: The exception
            context: Optional context about what was happening
        """
        entry = {
            "ts": datetime.now().isoformat(),
            "session_id": self.session_id,
            "category": "error",
            "action": "exception",
            "details": {
                "error_type": type(error).__name__,
                "error_message": str(error),
                "traceback": traceback.format_exc(),
            },
        }

        if context:
            entry["details"]["context"] = context

        self._write_entry(entry)

    def _write_entry(self, entry: Dict[str, Any]) -> None:
        """Write a single entry to the log file."""
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def _cleanup_old_entries(self) -> int:
        """
        Remove entries older than retention period.

        Returns:
            Number of entries removed
        """
        if not self.log_path.exists():
            return 0

        cutoff = datetime.now() - timedelta(days=self.retention_days)
        cutoff_str = cutoff.isoformat()

        # Read all entries
        entries = []
        removed = 0

        with open(self.log_path, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("ts", "") >= cutoff_str:
                        entries.append(line)
                    else:
                        removed += 1
                except json.JSONDecodeError:
                    # Keep malformed lines (shouldn't happen)
                    entries.append(line)

        # Rewrite file if we removed anything
        if removed > 0:
            with open(self.log_path, "w") as f:
                f.writelines(entries)

        return removed

    def get_entries(
        self,
        category: Optional[str] = None,
        action: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 1000,
    ) -> list[Dict[str, Any]]:
        """
        Read entries from the log.

        Args:
            category: Filter by category
            action: Filter by action
            since: Only entries after this time
            limit: Maximum entries to return

        Returns:
            List of matching entries (newest first)
        """
        if not self.log_path.exists():
            return []

        entries = []
        since_str = since.isoformat() if since else None

        with open(self.log_path, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())

                    # Apply filters
                    if category and entry.get("category") != category:
                        continue
                    if action and entry.get("action") != action:
                        continue
                    if since_str and entry.get("ts", "") < since_str:
                        continue

                    entries.append(entry)

                except json.JSONDecodeError:
                    continue

        # Return newest first, limited
        return list(reversed(entries[-limit:]))

    def get_session_stats(self) -> Dict[str, Any]:
        """Get statistics for current session."""
        entries = self.get_entries()

        session_entries = [e for e in entries if e.get("session_id") == self.session_id]

        stats = {
            "session_id": self.session_id,
            "total_entries": len(session_entries),
            "by_category": {},
            "errors": 0,
        }

        for entry in session_entries:
            cat = entry.get("category", "unknown")
            stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
            if cat == "error":
                stats["errors"] += 1

        return stats


def get_logger() -> Optional[AuditLogger]:
    """Get the current logger instance."""
    return _logger


def init_logger(
    log_path: Path,
    retention_days: int = 30,
    session_id: Optional[str] = None,
) -> AuditLogger:
    """
    Initialize the module-level logger.

    Args:
        log_path: Path to audit log file
        retention_days: Days to retain entries
        session_id: Current session ID

    Returns:
        The initialized logger
    """
    global _logger
    _logger = AuditLogger(log_path, retention_days, session_id)
    return _logger


def log_audit(
    category: str,
    action: str,
    details: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[int] = None,
) -> None:
    """
    Log an audit entry using the module-level logger.

    Falls back to no-op if logger not initialized.
    """
    if _logger:
        _logger.log(category, action, details, duration_ms)


def log_error(
    error: Exception,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Log an error using the module-level logger.

    Falls back to no-op if logger not initialized.
    """
    if _logger:
        _logger.log_error(error, context)


def init_audit_logger(
    log_dir: Path,
    retention_days: int = 30,
    session_id: Optional[str] = None,
) -> AuditLogger:
    """
    Initialize the audit logger for a directory.

    Creates a timestamped log file in the directory.

    Args:
        log_dir: Directory for audit logs
        retention_days: Days to retain entries
        session_id: Current session ID

    Returns:
        The initialized logger
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Use dated log file
    log_file = log_dir / f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl"

    return init_logger(log_file, retention_days, session_id)
