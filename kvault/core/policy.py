"""Conservative, provider-neutral reconciliation policy.

Policy decides whether a fully specified reconciliation plan may be applied
without human review.  It deliberately does not perform model inference or
semantic mutation.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from kvault.core.paths import PathSafetyError, resolve_within_root
from kvault.core.transactions import atomic_write_text

POLICY_SCHEMA_VERSION = 1


class PolicyError(RuntimeError):
    """Raised when a policy file is unsafe or invalid."""


class MutationOperation(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    SUMMARY = "summary"
    MOVE = "move"
    MERGE = "merge"
    DELETE = "delete"
    RESTRUCTURE = "restructure"


class ReconciliationPolicy(BaseModel):
    """Versioned gates controlling unattended reconciliation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    require_event_for_mutations: bool = True
    auto_apply_operations: List[MutationOperation] = Field(
        default_factory=lambda: [
            MutationOperation.CREATE,
            MutationOperation.UPDATE,
            MutationOperation.SUMMARY,
        ]
    )
    auto_resolve_outcomes: List[str] = Field(
        default_factory=lambda: ["duplicate", "no_op", "journal_only"]
    )
    review_operations: List[MutationOperation] = Field(
        default_factory=lambda: [
            MutationOperation.MOVE,
            MutationOperation.MERGE,
            MutationOperation.DELETE,
            MutationOperation.RESTRUCTURE,
        ]
    )
    review_sensitivities: List[str] = Field(default_factory=lambda: ["sensitive", "restricted"])
    additive_updates_only: bool = True

    @field_validator(
        "auto_apply_operations",
        "review_operations",
        "auto_resolve_outcomes",
        "review_sensitivities",
    )
    @classmethod
    def unique_values(cls, values: List[Any]) -> List[Any]:
        result: List[Any] = []
        seen = set()
        for value in values:
            key = value.value if isinstance(value, Enum) else str(value)
            if key not in seen:
                result.append(value)
                seen.add(key)
        return result


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    review_required: bool
    reasons: List[str] = Field(default_factory=list)


def policy_path(root: Union[str, Path]) -> Path:
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise PolicyError(f"KB root is not a directory: {root_path}")
    try:
        return resolve_within_root(
            root_path,
            ".kvault/policy.yaml",
            allow_root=False,
            reject_symlinks=True,
        )
    except PathSafetyError as exc:
        raise PolicyError("Unsafe .kvault/policy.yaml path") from exc


def default_policy() -> ReconciliationPolicy:
    return ReconciliationPolicy()


def _render_policy(policy: ReconciliationPolicy) -> str:
    return yaml.safe_dump(
        policy.model_dump(mode="json"),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )


def ensure_policy(
    root: Union[str, Path],
    policy: Optional[ReconciliationPolicy] = None,
) -> ReconciliationPolicy:
    """Write the default policy atomically when no policy exists."""
    path = policy_path(root)
    if path.exists():
        return load_policy(root)
    selected = policy or default_policy()
    atomic_write_text(path, _render_policy(selected))
    return selected


def load_policy(root: Union[str, Path]) -> ReconciliationPolicy:
    """Load strict policy YAML, or return conservative defaults when absent."""
    path = policy_path(root)
    if not path.exists():
        return default_policy()
    if not path.is_file() or path.is_symlink():
        raise PolicyError(".kvault/policy.yaml must be a regular file")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"Could not parse policy: {path}") from exc
    if not isinstance(raw, dict):
        raise PolicyError("Policy YAML must contain a mapping")
    try:
        return ReconciliationPolicy.model_validate(raw)
    except ValueError as exc:
        raise PolicyError(f"Invalid policy: {exc}") from exc


def _as_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("plan must be a mapping or Pydantic model")


def policy_allows_plan(
    policy: ReconciliationPolicy,
    plan: Union[BaseModel, Mapping[str, Any]],
    *,
    sensitivities: Optional[Sequence[str]] = None,
    additive_update: bool = True,
) -> PolicyDecision:
    """Evaluate coarse unattended-apply gates for a prepared plan.

    Content-level revision, hierarchy, and additive-update validation remains
    the reconciliation engine's responsibility.  A false ``additive_update``
    tells this gate that the engine already found replacement or deletion.
    """
    payload = _as_mapping(plan)
    reasons: List[str] = []
    event_ids = payload.get("event_ids") or []
    mutations = payload.get("mutations") or []

    if policy.require_event_for_mutations and mutations and not event_ids:
        reasons.append("semantic mutations require at least one captured event")

    auto_operations = {item.value for item in policy.auto_apply_operations}
    review_operations = {item.value for item in policy.review_operations}
    for mutation in mutations:
        if isinstance(mutation, BaseModel):
            mutation = mutation.model_dump(mode="json")
        if not isinstance(mutation, Mapping):
            reasons.append("mutation is not a structured object")
            continue
        operation = str(mutation.get("operation", ""))
        if operation in review_operations:
            reasons.append(f"operation requires review: {operation}")
        elif operation not in auto_operations:
            reasons.append(
                f"operation is not allowed for automatic apply: {operation or 'missing'}"
            )

    review_sensitivities = set(policy.review_sensitivities)
    for sensitivity in sensitivities or []:
        if str(sensitivity) in review_sensitivities:
            reasons.append(f"sensitivity requires review: {sensitivity}")

    if policy.additive_updates_only and not additive_update:
        reasons.append("update removes or replaces existing semantic content")

    reasons = sorted(set(reasons))
    return PolicyDecision(
        allowed=not reasons,
        review_required=bool(reasons),
        reasons=reasons,
    )


__all__ = [
    "POLICY_SCHEMA_VERSION",
    "MutationOperation",
    "PolicyDecision",
    "PolicyError",
    "ReconciliationPolicy",
    "default_policy",
    "ensure_policy",
    "load_policy",
    "policy_allows_plan",
    "policy_path",
]
