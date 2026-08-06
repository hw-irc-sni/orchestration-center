# SPDX-FileCopyrightText: Copyright contributors to the OpenAN project
# SPDX-License-Identifier: Apache-2.0

"""Topology validation for OpenAN Orchestration Center PSOP workflows.

This module implements a lightweight, dependency-free, pre-save validator
for PSOP workflows. It detects structural problems (cycles, orphans) and
security-boundary violations inspired by the DeepArchi multi-agent topology
governance model.

The validator is designed to be invoked synchronously inside the workflow
CRUD handlers, before persistence or execution.
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from orchestrate.validation.models import (
    CheckResult,
    CheckStatus,
    SecurityLevel,
    StepSecurity,
    TopologyValidationInput,
    TopologyValidationReport,
    Verdict,
)


def _derive_step_security(
    step_name: str,
    step_security_map: Dict[str, StepSecurity],
    agent_security_map: Dict[str, SecurityLevel],
    agent_by_step: Dict[str, str],
) -> SecurityLevel:
    """Return the security tier of a step.

    Priority:
    1. explicit step-level metadata
    2. agent-level metadata for the step's primary agent
    3. L2_FUNCTIONAL default
    """
    if step_name in step_security_map:
        return step_security_map[step_name].security_level
    agent = agent_by_step.get(step_name)
    if agent and agent in agent_security_map:
        return agent_security_map[agent]
    return SecurityLevel.L2_FUNCTIONAL


def _build_step_graph(steps: List[Any]) -> Tuple[Dict[str, List[str]], Set[str], Set[str]]:
    """Extract a step-level directed graph from a PSOP.

    Returns (edges, sources, sinks). 'next' jump conditions are used as edges.
    """
    edges: Dict[str, List[str]] = defaultdict(list)
    all_steps: Set[str] = {step.name for step in steps}
    has_incoming: Set[str] = set()

    for step in steps:
        targets = step.next or []
        for jump in targets:
            if jump.step in all_steps:
                edges[step.name].append(jump.step)
                has_incoming.add(jump.step)

    sources = all_steps - has_incoming
    sinks = {name for name in all_steps if not edges[name]}
    return dict(edges), sources, sinks


def _detect_cycle(
    edges: Dict[str, List[str]], sources: Set[str]
) -> Tuple[bool, Optional[List[str]]]:
    """Return (has_cycle, path) using DFS with stack-based traceback."""
    visited: Set[str] = set()
    rec_stack: Set[str] = set()
    path: List[str] = []

    def dfs(node: str) -> Tuple[bool, Optional[List[str]]]:
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        for neighbor in edges.get(node, []):
            if neighbor not in visited:
                found, cycle = dfs(neighbor)
                if found:
                    return True, cycle
            elif neighbor in rec_stack:
                idx = path.index(neighbor)
                cycle = path[idx:] + [neighbor]
                return True, cycle
        path.pop()
        rec_stack.remove(node)
        return False, None

    for start in list(sources) + [n for n in edges if n not in visited]:
        if start not in visited:
            found, cycle = dfs(start)
            if found:
                return True, cycle
    return False, None


def _compute_blast_radius(
    edges: Dict[str, List[str]], start: str, max_depth: int
) -> Set[str]:
    """Return all reachable downstream steps up to max_depth from start."""
    visited: Set[str] = set()
    frontier: List[Tuple[str, int]] = [(start, 0)]
    while frontier:
        node, depth = frontier.pop()
        if depth > max_depth or node in visited:
            continue
        visited.add(node)
        for neighbor in edges.get(node, []):
            frontier.append((neighbor, depth + 1))
    visited.discard(start)
    return visited


def _run_cycle_check(
    edges: Dict[str, List[str]], sources: Set[str]
) -> CheckResult:
    has_cycle, cycle = _detect_cycle(edges, sources)
    if has_cycle and cycle:
        return CheckResult(
            status=CheckStatus.REJECT,
            detail=f"Cycle detected: {' -> '.join(cycle)}",
            affected=cycle,
        )
    return CheckResult(status=CheckStatus.PASS, detail="No cycles detected")


def _run_orphan_check(
    edges: Dict[str, List[str]], all_steps: Set[str], sources: Set[str], sinks: Set[str]
) -> CheckResult:
    isolated = {s for s in all_steps if s in sources and s in sinks}
    if isolated:
        return CheckResult(
            status=CheckStatus.WARN,
            detail=f"Isolated steps (no incoming or outgoing edges): {sorted(isolated)}",
            affected=sorted(isolated),
        )
    return CheckResult(status=CheckStatus.PASS, detail="No isolated steps")


def _run_security_boundary_check(
    edges: Dict[str, List[str]],
    step_security_map: Dict[str, StepSecurity],
    agent_security_map: Dict[str, SecurityLevel],
    agent_by_step: Dict[str, str],
) -> CheckResult:
    """Detect L2/L3 -> L1 upstream delegation.

    Lower-trust steps must not pass data upward to higher-trust strategic
    steps unless break-glass is explicitly enabled on the target step.
    """
    numeric_tier = {
        SecurityLevel.L1_STRATEGIC: 1,
        SecurityLevel.L2_FUNCTIONAL: 2,
        SecurityLevel.L3_EXTERNAL: 3,
    }
    violations: List[str] = []
    for source, targets in edges.items():
        source_tier = numeric_tier[_derive_step_security(
            source, step_security_map, agent_security_map, agent_by_step
        )]
        for target in targets:
            target_security = step_security_map.get(target)
            break_glass = target_security.break_glass if target_security else False
            target_tier = numeric_tier[_derive_step_security(
                target, step_security_map, agent_security_map, agent_by_step
            )]
            if source_tier > target_tier and not break_glass:
                violations.append(f"{source}({source_tier}) -> {target}({target_tier})")
    if violations:
        return CheckResult(
            status=CheckStatus.REJECT,
            detail=f"Security boundary violation(s): {violations}",
            affected=violations,
        )
    return CheckResult(
        status=CheckStatus.PASS, detail="No upstream delegation violations"
    )


def _run_blast_radius(
    edges: Dict[str, List[str]], max_depth: int
) -> CheckResult:
    radii: Dict[str, List[str]] = {
        start: sorted(_compute_blast_radius(edges, start, max_depth))
        for start in edges
    }
    max_affected = max((len(v) for v in radii.values()), default=0)
    detail = f"Max downstream blast radius up to depth {max_depth}: {max_affected} steps"
    return CheckResult(
        status=CheckStatus.PASS,
        detail=detail,
        affected=[k for k, v in radii.items() if len(v) == max_affected],
    )


def validate_topology(
    input_data: TopologyValidationInput,
) -> TopologyValidationReport:
    """Validate a PSOP workflow before persistence or execution.

    Args:
        input_data: TopologyValidationInput containing the PSOP and optional
            security metadata.

    Returns:
        TopologyValidationReport with per-step results and aggregated verdict.
    """
    psop = input_data.psop
    steps = psop.steps
    step_names = {step.name for step in steps}

    agent_by_step: Dict[str, str] = {}
    for step in steps:
        if step.subtasks:
            agent_by_step[step.name] = step.subtasks[0].agent

    edges, sources, sinks = _build_step_graph(steps)

    check_results: Dict[str, CheckResult] = {}
    check_results["cycle_check"] = _run_cycle_check(edges, sources)
    check_results["orphan_check"] = _run_orphan_check(edges, step_names, sources, sinks)
    check_results["security_boundary_check"] = _run_security_boundary_check(
        edges,
        input_data.step_security or {},
        input_data.agent_security or {},
        agent_by_step,
    )
    check_results["blast_radius"] = _run_blast_radius(
        edges, input_data.max_blast_radius_depth
    )

    dag_summary = {
        "nodes": sorted(step_names),
        "edges": {k: sorted(v) for k, v in sorted(edges.items())},
        "sources": sorted(sources),
        "sinks": sorted(sinks),
    }

    errors: List[str] = []
    warnings: List[str] = []
    for name, result in check_results.items():
        if result.status == CheckStatus.REJECT:
            errors.append(f"{name}: {result.detail}")
        elif result.status == CheckStatus.WARN:
            warnings.append(f"{name}: {result.detail}")

    final_verdict = Verdict.APPROVE
    if errors:
        final_verdict = Verdict.REJECT
    elif warnings:
        final_verdict = Verdict.APPROVE_WITH_CONDITIONS

    escalation_required = any(
        (step_security.break_glass for step_security in (input_data.step_security or {}).values())
    )

    return TopologyValidationReport(
        psop_id=psop.id,
        psop_name=psop.name,
        dag_summary=dag_summary,
        steps=check_results,
        final_verdict=final_verdict,
        escalation_required=escalation_required,
        errors=errors,
        warnings=warnings,
    )
