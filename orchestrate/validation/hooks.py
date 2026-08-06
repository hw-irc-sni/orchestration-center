# SPDX-FileCopyrightText: Copyright contributors to the OpenAN project
# SPDX-License-Identifier: Apache-2.0

"""Integration hook example: validate a PSOP before saving.

This is a drop-in example for Orchestration Center workflow CRUD handlers.
It is not imported automatically to avoid side effects; maintainers can wire
it into `frontend_support_server.py` or the workflow router of choice.
"""

from fastapi import HTTPException, status
from orchestrate.core.model.psop import PSOP
from orchestrate.validation.models import (
    SecurityLevel,
    StepSecurity,
    TopologyValidationInput,
    Verdict,
)
from orchestrate.validation.topology_validator import validate_topology


async def validate_before_save(
    psop: PSOP,
    step_security: dict[str, StepSecurity] | None = None,
    agent_security: dict[str, SecurityLevel] | None = None,
) -> None:
    """Raise HTTPException if PSOP topology validation fails.

    Call this function inside workflow create/update endpoints before
    persisting the PSOP. Optional security metadata can be supplied from
    agent registry tags or external governance configuration.
    """
    report = validate_topology(
        TopologyValidationInput(
            psop=psop,
            step_security=step_security or {},
            agent_security=agent_security or {},
        )
    )

    if report.final_verdict == Verdict.REJECT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Workflow topology validation failed",
                "errors": report.errors,
                "warnings": report.warnings,
            },
        )
    # APPROVE_WITH_CONDITIONS is allowed but can be logged or escalated.
    if report.final_verdict == Verdict.APPROVE_WITH_CONDITIONS:
        # TODO: emit audit/escalation log
        pass
