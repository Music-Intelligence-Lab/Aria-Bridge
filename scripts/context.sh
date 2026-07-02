#!/bin/bash
# Sync the private "context" repo: a separate git dir over THIS worktree that versions
# the gitignored context files (STATUS.md, CLAUDE.md, design docs, dev tools, Track.mid,
# the graphify-out/ knowledge graph) plus, best-effort, the Claude session transcripts — so
# they move between machines without touching the main Aria-Bridge repo.
#
#   scripts/context.sh clone <url>   # first-time setup on a new machine
#   scripts/context.sh save ["msg"]  # mirror sessions, add context files, commit, push
#   scripts/context.sh load          # pull latest, restore sessions
#   scripts/context.sh <git args>    # passthrough (status, log, diff, ...)
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GD="$ROOT/.aria-context.git"
CTX() { git --git-dir="$GD" --work-tree="$ROOT" "$@"; }

FILES=(CLAUDE.md STATUS.md SYNC.md tasks.md TODO.md ARIA_Model.pdf
  real-time/docs/TEMPO_AND_TIMING.md real-time/docs/TOKENS_AND_CLOCK.md
  real-time/tools/count_tokens.py real-time/tools/midi_to_clip.py
  scripts/list_midi_ports.py real-time/tests/Track.mid graphify-out .claude-sessions)

# This machine's Claude transcript dir for THIS project (best-effort; slug differs per OS).
claude_dir() {
  local base="$HOME/.claude/projects"
  [ -d "$base" ] || return 1
  local d; d="$(ls "$base" 2>/dev/null | grep -i 'aria-bridge' | head -1)"
  [ -n "$d" ] && echo "$base/$d"
}
sessions_to_repo() {
  local d; d="$(claude_dir)" || { echo "  (no ~/.claude project dir; skipping sessions)"; return 0; }
  mkdir -p "$ROOT/.claude-sessions"
  cp -f "$d"/*.jsonl "$ROOT/.claude-sessions/" 2>/dev/null || true
  cp -rf "$d"/memory "$ROOT/.claude-sessions/" 2>/dev/null || true
  echo "  sessions copied from $d"
}
sessions_from_repo() {
  local d; d="$(claude_dir)" || { echo "  (no ~/.claude project dir; sessions left in .claude-sessions/)"; return 0; }
  [ -d "$ROOT/.claude-sessions" ] || return 0
  cp -f "$ROOT/.claude-sessions"/*.jsonl "$d/" 2>/dev/null || true
  cp -rf "$ROOT/.claude-sessions/memory" "$d/" 2>/dev/null || true
  echo "  sessions restored to $d"
}

cmd="${1:-}"; [ $# -gt 0 ] && shift
case "$cmd" in
  clone)
    git clone --bare "$1" "$GD"
    CTX config core.bare false
    CTX config status.showUntrackedFiles no
    CTX checkout -f main
    sessions_from_repo
    echo "Context loaded into $ROOT"
    ;;
  save)
    sessions_to_repo
    for f in "${FILES[@]}"; do
      [ -e "$ROOT/$f" ] || continue
      if [ "$f" = "graphify-out" ]; then
        # Sync the graph but skip machine-specific interpreter/root paths (they'd
        # flip-flop and conflict between Windows and Mac).
        CTX add -f "$f" ':!graphify-out/.graphify_python' ':!graphify-out/.graphify_root'
      else
        CTX add -f "$f"
      fi
    done
    CTX commit -m "${1:-context update}" && CTX push
    ;;
  load)
    CTX pull --no-rebase
    sessions_from_repo
    ;;
  "")
    echo "usage: context.sh {clone <url>|save [msg]|load|<git args>}"
    ;;
  *)
    CTX "$cmd" "$@"
    ;;
esac
