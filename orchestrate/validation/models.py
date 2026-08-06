# SPDX-FileCopyrightText: Copyright contributors to the OpenAN project
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for topology validation.

Defines the input schema consumed by the topology validator and the
structured report it produces. The validator is intentionally decoupled
from persistence concerns so that it can be invoked before any PSOP is
saved or executed.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from orchestrate.core.model.psop import PSOP


class SecurityLevel(str, Enum):
    """Security tier inspired by the MAEA layered governance model.

    The numeric order is intentional: lower values are higher-trust layers.
    A step/task at L1 (strategic) must not receive upstream delegation from
    L2/L3 unless an explicit break-glass escalation is recorded.
    """

    L1_STRATEGIC = "L1"
    L2_FUNCTIONAL = "L2"
    L3_EXTERNAL = "L3"


class Verdict(str, Enum):
    """Final validation verdict."""

    APPROVE = "approve"
    APPROVE_WITH_CONDITIONS = "approve_with_conditions"
    REJECT = "reject"


class CheckStatus(str, Enum):
    """Status of an individual validation check."""

    PASS = "pass"
    WARN = "warn"
    REJECT = "reject"


class StepSecurity(BaseModel):
    """Optional per-step security metadata.

    If not provided, the validator falls back to agent-level metadata or
    treats the step as L2_FUNCTIONAL.
    """

    step_name: str = Field(..., description="Step name as declared in PSOP.steps")
    security_level: SecurityLevel = Field(SecurityLevel.L2_FUNCTIONAL, description="Security tier")
    break_glass: bool = Field(False, description="Explicit escalation allowed for this step")


class TopologyValidationInput(BaseModel):
    """Input to the topology validator."""

    psop: PSOP = Field(..., description="PSOP workflow to validate")
    step_security: Optional[Dict[str, StepSecurity]] = Field(
        default_factory=dict,
        description="Optional step-level security metadata keyed by step name",
    )
    agent_security: Optional[Dict[str, SecurityLevel]] = Field(
        default_factory=dict,
        description="Optional agent-level security tier keyed by agent name",
    )
    max_blast_radius_depth: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum depth for downstream blast-radius traversal",
    )


class CheckResult(BaseModel):
    """Result of a single validation check."""

    status: CheckStatus = Field(..., description="Check status")
    detail: str = Field("", description="Human-readable detail message")
    affected: Optional[List[str]] = Field(default_factory=list, description="Affected node identifiers")


class TopologyValidationReport(BaseModel):
    """Structured report produced by the topology validator."""

    psop_id: Optional[str] = Field(None, description="PSOP identifier")
    psop_name: str = Field(..., description="PSOP name")
    dag_summary: Dict[str, Any] = Field(
        default_factory=dict,
        description="Summary of the extracted step graph (nodes, edges, sources, sinks)",
    )
    steps: Dict[str, CheckResult] = Field(
        default_factory=dict,
        description="Per-step check results keyed by check name",
    )
    final_verdict: Verdict = Field(..., description="Aggregated validation verdict")
    escalation_required: bool = Field(
        False, description="Whether a break-glass escalation is required to proceed"
    )
    errors: List[str] = Field(default_factory=list, description="High-level error messages")
    warnings: List[str] = Field(default_factory=list, description="Non-blocking warnings")
