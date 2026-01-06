"""
Operation executor for applying staged changes to the knowledge graph.

Executes operations in priority order:
1. MERGE - Combine duplicate entities (reduces count)
2. UPDATE - Add info to existing entities
3. CREATE - Add new entities

This order ensures merges happen first to avoid creating duplicates
that would then need to be merged.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kgraph.core.config import KGraphConfig
from kgraph.core.storage import FilesystemStorage, normalize_entity_id
from kgraph.pipeline.staging import StagingDatabase
from kgraph.pipeline.audit import log_audit, log_error
from kgraph.pipeline.hooks import HookRegistry


@dataclass
class ExecutionResult:
    """Result of executing a single operation."""

    op_id: int
    """Staged operation ID"""

    success: bool
    """Whether execution succeeded"""

    action: str
    """Action that was taken: merge, update, create"""

    entity_path: Optional[str] = None
    """Path to the entity (created or merged into)"""

    error_message: Optional[str] = None
    """Error message if failed"""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "op_id": self.op_id,
            "success": self.success,
            "action": self.action,
            "entity_path": self.entity_path,
            "error_message": self.error_message,
        }


@dataclass
class BatchExecutionSummary:
    """Summary of a batch execution."""

    batch_id: str
    """Batch identifier"""

    total_operations: int
    """Total operations processed"""

    successful: int = 0
    """Successfully applied"""

    failed: int = 0
    """Failed to apply"""

    skipped: int = 0
    """Skipped (pending review, etc.)"""

    merges: int = 0
    """Merge operations"""

    updates: int = 0
    """Update operations"""

    creates: int = 0
    """Create operations"""

    errors: List[str] = field(default_factory=list)
    """List of error messages"""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "batch_id": self.batch_id,
            "total_operations": self.total_operations,
            "successful": self.successful,
            "failed": self.failed,
            "skipped": self.skipped,
            "merges": self.merges,
            "updates": self.updates,
            "creates": self.creates,
            "errors": self.errors[:10],  # Cap for logging
        }


class OperationExecutor:
    """
    Executes staged operations against the knowledge graph.

    Operations are executed in priority order:
    - Priority 1: MERGE (combine duplicates first)
    - Priority 2: UPDATE (add to existing)
    - Priority 3: CREATE (add new entities)
    """

    def __init__(
        self,
        config: KGraphConfig,
        storage: FilesystemStorage,
        staging_db: StagingDatabase,
        hooks: Optional[HookRegistry] = None,
    ):
        """
        Initialize operation executor.

        Args:
            config: KGraph configuration
            storage: Storage backend for knowledge graph
            staging_db: Staging database
            hooks: Optional hook registry for event callbacks
        """
        self.config = config
        self.storage = storage
        self.staging_db = staging_db
        self.hooks = hooks or HookRegistry()

    def execute_batch(
        self,
        batch_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> BatchExecutionSummary:
        """
        Execute all ready operations, optionally for a specific batch.

        Args:
            batch_id: Optional batch filter
            dry_run: If True, don't actually apply changes

        Returns:
            Summary of execution
        """
        operations = self.staging_db.get_ready_operations(batch_id=batch_id)

        summary = BatchExecutionSummary(
            batch_id=batch_id or "all",
            total_operations=len(operations),
        )

        log_audit(
            "apply",
            "batch_start",
            {
                "batch_id": batch_id,
                "operations": len(operations),
                "dry_run": dry_run,
            },
        )

        for op in operations:
            result = self._execute_one(op, dry_run=dry_run)

            if result.success:
                summary.successful += 1
                if result.action == "merge":
                    summary.merges += 1
                elif result.action == "update":
                    summary.updates += 1
                elif result.action == "create":
                    summary.creates += 1
            else:
                summary.failed += 1
                if result.error_message:
                    summary.errors.append(f"{op['entity_name']}: {result.error_message}")

        log_audit("apply", "batch_complete", summary.to_dict())

        return summary

    def execute_one(self, op_id: int, dry_run: bool = False) -> ExecutionResult:
        """
        Execute a single operation by ID.

        Args:
            op_id: Operation ID
            dry_run: If True, don't actually apply changes

        Returns:
            Execution result
        """
        op = self.staging_db.get_operation(op_id)

        if not op:
            return ExecutionResult(
                op_id=op_id,
                success=False,
                action="unknown",
                error_message="Operation not found",
            )

        if op["status"] != "ready":
            return ExecutionResult(
                op_id=op_id,
                success=False,
                action=op["action"],
                error_message=f"Operation not ready (status: {op['status']})",
            )

        return self._execute_one(op, dry_run=dry_run)

    def _execute_one(
        self,
        op: Dict[str, Any],
        dry_run: bool = False,
    ) -> ExecutionResult:
        """Execute a single operation."""
        op_id = op["id"]
        action = op["action"]
        entity_data = op["entity_data"]
        target_path = op.get("target_path")

        try:
            if action == "merge":
                result = self._execute_merge(op, dry_run=dry_run)
            elif action == "update":
                result = self._execute_update(op, dry_run=dry_run)
            elif action == "create":
                result = self._execute_create(op, dry_run=dry_run)
            else:
                result = ExecutionResult(
                    op_id=op_id,
                    success=False,
                    action=action,
                    error_message=f"Unknown action: {action}",
                )

            # Update staging database
            if not dry_run:
                if result.success:
                    self.staging_db.update_status(op_id, "applied")

                    # Emit operation applied event
                    self.hooks.emit_simple(
                        "operation_applied",
                        {
                            "op_id": op_id,
                            "action": action,
                            "entity_name": entity_data.get("name"),
                            "entity_path": result.entity_path,
                        },
                    )
                else:
                    self.staging_db.update_status(
                        op_id, "failed", error_message=result.error_message
                    )

            log_audit(
                "apply",
                "operation",
                {
                    "op_id": op_id,
                    "action": action,
                    "entity": entity_data.get("name"),
                    "success": result.success,
                    "dry_run": dry_run,
                    "error": result.error_message,
                },
            )

            return result

        except Exception as e:
            log_error(e, {"op_id": op_id, "action": action})

            if not dry_run:
                self.staging_db.update_status(op_id, "failed", error_message=str(e))

            # Emit operation failed event for exceptions
            self.hooks.emit_simple(
                "operation_failed",
                {
                    "op_id": op_id,
                    "action": action,
                    "entity_name": entity_data.get("name"),
                    "error": str(e),
                },
            )

            return ExecutionResult(
                op_id=op_id,
                success=False,
                action=action,
                error_message=str(e),
            )

    def _execute_merge(
        self,
        op: Dict[str, Any],
        dry_run: bool = False,
    ) -> ExecutionResult:
        """
        Execute a MERGE operation.

        Merges extracted entity data into an existing entity:
        - Adds new contacts (dedupe by email)
        - Adds extracted name as alias
        - Updates sources
        """
        op_id = op["id"]
        entity_data = op["entity_data"]
        target_path = op.get("target_path")

        if not target_path:
            return ExecutionResult(
                op_id=op_id,
                success=False,
                action="merge",
                error_message="No target path for merge",
            )

        # Parse target path: "customers/strategic/acme_corp"
        target_type, target_tier, target_id = self._parse_path(target_path)

        if not target_type or not target_id:
            return ExecutionResult(
                op_id=op_id,
                success=False,
                action="merge",
                error_message=f"Invalid target path: {target_path}",
            )

        if dry_run:
            return ExecutionResult(
                op_id=op_id,
                success=True,
                action="merge",
                entity_path=target_path,
            )

        # Perform merge
        success = self.storage.merge_entities(
            source_data=entity_data,
            target_type=target_type,
            target_id=target_id,
            target_tier=target_tier,
        )

        if success:
            # Emit entity merged event
            self.hooks.emit_simple(
                "entity_merged",
                {
                    "source_name": entity_data.get("name"),
                    "target_path": target_path,
                    "target_id": target_id,
                    "entity_data": entity_data,
                },
            )
            return ExecutionResult(
                op_id=op_id,
                success=True,
                action="merge",
                entity_path=target_path,
            )
        else:
            # Emit operation failed event
            self.hooks.emit_simple(
                "operation_failed",
                {
                    "op_id": op_id,
                    "action": "merge",
                    "entity_name": entity_data.get("name"),
                    "target_path": target_path,
                    "error": f"Merge failed for {target_path}",
                },
            )
            return ExecutionResult(
                op_id=op_id,
                success=False,
                action="merge",
                error_message=f"Merge failed for {target_path}",
            )

    def _execute_update(
        self,
        op: Dict[str, Any],
        dry_run: bool = False,
    ) -> ExecutionResult:
        """
        Execute an UPDATE operation.

        Updates an existing entity with new information.
        Similar to merge but for adding info (not combining entities).
        """
        # Execute using merge logic
        result = self._execute_merge(op, dry_run=dry_run)

        # Emit entity_updated instead of entity_merged (which was already emitted)
        # If successful, emit the more specific update event
        if result.success and not dry_run:
            entity_data = op["entity_data"]
            self.hooks.emit_simple(
                "entity_updated",
                {
                    "entity_name": entity_data.get("name"),
                    "entity_path": result.entity_path,
                    "entity_data": entity_data,
                },
            )

        return result

    def _execute_create(
        self,
        op: Dict[str, Any],
        dry_run: bool = False,
    ) -> ExecutionResult:
        """
        Execute a CREATE operation.

        Creates a new entity in the knowledge graph.
        """
        op_id = op["id"]
        entity_data = op["entity_data"]

        # Determine entity type and tier
        entity_type = entity_data.get("entity_type", "customer")
        tier = entity_data.get("tier")

        # Normalize entity name to ID
        name = entity_data.get("name", "")
        if not name:
            return ExecutionResult(
                op_id=op_id,
                success=False,
                action="create",
                error_message="No entity name provided",
            )

        entity_id = normalize_entity_id(name)

        # Validate entity type exists in config
        if entity_type not in self.config.entity_types:
            return ExecutionResult(
                op_id=op_id,
                success=False,
                action="create",
                error_message=f"Unknown entity type: {entity_type}",
            )

        # Determine tier if not specified
        if not tier:
            tier = self._infer_tier(entity_data)

        # Validate tier
        if tier and tier not in self.config.tiers:
            return ExecutionResult(
                op_id=op_id,
                success=False,
                action="create",
                error_message=f"Unknown tier: {tier}",
            )

        # Build entity path
        et_config = self.config.entity_types[entity_type]
        if tier:
            entity_path = f"{et_config.directory}/{tier}/{entity_id}"
        else:
            entity_path = f"{et_config.directory}/{entity_id}"

        # Check if entity already exists
        if self.storage.entity_exists(entity_type, entity_id, tier):
            return ExecutionResult(
                op_id=op_id,
                success=False,
                action="create",
                entity_path=entity_path,
                error_message=f"Entity already exists: {entity_path}",
            )

        if dry_run:
            return ExecutionResult(
                op_id=op_id,
                success=True,
                action="create",
                entity_path=entity_path,
            )

        # Prepare entity data for storage
        storage_data = {
            "name": name,
            "industry": entity_data.get("industry"),
            "contacts": entity_data.get("contacts", []),
            "sources": [f"kgraph-pipeline-{datetime.now().strftime('%Y-%m-%d')}"],
            "created": datetime.now().strftime("%Y-%m-%d"),
        }

        # Add optional fields
        for field in ["description", "location", "status", "aliases"]:
            if entity_data.get(field):
                storage_data[field] = entity_data[field]

        # Write entity
        success = self.storage.write_entity(
            entity_type=entity_type,
            entity_id=entity_id,
            data=storage_data,
            tier=tier,
        )

        if success:
            # Emit entity created event
            self.hooks.emit_simple(
                "entity_created",
                {
                    "entity_name": name,
                    "entity_path": entity_path,
                    "entity_id": entity_id,
                    "entity_type": entity_type,
                    "tier": tier,
                    "entity_data": storage_data,
                },
            )
            return ExecutionResult(
                op_id=op_id,
                success=True,
                action="create",
                entity_path=entity_path,
            )
        else:
            # Emit operation failed event
            self.hooks.emit_simple(
                "operation_failed",
                {
                    "op_id": op_id,
                    "action": "create",
                    "entity_name": name,
                    "entity_path": entity_path,
                    "error": f"Write failed for {entity_path}",
                },
            )
            return ExecutionResult(
                op_id=op_id,
                success=False,
                action="create",
                error_message=f"Write failed for {entity_path}",
            )

    def _parse_path(self, path: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Parse entity path into (entity_type, tier, entity_id).

        Examples:
            "customers/strategic/acme_corp" → ("customer", "strategic", "acme_corp")
            "suppliers/acme_supply" → ("supplier", None, "acme_supply")
        """
        parts = path.strip("/").split("/")

        if len(parts) == 3:
            # entity_type/tier/entity_id
            directory, tier, entity_id = parts

            # Find entity type by directory
            for et_name, et_config in self.config.entity_types.items():
                if et_config.directory == directory:
                    return (et_name, tier, entity_id)

            return (None, tier, entity_id)

        elif len(parts) == 2:
            # entity_type/entity_id (no tier)
            directory, entity_id = parts

            for et_name, et_config in self.config.entity_types.items():
                if et_config.directory == directory:
                    return (et_name, None, entity_id)

            return (None, None, entity_id)

        else:
            return (None, None, None)

    def _infer_tier(self, entity_data: Dict[str, Any]) -> Optional[str]:
        """
        Infer appropriate tier from entity data.

        Uses confidence and source to determine tier:
        - High confidence + customer = "standard"
        - Low confidence = "prospects" (if available)
        """
        confidence = entity_data.get("confidence", 0.5)
        entity_type = entity_data.get("entity_type", "customer")

        # Check if entity type uses tiers
        et_config = self.config.entity_types.get(entity_type)
        if not et_config:
            return None

        # Default tier logic
        if confidence < 0.6:
            # Lower confidence → prospects if available
            if "prospects" in self.config.tiers:
                return "prospects"

        # Default to standard for most cases
        if "standard" in self.config.tiers:
            return "standard"

        # Return first available tier
        if self.config.tiers:
            return list(self.config.tiers.keys())[0]

        return None

    def get_pending_count(self, batch_id: Optional[str] = None) -> Dict[str, int]:
        """
        Get count of operations by status.

        Args:
            batch_id: Optional batch filter

        Returns:
            Dict of status -> count
        """
        if batch_id:
            return self.staging_db.count_by_batch(batch_id)
        return self.staging_db.count_by_status()
