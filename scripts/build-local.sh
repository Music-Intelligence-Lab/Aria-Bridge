#!/bin/bash
# Local build script for Aria Bridge (macOS, Apple Silicon).
# Mirrors build-local.ps1. Each step is skipped automatically when its output
# already exists. Delete the relevant artifact to force a rebuild.
#
# Produces an ad-hoc-signed (unsigned for distribution) build. Fine for running
# on your own Mac; other Macs will need: xattr -dr com.apple.quarantine "Aria Launcher.app"
#
# Usage:
#   ./build-local.sh                       # build everything into ~/Downloads/AriaBridge
#   ./build-local.sh --out /path/to/dir    # custom output dir
#   ./build-local.sh --skip-juce           # reuse existing JUCE artifacts
#   ./build-local.sh --skip-backend        # reuse existing dist/aria_backend
#   ./build-local.sh --skip-frontend       # reuse existing launcher build
#
# Prereqs: Xcode + CommandLineTools, CMake, Node.js, a Python 3.11 venv with
#   real-time/requirements.txt installed plus pyinstaller. Activate it first.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HOME/Downloads/AriaBridge"
SKIP_JUCE=0; SKIP_BACKEND=0; SKIP_FRONTEND=0

while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT="$2"; shift 2 ;;
        --skip-juce) SKIP_JUCE=1; shift ;;
        --skip-backend) SKIP_BACKEND=1; shift ;;
        --skip-frontend) SKIP_FRONTEND=1; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

step() { printf '\n==> %s\n' "$1"; }
done_() { printf '    %s\n' "$1"; }
warn() { printf '    %s\n' "$1"; }

SECONDS=0

step "Killing any running Aria processes..."
pkill -f "Aria Launcher" 2>/dev/null || true
pkill -f "aria_backend"  2>/dev/null || true
pkill -f "Aria Bridge"   2>/dev/null || true
sleep 1

mkdir -p "$ROOT/dist"

JUCE_BASE="$ROOT/real-time/Plugin/build/AriaBridge_artefacts/Release"
JUCE_APP="$JUCE_BASE/Standalone/Aria Bridge.app"
JUCE_VST="$JUCE_BASE/VST3/Aria Bridge.vst3"
BACKEND_BIN="$ROOT/dist/aria_backend"
LAUNCHER_ZIP="$ROOT/dist/AriaLauncher-mac.zip"

# ── 1. JUCE: Standalone (.app) + VST3 ─────────────────────────────────────────
if [ "$SKIP_JUCE" = 1 ]; then
    warn "Skipping JUCE build (flag set)."
elif [ -d "$JUCE_APP" ] && [ -d "$JUCE_VST" ]; then
    warn "JUCE artifacts found, skipping build. Delete real-time/Plugin/build to rebuild."
else
    step "Configuring JUCE (CMake + Xcode)..."
    cmake -S "$ROOT/real-time/Plugin" -B "$ROOT/real-time/Plugin/build" -G Xcode
    step "Building JUCE Standalone + VST3..."
    cmake --build "$ROOT/real-time/Plugin/build" --config Release --target AriaBridge_Standalone
    cmake --build "$ROOT/real-time/Plugin/build" --config Release --target AriaBridge_VST3
fi

# Ad-hoc sign so Gatekeeper lets the standalone run locally.
step "Ad-hoc signing the standalone..."
codesign --force --deep --sign - "$JUCE_APP"

# Stage JUCE artifacts into dist/ (where electron-builder expects them).
rm -rf "$ROOT/dist/Aria Bridge.app" "$ROOT/dist/Aria Bridge.vst3"
cp -R "$JUCE_APP" "$ROOT/dist/Aria Bridge.app"
cp -R "$JUCE_VST" "$ROOT/dist/Aria Bridge.vst3"
done_ "JUCE artifacts staged to dist/."

# ── 2. Python backend → dist/aria_backend ─────────────────────────────────────
if [ "$SKIP_BACKEND" = 1 ]; then
    warn "Skipping PyInstaller (flag set)."
elif [ -f "$BACKEND_BIN" ]; then
    warn "dist/aria_backend found, skipping PyInstaller. Delete it to rebuild."
else
    step "Building aria_backend (PyInstaller, bundles MLX/Metal)..."
    ( cd "$ROOT" && pyinstaller scripts/aria_backend.spec )
    chmod +x "$BACKEND_BIN"
    rm -rf "$ROOT/build"   # PyInstaller work dir, not needed after build
fi
done_ "aria_backend ready in dist/."

# ── 3. Electron launcher → dist/AriaLauncher-mac.zip ──────────────────────────
if [ "$SKIP_FRONTEND" = 1 ]; then
    warn "Skipping electron-builder (flag set)."
elif [ -f "$LAUNCHER_ZIP" ]; then
    warn "dist/AriaLauncher-mac.zip found, skipping electron-builder. Delete it to rebuild."
else
    step "Building AriaLauncher (electron-builder, mac arm64)..."
    mkdir -p "$ROOT/models" "$ROOT/feedback"
    ( cd "$ROOT/front-end" && npm install && npm run build:mac )
fi
done_ "AriaLauncher built."

# ── 4. Package into $OUT ──────────────────────────────────────────────────────
step "Packaging into $OUT ..."
rm -rf "$OUT"
mkdir -p "$OUT/models" "$OUT/feedback"

# Electron app (.app + bundled backend/plugin in its Resources).
unzip -q "$LAUNCHER_ZIP" -d "$OUT"
done_ "Electron app unpacked."

# Ableton MIDI device + set.
cp -R "$ROOT/real-time/ableton" "$OUT/ableton"
done_ "Ableton files copied."

[ -f "$ROOT/README.md" ] && cp "$ROOT/README.md" "$OUT/README.md"

# Model: copy the newest .safetensors/.gen from repo models/, else leave a note.
SRC_MODEL="$(ls -t "$ROOT/models/"*.safetensors "$ROOT/models/"*.gen 2>/dev/null | head -n 1 || true)"
if [ -n "$SRC_MODEL" ]; then
    cp "$SRC_MODEL" "$OUT/models/$(basename "$SRC_MODEL")"
    done_ "Model copied: $(basename "$SRC_MODEL")"
else
    echo "Download model-gen.safetensors from HuggingFace (EleutherAI/aria) and place it here." \
        > "$OUT/models/PUT_MODEL_HERE.txt"
    warn "No model found in models/ - place a .safetensors in $OUT/models/ before running."
fi

printf '\nBuild complete in %ds\n' "$SECONDS"
printf 'Output: %s\n' "$OUT"
printf 'Launch: open "%s/Aria Launcher.app"\n' "$OUT"
printf 'First run on another Mac: xattr -dr com.apple.quarantine "%s/Aria Launcher.app"\n' "$OUT"
