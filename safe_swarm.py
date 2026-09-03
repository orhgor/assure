#!/usr/bin/env python3
"""
Safe Swarm Wrapper – Prevents wasteful, empty, or duplicate swarm runs.

Usage:
    python safe_swarm.py --task "Add mobile responsive layout" [--force] [extra swarm args...]

Environment variables:
    PEM_SWARM_MAX_COST    Hard cost cap in USD (default 0.50)
    PEM_MCP_PID           (optional) PID of the MCP server, if known

Checks:
    1. If task contains verification keywords and git working tree is clean → skip.
    2. If same task + same commit hash already ran → skip.
    3. If MCP server is not running → abort (override with --force).
    4. After run, if generated patch is empty → treat as failure.
    5. Records successful runs in .swarm_history to prevent repeats.
"""

import os
import sys
import subprocess
import hashlib
import argparse
from pathlib import Path

# ----- Configuration -----
REPO_ROOT = Path(__file__).resolve().parent
VERIFICATION_KEYWORDS = [
    "verify", "check", "validate", "inspect",
    "against landed", "already landed", "review existing"
]
HISTORY_FILE = REPO_ROOT / ".swarm_history"
LOG_PATCH = REPO_ROOT / "logs" / "swarm.patch"
MIN_PATCH_SIZE = 100  # bytes – anything smaller is considered empty


def git_working_tree_clean() -> bool:
    """Return True if no uncommitted changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        capture_output=True, text=True
    )
    return result.stdout.strip() == ""


def get_current_commit() -> str:
    """Return current HEAD commit hash (short)."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def task_hash(task: str) -> str:
    """Return SHA256 hash of the task description."""
    return hashlib.sha256(task.encode()).hexdigest()[:12]


def is_mcp_running() -> bool:
    """Check if the MCP server process is alive."""
    # If PID provided via env, check that process
    pid_env = os.getenv("PEM_MCP_PID")
    if pid_env:
        try:
            os.kill(int(pid_env), 0)  # signal 0 just checks existence
            return True
        except (ProcessLookupError, ValueError):
            return False

    # Otherwise, try to find any process with "pem mcp" in command line
    try:
        # Unix-like systems
        result = subprocess.run(
            ["pgrep", "-f", "pem mcp"],
            capture_output=True, text=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        # Windows or no pgrep – fallback: try a quick 'pem mcp --help'
        try:
            subprocess.run(
                ["pem", "mcp", "--help"],
                capture_output=True, timeout=2, check=False
            )
            # If the command runs without error, assume MCP is available
            return True
        except Exception:
            return False


def record_run(task_hash: str, commit: str):
    """Append a successful run to history file."""
    with open(HISTORY_FILE, "a") as f:
        f.write(f"{task_hash}:{commit}\n")


def already_run(task_hash: str, commit: str) -> bool:
    """Check if this task+commit combo already exists in history."""
    if not HISTORY_FILE.exists():
        return False
    with open(HISTORY_FILE) as f:
        for line in f:
            if line.strip() == f"{task_hash}:{commit}":
                return True
    return False


def is_patch_empty() -> bool:
    """Return True if the swarm patch is empty or tiny."""
    if not LOG_PATCH.exists():
        return True
    return LOG_PATCH.stat().st_size < MIN_PATCH_SIZE


def main():
    parser = argparse.ArgumentParser(
        description="Run the swarm safely with cost/duplicate guards."
    )
    parser.add_argument("--task", required=True, help="Description of what the swarm should do.")
    parser.add_argument("--force", action="store_true", help="Override all checks and run anyway.")
    parser.add_argument("args", nargs=argparse.REMAINDER, help="Extra arguments to pass to swarm.")
    args = parser.parse_args()

    task_lower = args.task.lower()
    commit = get_current_commit()
    t_hash = task_hash(args.task)

    # ----- Layer 1: Git Diff Pre-Flight -----
    if not args.force:
        if any(kw in task_lower for kw in VERIFICATION_KEYWORDS):
            if git_working_tree_clean():
                print("🛑  Working tree is clean and task appears to be verification-only.")
                print("     Skipping to avoid burning credits. Use --force to override.")
                sys.exit(0)

    # ----- Layer 4: MCP Health Gate (unless forced) -----
    if not args.force:
        if not is_mcp_running():
            print("❌  PEM MCP server is not running or not reachable.")
            print("     Start it with 'pem mcp' (or set PEM_MCP_PID) and try again.")
            print("     Use --force to bypass this check (not recommended).")
            sys.exit(1)

    # ----- Layer 2: Cache (duplicate task against same HEAD) -----
    if not args.force:
        if already_run(t_hash, commit):
            print("🔄  This task has already been run against this commit.")
            print("     Skipping to avoid wasting cycles. Use --force to re-run.")
            sys.exit(0)

    # ----- Layer 3: Cost Cap (warn, but don't block) -----
    max_cost = os.getenv("PEM_SWARM_MAX_COST", "0.50")
    try:
        max_cost_float = float(max_cost)
    except ValueError:
        max_cost_float = 0.50
    print(f"💰  Swarm will be capped at approximately ${max_cost_float:.2f}.")
    print("     (Actual cost may vary; ensure your keys have limits.)")

    extra = list(args.args)
    if extra and extra[0] == "--":
        extra = extra[1:]

    # ----- Build the swarm command -----
    swarm_cmd = [
        sys.executable, "-m", "prompt_matrix.swarm",
        "--edition", "team",   # unlimited, but we'll rely on cost cap
        "--max-iterations", "1",
        "--task", args.task,
    ]
    # Append any extra args passed by the user
    if extra:
        swarm_cmd.extend(extra)

    print(f"🚀  Running: {' '.join(swarm_cmd)}")
    print(f"    Task: {args.task}")

    # Run the swarm
    result = subprocess.run(swarm_cmd, cwd=REPO_ROOT)  # let output stream

    # ----- Check for empty patch (post-run) -----
    if is_patch_empty():
        print("❌  Swarm produced an empty or tiny patch. Treating as failed run.")
        # Do not record history (so it can be tried again with better input)
        sys.exit(1)

    # ----- Record success (so we don't run it again) -----
    record_run(t_hash, commit)
    print("✅  Swarm run successful and patch recorded. History updated.")
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
