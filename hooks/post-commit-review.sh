#!/bin/bash
# post-commit-review.sh — Forge post-commit review hook
#
# Queues a lightweight code review for every commit by writing:
#   1. A .review file in .dev/reviews/pending/ (the durable queue)
#   2. A signal file in .dev/signals/ (ephemeral notification)
#   3. A one-line entry in .dev/reviews/hook.log
#
# This hook is a pure file producer — no tmux interaction, no forge-bridge
# dependency for core functionality. Signal writing is best-effort.
#
# The hook ALWAYS exits 0. It never blocks or delays commits.

main() {
    # --- resolve paths (worktree-safe) ---
    local git_dir repo_root
    git_dir="$(git rev-parse --git-dir 2>/dev/null)" || return 0
    repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || return 0

    local reviews_dir="$repo_root/.dev/reviews"
    local pending_dir="$reviews_dir/pending"
    local signals_dir="$repo_root/.dev/signals"
    local hook_log="$reviews_dir/hook.log"
    local timestamp
    timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local short_ts
    short_ts="$(date -u +%Y%m%d-%H%M%S)"

    # --- guards ---

    # Environment escape hatch
    if [ "${FORGE_SKIP_REVIEW:-0}" = "1" ]; then
        mkdir -p "$reviews_dir"
        printf '%s skip FORGE_SKIP_REVIEW=1\n' "$timestamp" >> "$hook_log" 2>/dev/null
        return 0
    fi

    # Subject-line [no-review] tag
    local subject
    subject="$(git log -1 --format=%s HEAD 2>/dev/null)" || return 0
    if printf '%s' "$subject" | grep -q '\[no-review\]'; then
        mkdir -p "$reviews_dir"
        printf '%s skip [no-review] in subject\n' "$timestamp" >> "$hook_log" 2>/dev/null
        return 0
    fi

    # Merge commit (2+ parents)
    local parents
    parents="$(git rev-list --parents -n1 HEAD 2>/dev/null)" || return 0
    local parent_count
    parent_count=$(echo "$parents" | wc -w)
    if [ "$parent_count" -gt 2 ]; then
        mkdir -p "$reviews_dir"
        printf '%s skip merge-commit\n' "$timestamp" >> "$hook_log" 2>/dev/null
        return 0
    fi

    # Rebase in progress
    if [ -d "$git_dir/rebase-merge" ] || [ -d "$git_dir/rebase-apply" ]; then
        mkdir -p "$reviews_dir"
        printf '%s skip rebase-in-progress\n' "$timestamp" >> "$hook_log" 2>/dev/null
        return 0
    fi

    # --- capture metadata ---
    local full_hash short_hash commit_msg commit_author committer_ident

    full_hash="$(git rev-parse HEAD 2>/dev/null)" || return 0
    short_hash="${full_hash:0:12}"

    # Sanitize subject: keep alphanumeric, spaces, and safe punctuation
    commit_msg="$(printf '%s' "$subject" | tr -cd '[:alnum:][:space:]._:/-')"

    commit_author="$(git log -1 --format=%an HEAD 2>/dev/null)"

    # Best-effort committer identity for self-review routing
    committer_ident="${GIT_AUTHOR_NAME:-unknown}"
    if [ "$committer_ident" = "unknown" ] && [ -n "${TMUX_PANE:-}" ]; then
        committer_ident="tmux-pane-${TMUX_PANE}"
    fi

    # Diffstat (--root handles root commits)
    local diffstat
    diffstat="$(git diff-tree --stat --no-commit-id --root HEAD 2>/dev/null)"

    # Full diff with deterministic flags
    local diff_full diff_lines truncated
    diff_full="$(git show --format="" --no-ext-diff --no-color --find-renames -p HEAD 2>/dev/null)"
    diff_lines="$(printf '%s' "$diff_full" | wc -l | tr -d ' ')"
    truncated="false"

    # Diff size guard: truncate at 500 lines
    if [ "$diff_lines" -gt 500 ] 2>/dev/null; then
        diff_full="$(printf '%s' "$diff_full" | head -500)"
        diff_full="$diff_full
[TRUNCATED — full diff: git show $full_hash]"
        truncated="true"
    fi

    # --- write review request file (atomic) ---
    mkdir -p "$pending_dir"

    local review_filename="${short_ts}-${short_hash}.review"
    local review_tmp="$pending_dir/.${review_filename}.tmp"
    local review_dst="$pending_dir/$review_filename"

    cat > "$review_tmp" << REVIEWEOF
---
commit: $full_hash
short: $short_hash
author: "$commit_author"
committer_ident: "$committer_ident"
subject: "$commit_msg"
timestamp: "$timestamp"
diff_lines: $diff_lines
truncated: $truncated
---
DIFFSTAT:
$diffstat

DIFF:
$diff_full
REVIEWEOF

    mv "$review_tmp" "$review_dst" 2>/dev/null || return 0

    # --- write signal (best-effort, no tmux) ---
    if command -v forge-bridge >/dev/null 2>&1 || [ -x "$HOME/bin/forge-bridge" ]; then
        mkdir -p "$signals_dir"
        local signal_tmp="$signals_dir/.review-${full_hash}.signal.tmp"
        local signal_dst="$signals_dir/review-${full_hash}.signal"
        printf 'from: post-commit\ntimestamp: %s\nmessage: review pending: %s\n' \
            "$timestamp" "$full_hash" > "$signal_tmp" 2>/dev/null
        mv "$signal_tmp" "$signal_dst" 2>/dev/null
    fi

    # --- log ---
    printf '%s %s queued %s\n' "$timestamp" "$short_hash" "$review_filename" >> "$hook_log" 2>/dev/null
}

main "$@" || true
exit 0
