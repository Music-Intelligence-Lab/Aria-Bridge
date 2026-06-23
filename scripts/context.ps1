# Sync the private "context" repo (separate git dir over THIS worktree) that versions the
# gitignored context files (STATUS.md, CLAUDE.md, design docs, dev tools, Track.mid) plus,
# best-effort, the Claude session transcripts — without touching the main Aria-Bridge repo.
#
#   scripts\context.ps1 clone <url>   # first-time setup on a new machine
#   scripts\context.ps1 save ["msg"]  # mirror sessions, add context files, commit, push
#   scripts\context.ps1 load          # pull latest, restore sessions
#   scripts\context.ps1 <git args>    # passthrough (status, log, ...)
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Args)

$Root = (Resolve-Path "$PSScriptRoot\..").Path
$GD   = Join-Path $Root ".aria-context.git"
function Ctx { & git --git-dir="$GD" --work-tree="$Root" @args }

$Files = @("CLAUDE.md","STATUS.md","tasks.md","ARIA_Model.pdf",
  "real-time/docs/TEMPO_AND_TIMING.md","real-time/docs/TOKENS_AND_CLOCK.md",
  "real-time/tools/count_tokens.py","real-time/tools/midi_to_clip.py",
  "scripts/list_midi_ports.py","real-time/tests/Track.mid",".claude-sessions")

function Claude-Dir {
  $base = Join-Path $env:USERPROFILE ".claude\projects"
  if (-not (Test-Path $base)) { return $null }
  $d = Get-ChildItem $base -Directory | Where-Object { $_.Name -match "aria-bridge" } | Select-Object -First 1
  if ($d) { return $d.FullName } else { return $null }
}
function Sessions-ToRepo {
  $d = Claude-Dir; if (-not $d) { Write-Host "  (no ~/.claude project dir; skipping sessions)"; return }
  $dst = Join-Path $Root ".claude-sessions"; New-Item -ItemType Directory -Force -Path $dst | Out-Null
  Copy-Item "$d\*.jsonl" $dst -Force -ErrorAction SilentlyContinue
  if (Test-Path "$d\memory") { Copy-Item "$d\memory" $dst -Recurse -Force -ErrorAction SilentlyContinue }
  Write-Host "  sessions copied from $d"
}
function Sessions-FromRepo {
  $d = Claude-Dir; if (-not $d) { Write-Host "  (no ~/.claude project dir; sessions left in .claude-sessions)"; return }
  $src = Join-Path $Root ".claude-sessions"; if (-not (Test-Path $src)) { return }
  Copy-Item "$src\*.jsonl" $d -Force -ErrorAction SilentlyContinue
  if (Test-Path "$src\memory") { Copy-Item "$src\memory" $d -Recurse -Force -ErrorAction SilentlyContinue }
  Write-Host "  sessions restored to $d"
}

$cmd = if ($Args.Count -gt 0) { $Args[0] } else { "" }
$rest = if ($Args.Count -gt 1) { $Args[1..($Args.Count-1)] } else { @() }
switch ($cmd) {
  "clone" {
    & git clone --bare $rest[0] $GD
    Ctx config core.bare false
    Ctx config status.showUntrackedFiles no
    Ctx checkout -f main
    Sessions-FromRepo
    Write-Host "Context loaded into $Root"
  }
  "save" {
    Sessions-ToRepo
    foreach ($f in $Files) { if (Test-Path (Join-Path $Root $f)) { Ctx add -f $f } }
    $msg = if ($rest.Count -gt 0) { $rest[0] } else { "context update" }
    Ctx commit -m $msg; Ctx push
  }
  "load" {
    Ctx pull --no-rebase
    Sessions-FromRepo
  }
  "" { Write-Host "usage: context.ps1 {clone <url>|save [msg]|load|<git args>}" }
  default { Ctx @Args }
}
