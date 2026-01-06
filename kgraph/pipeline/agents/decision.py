"""
Decision agent for entity reconciliation.

Decides whether extracted entities should be merged, updated, or created
based on match confidence and configurable thresholds.
"""

import json
import subprocess
from typing import List, Optional, Tuple

from kgraph.core.config import KGraphConfig, ConfidenceConfig
from kgraph.pipeline.agents.base import (
    ExtractedEntity,
    MatchCandidate,
    ReconcileDecision,
)
from kgraph.pipeline.audit import log_audit, log_error


class DecisionAgent:
    """
    Agent that decides how to reconcile extracted entities.

    Uses auto-decide rules for high/low confidence cases,
    with optional LLM fallback for ambiguous cases.
    """

    def __init__(self, config: KGraphConfig):
        """
        Initialize decision agent.

        Args:
            config: KGraph configuration
        """
        self.config = config
        self.confidence = config.confidence

    @property
    def name(self) -> str:
        """Agent name for logging."""
        return "decision"

    def reconcile(
        self,
        entities_with_candidates: List[Tuple[ExtractedEntity, List[MatchCandidate]]],
        use_llm: bool = True,
        thresholds: Optional[ConfidenceConfig] = None,
    ) -> List[ReconcileDecision]:
        """
        Decide action for each entity.

        Auto-decide rules (from ConfidenceConfig):
        - Alias match (score=1.0) → MERGE
        - Score >= auto_merge (0.95) → MERGE
        - Email domain match >= auto_update (0.90) → UPDATE
        - Score < auto_create (0.50) → CREATE
        - Otherwise → LLM decides (or CREATE if LLM disabled)

        Args:
            entities_with_candidates: List of (entity, candidates) tuples
            use_llm: Whether to use LLM for ambiguous cases
            thresholds: Override confidence thresholds

        Returns:
            List of ReconcileDecision objects
        """
        thresholds = thresholds or self.confidence

        decisions: List[Optional[ReconcileDecision]] = []
        needs_llm: List[Tuple[ExtractedEntity, List[MatchCandidate], int]] = []

        # First pass: try auto-decide
        for entity, candidates in entities_with_candidates:
            decision = self._try_auto_decide(entity, candidates, thresholds)

            if decision:
                decisions.append(decision)
            else:
                # Queue for LLM or manual decision
                needs_llm.append((entity, candidates, len(decisions)))
                decisions.append(None)  # Placeholder

        # Second pass: LLM for ambiguous cases
        if needs_llm and use_llm:
            try:
                llm_decisions = self._llm_reconcile(needs_llm, thresholds)
                for (entity, candidates, idx), decision in zip(needs_llm, llm_decisions):
                    decisions[idx] = decision
            except Exception as e:
                log_error(e, {"agent": "decision", "phase": "llm_reconcile"})

        # Fill any remaining None with CREATE + review flag
        for i, d in enumerate(decisions):
            if d is None:
                entity, candidates = entities_with_candidates[i]
                decisions[i] = ReconcileDecision(
                    entity_name=entity.name,
                    action="create",
                    confidence=0.5,
                    reasoning="Ambiguous case, LLM unavailable",
                    needs_review=True,
                    source_entity=entity,
                    candidates=candidates,
                )

        # Log summary
        action_counts = {}
        review_count = 0
        for d in decisions:
            if d:
                action_counts[d.action] = action_counts.get(d.action, 0) + 1
                if d.needs_review:
                    review_count += 1

        log_audit(
            "reconciliation",
            "batch_complete",
            {
                "total": len(decisions),
                "auto_decided": len(decisions) - len(needs_llm),
                "llm_decided": len(needs_llm) if use_llm else 0,
                "needs_review": review_count,
                "action_counts": action_counts,
            },
        )

        return [d for d in decisions if d is not None]

    def _try_auto_decide(
        self,
        entity: ExtractedEntity,
        candidates: List[MatchCandidate],
        thresholds: ConfidenceConfig,
    ) -> Optional[ReconcileDecision]:
        """
        Try to make decision without LLM.

        Returns None if decision requires LLM.
        """
        # No candidates = create new
        if not candidates:
            decision = ReconcileDecision(
                entity_name=entity.name,
                action="create",
                confidence=0.9,
                reasoning="No matching entities found",
                needs_review=False,
                source_entity=entity,
                candidates=[],
            )
            log_audit("reconciliation", "auto_decide", {
                "entity": entity.name,
                "action": "create",
                "reason": "no_candidates",
            })
            return decision

        top = candidates[0]

        # Perfect alias match = definite merge
        if top.match_type == "alias" and top.match_score == 1.0:
            decision = ReconcileDecision(
                entity_name=entity.name,
                action="merge",
                target_path=top.candidate_path,
                confidence=1.0,
                reasoning=f"Exact alias match: {top.match_details.get('matched_alias', top.candidate_name)}",
                needs_review=False,
                source_entity=entity,
                candidates=candidates,
            )
            log_audit("reconciliation", "auto_decide", {
                "entity": entity.name,
                "action": "merge",
                "reason": "alias_match",
                "target": top.candidate_path,
            })
            return decision

        # Very high fuzzy match = merge
        if top.match_score >= thresholds.auto_merge:
            decision = ReconcileDecision(
                entity_name=entity.name,
                action="merge",
                target_path=top.candidate_path,
                confidence=top.match_score,
                reasoning=f"High similarity match: {top.candidate_name} (score: {top.match_score:.2f})",
                needs_review=False,
                source_entity=entity,
                candidates=candidates,
            )
            log_audit("reconciliation", "auto_decide", {
                "entity": entity.name,
                "action": "merge",
                "reason": "high_similarity",
                "score": top.match_score,
                "target": top.candidate_path,
            })
            return decision

        # Email domain match with good score = update
        if (
            top.match_type == "email_domain"
            and top.match_score >= thresholds.auto_update
        ):
            decision = ReconcileDecision(
                entity_name=entity.name,
                action="update",
                target_path=top.candidate_path,
                confidence=0.9,
                reasoning=f"Same email domain: {top.match_details.get('matched_domains', [])}",
                needs_review=False,
                source_entity=entity,
                candidates=candidates,
            )
            log_audit("reconciliation", "auto_decide", {
                "entity": entity.name,
                "action": "update",
                "reason": "email_domain",
                "target": top.candidate_path,
            })
            return decision

        # Low match scores = likely new entity
        if top.match_score < thresholds.auto_create:
            decision = ReconcileDecision(
                entity_name=entity.name,
                action="create",
                confidence=0.8,
                reasoning=f"No strong matches found (best: {top.match_score:.2f})",
                needs_review=False,
                source_entity=entity,
                candidates=candidates,
            )
            log_audit("reconciliation", "auto_decide", {
                "entity": entity.name,
                "action": "create",
                "reason": "low_scores",
                "best_score": top.match_score,
            })
            return decision

        # Ambiguous - needs LLM
        log_audit("reconciliation", "needs_llm", {
            "entity": entity.name,
            "best_score": top.match_score,
            "best_type": top.match_type,
        })
        return None

    def _llm_reconcile(
        self,
        needs_llm: List[Tuple[ExtractedEntity, List[MatchCandidate], int]],
        thresholds: ConfidenceConfig,
    ) -> List[ReconcileDecision]:
        """
        Use LLM to reconcile ambiguous cases.

        Returns list of decisions in same order as input.
        """
        prompt = self._build_reconcile_prompt(
            [(e, c) for e, c, _ in needs_llm]
        )

        try:
            result = subprocess.run(
                ["claude", "-p", "-"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.config.agent.timeout,
            )

            if result.returncode == 0:
                decisions = self._parse_llm_response(result.stdout, needs_llm)

                log_audit("reconciliation", "llm_decide", {
                    "entities_processed": len(needs_llm),
                })

                return decisions

            else:
                log_audit("reconciliation", "llm_failed", {
                    "returncode": result.returncode,
                    "stderr": result.stderr[:500],
                })

        except subprocess.TimeoutExpired:
            log_audit("reconciliation", "llm_timeout", {
                "timeout": self.config.agent.timeout,
            })

        except Exception as e:
            log_error(e, {"agent": "decision", "phase": "llm_call"})

        # Fallback: return decisions with review flag
        return [
            ReconcileDecision(
                entity_name=entity.name,
                action="create",
                confidence=0.5,
                reasoning="LLM failed, defaulting to CREATE with review",
                needs_review=True,
                source_entity=entity,
                candidates=candidates,
            )
            for entity, candidates, _ in needs_llm
        ]

    def _build_reconcile_prompt(
        self,
        entities_with_candidates: List[Tuple[ExtractedEntity, List[MatchCandidate]]],
    ) -> str:
        """Build batch reconciliation prompt for LLM."""
        entities_json = []

        for entity, candidates in entities_with_candidates:
            entities_json.append({
                "extracted": {
                    "name": entity.name,
                    "type": entity.entity_type,
                    "industry": entity.industry,
                    "contacts": entity.contacts[:3],  # Limit for context
                },
                "candidates": [
                    {
                        "path": c.candidate_path,
                        "name": c.candidate_name,
                        "match_type": c.match_type,
                        "score": round(c.match_score, 2),
                    }
                    for c in candidates[:5]  # Top 5 candidates
                ],
            })

        return f"""# Entity Reconciliation

Decide whether each extracted entity should be:
- **MERGE**: Same entity as existing (combine data into existing)
- **UPDATE**: Add new info to existing entity (same org, new contact)
- **CREATE**: Genuinely new entity

## Decision Guidelines

| Scenario | Decision |
|----------|----------|
| Names are variations of same entity | MERGE |
| Same email domain + similar names | MERGE |
| Same organization, different division | UPDATE |
| Similar name but different industry | CREATE |
| No matches above 0.5 | CREATE |

## Entities to Reconcile

```json
{json.dumps({"entities": entities_json}, indent=2)}
```

## Output Format

Return ONLY valid JSON:
```json
{{
  "decisions": [
    {{
      "entity_name": "...",
      "action": "merge|update|create",
      "target_path": "path/to/entity or null",
      "confidence": 0.0-1.0,
      "reasoning": "brief explanation"
    }}
  ]
}}
```

Respond with JSON only, no explanation."""

    def _parse_llm_response(
        self,
        response: str,
        needs_llm: List[Tuple[ExtractedEntity, List[MatchCandidate], int]],
    ) -> List[ReconcileDecision]:
        """Parse LLM response into decisions."""
        try:
            # Find JSON in response
            start = response.find("{")
            end = response.rfind("}") + 1

            if start < 0 or end <= start:
                raise ValueError("No JSON found in response")

            data = json.loads(response[start:end])
            llm_decisions = data.get("decisions", [])

            decisions = []
            for entity, candidates, _ in needs_llm:
                # Find matching decision (case-insensitive)
                d_data = next(
                    (
                        d
                        for d in llm_decisions
                        if d.get("entity_name", "").lower() == entity.name.lower()
                    ),
                    None,
                )

                if d_data:
                    action = d_data.get("action", "create").lower()
                    confidence = d_data.get("confidence", 0.7)

                    decisions.append(
                        ReconcileDecision(
                            entity_name=entity.name,
                            action=action,
                            target_path=d_data.get("target_path"),
                            confidence=confidence,
                            reasoning=d_data.get("reasoning", "LLM decision"),
                            needs_review=confidence < 0.8,
                            source_entity=entity,
                            candidates=candidates,
                        )
                    )
                else:
                    # Not found in response
                    decisions.append(
                        ReconcileDecision(
                            entity_name=entity.name,
                            action="create",
                            confidence=0.5,
                            reasoning="Not found in LLM response",
                            needs_review=True,
                            source_entity=entity,
                            candidates=candidates,
                        )
                    )

            return decisions

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log_audit("reconciliation", "parse_failed", {
                "error": str(e),
                "response_preview": response[:200],
            })

            # Return CREATE with review flag for all
            return [
                ReconcileDecision(
                    entity_name=entity.name,
                    action="create",
                    confidence=0.5,
                    reasoning="Failed to parse LLM response",
                    needs_review=True,
                    source_entity=entity,
                    candidates=candidates,
                )
                for entity, candidates, _ in needs_llm
            ]
