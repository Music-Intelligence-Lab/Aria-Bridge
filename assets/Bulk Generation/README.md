# Bulk MIDI Generation Grid

Utility script (`generator.py`) to sweep sampling parameters over one or many prompt MIDIs and save outputs in organized folders.

## Run (single prompt)
```powershell
python "assets/Bulk Generation/generator.py" `
  --prompt "C:\path\to\prompt.mid" `
  --checkpoint "C:\path\to\model-gen.safetensors"
```

## Run (folder of prompts)
```powershell
python "assets/Bulk Generation/generator.py" `
  --prompt_dir "C:\path\to\midi_folder" `
  --checkpoint "C:\path\to\model-gen.safetensors"
```

Outputs: `<prompt_dir>/grid_outputs/<prompt_stem>/<combo>/...` (or under `--out_root`). Existing non-empty combo folders are skipped by default.

Key options:
- `--temps`, `--top_ps`, `--min_ps`: parameter grids (space-separated lists; defaults provided).
- `--prompt_duration` (int seconds), `--length` (max tokens), `--variations` (per combo).
- `--device` (`torch_cuda`/`torch_cpu`); use CPU if CUDA is unstable.
- `--skip_existing` (default on) to avoid reruns.
