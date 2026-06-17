# Aria Real-Time Bridge - Quick Start Guide

This guide helps you get up and running quickly on **any system** without hardcoded paths.

## Step 1: Install Dependencies

```bash
cd aria/real-time
pip install -r requirements.txt
```

## Step 2: Obtain the Model Checkpoint

Download the Aria model from [Hugging Face](https://huggingface.co/EleutherAI/aria) and note its path.

> **Tip**: Place the model in a predictable location like `../models/model-gen.safetensors` (one level up from real-time folder).

## Step 3: Set Up MIDI Ports

- **Windows**: Install [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi/usbnmidi.html) and create virtual ports.
- **macOS/Linux**: Use built-in or virtual MIDI port tools.

Create these ports:
- `ARIA_IN` - for human input
- `ARIA_OUT` - for generated output

Verify ports are available:
```bash
python ableton_bridge.py --list-ports
```

## Step 4: Run the Bridge

### Simple Mode (Clock-Synchronized with Ableton)

```bash
python ableton_bridge.py \
  --checkpoint /path/to/model-gen.safetensors \
  --mode clock \
  --in ARIA_IN \
  --out ARIA_OUT \
  --clock_in ARIA_CLOCK
```

### Manual Mode (Keyboard-Driven)

```bash
python ableton_bridge.py \
  --checkpoint /path/to/model-gen.safetensors \
  --mode manual \
  --in ARIA_IN \
  --out ARIA_OUT
```

### With Max for Live & Feedback Collection

```bash
python ableton_bridge.py \
  --checkpoint /path/to/model-gen.safetensors \
  --mode manual \
  --m4l \
  --feedback \
  --data-dir ./my-feedback-data \
  --in ARIA_IN \
  --out ARIA_OUT
```

## Path Examples

### Windows (Absolute Path)
```bash
python ableton_bridge.py --checkpoint C:/Users/YourName/Downloads/model-gen.safetensors --mode manual --in ARIA_IN --out ARIA_OUT
```

### macOS/Linux (Absolute Path)
```bash
python ableton_bridge.py --checkpoint /home/user/aria-models/model-gen.safetensors --mode manual --in ARIA_IN --out ARIA_OUT
```

### Relative Path (From real-time folder)
```bash
python ableton_bridge.py --checkpoint ../models/model-gen.safetensors --mode manual --in ARIA_IN --out ARIA_OUT
```

## Feedback Data Location

Use `--data-dir` to store recordings anywhere on your system:

```bash
# Local folder in current directory
--data-dir ./feedback_data

# Absolute path to external drive
--data-dir /mnt/backup/aria_feedback

# Network path (Windows)
--data-dir //server/share/aria_output
```

## Next Steps

- See [README.md](../README.md) for full documentation
- Run `python ableton_bridge.py --help` for all options
- Check [Troubleshooting](../README.md#troubleshooting) if issues arise

## Common Issues

**"Checkpoint not found"**: Verify the path is correct and the file exists.

**"MIDI port failed"**: Run `python ableton_bridge.py --list-ports` to see available ports, and ensure they're created in your OS.

**Generation is slow**: Use `--device cuda` if you have an NVIDIA GPU, or reduce `--gen_seconds`.
