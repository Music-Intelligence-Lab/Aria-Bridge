#!/bin/bash
# Launch the Aria Bridge backend on macOS (Apple Silicon / MLX) in m4l mode.
# Pairs with the Max for Live device: real-time/ableton/Aria Bridge.amxd
# MIDI ports expected: ARIA_IN / ARIA_OUT (IAC Driver). OSC in:9000 out:9001.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/venv/bin/python"
MODEL="$ROOT/models/model-gen.safetensors"

if [ ! -f "$MODEL" ]; then
  echo "Model not found at $MODEL"
  exit 1
fi

cd "$ROOT/real-time"
exec "$PY" ableton_bridge.py m4l \
  --checkpoint "$MODEL" \
  --device mlx \
  --feedback --data-dir "$ROOT/data"
