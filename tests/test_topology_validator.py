# SPDX-FileCopyrightText: Copyright contributors to the OpenAN project
# SPDX-License-Identifier: Apache-2.0

"""Tests for the topology validator.

These tests verify cycle detection, orphan detection, security boundary
validation, and blast-radius computation using the PSOP pydantic model.
"""

import pytest

from orchestrate.core.model.psop import PSOP, Step, Task, JumpCondition
from orchestrate.validation.models import (
    CheckStatus,
    SecurityLevel,
    StepSecurity,
    TopologyValidationInput,
    Verdict,
)
from orchestrate.validation.topology_validator import validate_topology


def _make_step(name: str, agent: str, next_steps: list = None, layer: int = 0) -> Step:
    return Step(
        name=name,
        subtasks=[Task(description=f"task {name}", agent=agent, skill=f"skill-{name}")],
        next=[JumpCondition(step=t, condition="") for t in (next_steps or [])] or None,
        layer=layer,
    )


def test_linear_psop_approves():
    psop = PSOP(
        name="linear",
        steps=[
            _make_step("s1", "agent-a", next_steps=["s2"]),
            _make_step("s2", "agent-b", next_steps=["s3"]),
            _make_step("s3", "agent-c"),
        ],
    )
    report = validate_topology(TopologyValidationInput(psop=psop))
    assert report.final_verdict == Verdict.APPROVE
    assert report.steps["cycle_check"].status == CheckStatus.PASS
    assert report.steps["security_boundary_check"].status == CheckStatus.PASS


def test_cycle_rejects():
    psop = PSOP(
        name="cycle",
        steps=[
            _make_step("s1", "agent-a", next_steps=["s2"]),
            _make_step("s2", "agent-b", next_steps=["s3"]),
            _make_step("s3", "agent-c", next_steps=["s1"]),
        ],
    )
    report = validate_topology(TopologyValidationInput(psop=psop))
    assert report.final_verdict == Verdict.REJECT
    assert report.steps["cycle_check"].status == CheckStatus.REJECT


def test_orphan_warns():
    psop = PSOP(
        name="orphan",
        steps=[
            _make_step("s1", "agent-a", next_steps=["s2"]),
            _make_step("s2", "agent-b"),
            _make_step("s3", "agent-c"),  # isolated
        ],
    )
    report = validate_topology(TopologyValidationInput(psop=psop))
    assert report.final_verdict == Verdict.APPROVE_WITH_CONDITIONS
    assert report.steps["orphan_check"].status == CheckStatus.WARN


def test_l2_to_l1_upstream_rejects():
    psop = PSOP(
        name="upward-violation",
        steps=[
            _make_step("l2_step", "agent-l2", next_steps=["l1_step"]),
            _make_step("l1_step", "agent-l1"),
        ],
    )
    security = {
        "l2_step": StepSecurity(step_name="l2_step", security_level=SecurityLevel.L2_FUNCTIONAL),
        "l1_step": StepSecurity(step_name="l1_step", security_level=SecurityLevel.L1_STRATEGIC),
    }
    report = validate_topology(TopologyValidationInput(psop=psop, step_security=security))
    assert report.final_verdict == Verdict.REJECT
    assert report.steps["security_boundary_check"].status == CheckStatus.REJECT


def test_l1_to_l2_downstream_allows():
    psop = PSOP(
        name="downward-ok",
        steps=[
            _make_step("l1_step", "agent-l1", next_steps=["l2_step"]),
            _make_step("l2_step", "agent-l2"),
        ],
    )
    security = {
        "l1_step": StepSecurity(step_name="l1_step", security_level=SecurityLevel.L1_STRATEGIC),
        "l2_step": StepSecurity(step_name="l2_step", security_level=SecurityLevel.L2_FUNCTIONAL),
    }
    report = validate_topology(TopologyValidationInput(psop=psop, step_security=security))
    assert report.final_verdict == Verdict.APPROVE
    assert report.steps["security_boundary_check"].status == CheckStatus.PASS


def test_break_glass_allows_l2_to_l1():
    psop = PSOP(
        name="break-glass",
        steps=[
            _make_step("l2_step", "agent-l2", next_steps=["l1_step"]),
            _make_step("l1_step", "agent-l1"),
        ],
    )
    security = {
        "l2_step": StepSecurity(step_name="l2_step", security_level=SecurityLevel.L2_FUNCTIONAL),
        "l1_step": StepSecurity(
            step_name="l1_step",
            security_level=SecurityLevel.L1_STRATEGIC,
            break_glass=True,
        ),
    }
    report = validate_topology(TopologyValidationInput(psop=psop, step_security=security))
    assert report.final_verdict == Verdict.APPROVE
    assert report.steps["security_boundary_check"].status == CheckStatus.PASS
    assert report.escalation_required is True


def test_blast_radius_computed():
    psop = PSOP(
        name="blast",
        steps=[
            _make_step("root", "agent-root", next_steps=["a", "b"]),
            _make_step("a", "agent-a", next_steps=["c"]),
            _make_step("b", "agent-b", next_steps=["c"]),
            _make_step("c", "agent-c"),
        ],
    )
    report = validate_topology(TopologyValidationInput(psop=psop, max_blast_radius_depth=3))
    assert report.steps["blast_radius"].status == CheckStatus.PASS
    assert "3 steps" in report.steps["blast_radius"].detail
    assert "root" in report.steps["blast_radius"].affected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
