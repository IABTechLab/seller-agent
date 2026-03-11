#!/usr/bin/env python3
"""Generate PROGRESS.md from beads issues.jsonl for GitHub visibility."""

import json
import re
import os
from datetime import datetime, timezone
from pathlib import Path

BEADS_DIR = Path(__file__).parent
JSONL_PATH = BEADS_DIR / "issues.jsonl"
OUTPUT_PATH = BEADS_DIR / "PROGRESS.md"

# Phase grouping by title prefix
PHASE_MAP = {
    "1": ("Phase 1", "CTV/Linear Publisher Pilot"),
    "2": ("Phase 2", "Negotiation & Order Lifecycle"),
    "3": ("Phase 3", "Platform Features"),
    "4": ("Phase 4", "Production Hardening"),
}


def load_issues():
    issues = []
    if not JSONL_PATH.exists():
        return issues
    with open(JSONL_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                issues.append(json.loads(line))
    return issues


def get_phase(title):
    """Extract phase number from title like '1D: ...', '2B: ...', or '1A-Phase1: ...'."""
    # Match sub-tasks like "1A-Phase1: ..." or "1A-Phase2: ..."
    m = re.match(r"(\d)[A-Z]-", title)
    if m:
        return m.group(1)
    # Match top-level tasks like "1D: ..."
    m = re.match(r"(\d)[A-Z]:", title)
    if m:
        return m.group(1)
    return None


def get_parent_task(title):
    """Extract parent task ID from sub-task title like '1A-Phase1: ...' -> '1A'."""
    m = re.match(r"(\d[A-Z])-", title)
    if m:
        return m.group(1)
    return None


def get_sort_key(title):
    """Sort key: phase number then letter then sub-phase, e.g. '1A-Phase2' -> (1, 'A', 2)."""
    # Sub-task: "1A-Phase2: ..."
    m = re.match(r"(\d)([A-Z])-Phase(\d+):", title)
    if m:
        return (int(m.group(1)), m.group(2), int(m.group(3)))
    # Top-level: "1D: ..."
    m = re.match(r"(\d)([A-Z]):", title)
    if m:
        return (int(m.group(1)), m.group(2), 0)
    return (99, title, 0)


def is_blocked(issue, closed_ids):
    """Check if issue has unresolved blockers."""
    deps = issue.get("dependencies") or []
    for dep in deps:
        blocker_id = dep.get("depends_on_id", "")
        if blocker_id not in closed_ids:
            return True
    return False


def get_blocker_ids(issue):
    """Get list of depends_on_id values."""
    deps = issue.get("dependencies") or []
    return [dep.get("depends_on_id", "") for dep in deps]


def progress_bar(done, total, width=20):
    """Generate a Unicode progress bar."""
    if total == 0:
        return f"`[{'░' * width}] 0%`"
    filled = round(width * done / total)
    empty = width - filled
    pct = round(100 * done / total)
    return f"`[{'█' * filled}{'░' * empty}] {pct}% ({done}/{total})`"


def status_icon(issue, closed_ids):
    """Return status icon for an issue."""
    s = issue.get("status", "open")
    if s == "closed":
        return "\\[x]"
    if s == "in_progress":
        return "\\[~]"
    if is_blocked(issue, closed_ids):
        return "\\[!]"
    return "\\[ ]"


def format_date(iso_str):
    """Extract just the date from an ISO timestamp."""
    if not iso_str:
        return ""
    return iso_str[:10]


def generate():
    issues = load_issues()
    if not issues:
        OUTPUT_PATH.write_text("# Progress\n\nNo issues found.\n")
        return

    # Build lookup sets
    closed_ids = {i["id"] for i in issues if i.get("status") == "closed"}
    all_ids = {i["id"] for i in issues}

    # Stats
    total = len(issues)
    closed = len([i for i in issues if i.get("status") == "closed"])
    in_progress = len([i for i in issues if i.get("status") == "in_progress"])
    blocked = len([i for i in issues if i.get("status") not in ("closed",) and is_blocked(i, closed_ids)])
    open_count = total - closed - in_progress

    # Group by phase
    phases = {}
    ungrouped = []
    for issue in issues:
        phase = get_phase(issue.get("title", ""))
        if phase:
            phases.setdefault(phase, []).append(issue)
        else:
            ungrouped.append(issue)

    # Sort each phase
    for phase in phases:
        phases[phase].sort(key=lambda i: get_sort_key(i.get("title", "")))
    ungrouped.sort(key=lambda i: i.get("title", ""))

    # Build markdown
    lines = []
    lines.append("# Seller Agent V2 — Progress\n")
    lines.append(f"**{open_count} open** | **{in_progress} in progress** | **{closed} closed** | **{blocked} blocked** | {total} total\n")
    lines.append(f"{progress_bar(closed, total)}\n")

    # Render each phase
    for phase_num in sorted(phases.keys()):
        phase_name, phase_desc = PHASE_MAP.get(phase_num, (f"Phase {phase_num}", ""))
        lines.append(f"## {phase_name} — {phase_desc}\n")
        lines.append("| | ID | Task | Priority | Blockers | Done |")
        lines.append("|---|---|---|---|---|---|")

        # Separate top-level tasks and sub-tasks
        top_level = []
        sub_tasks = {}  # parent_key -> [issues]
        for issue in phases[phase_num]:
            title = issue.get("title", "")
            parent = get_parent_task(title)
            if parent:
                sub_tasks.setdefault(parent, []).append(issue)
            else:
                top_level.append(issue)

        for issue in top_level:
            icon = status_icon(issue, closed_ids)
            iid = issue["id"]
            title = issue.get("title", "")
            priority = f"P{issue.get('priority', '?')}"
            blockers = get_blocker_ids(issue)
            unresolved = [b for b in blockers if b not in closed_ids]
            blocker_str = ", ".join(unresolved) if unresolved else "—"
            done = format_date(issue.get("closed_at", ""))
            lines.append(f"| {icon} | {iid} | {title} | {priority} | {blocker_str} | {done} |")

            # Render sub-tasks indented right after their parent
            task_key_match = re.match(r"(\d[A-Z]):", title)
            if task_key_match:
                task_key = task_key_match.group(1)
                for sub in sub_tasks.get(task_key, []):
                    sub_icon = status_icon(sub, closed_ids)
                    sub_iid = sub["id"]
                    sub_title = sub.get("title", "")
                    sub_priority = f"P{sub.get('priority', '?')}"
                    sub_blockers = get_blocker_ids(sub)
                    sub_unresolved = [b for b in sub_blockers if b not in closed_ids]
                    sub_blocker_str = ", ".join(sub_unresolved) if sub_unresolved else "—"
                    sub_done = format_date(sub.get("closed_at", ""))
                    lines.append(f"| {sub_icon} | {sub_iid} | &nbsp;&nbsp;↳ {sub_title} | {sub_priority} | {sub_blocker_str} | {sub_done} |")

        lines.append("")

    # Ungrouped issues
    if ungrouped:
        lines.append("## Other\n")
        lines.append("| | ID | Task | Priority | Blockers | Done |")
        lines.append("|---|---|---|---|---|---|")
        for issue in ungrouped:
            icon = status_icon(issue, closed_ids)
            iid = issue["id"]
            title = issue.get("title", "")
            priority = f"P{issue.get('priority', '?')}"
            blockers = get_blocker_ids(issue)
            unresolved = [b for b in blockers if b not in closed_ids]
            blocker_str = ", ".join(unresolved) if unresolved else "—"
            done = format_date(issue.get("closed_at", ""))
            lines.append(f"| {icon} | {iid} | {title} | {priority} | {blocker_str} | {done} |")
        lines.append("")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append("---")
    lines.append(f"*Last updated: {now} — auto-generated by beads*\n")

    OUTPUT_PATH.write_text("\n".join(lines))


if __name__ == "__main__":
    generate()
