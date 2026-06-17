import argparse
import itertools
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Sequence


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run a grid search over sampling parameters and save generated MIDIs."
    )
    p.add_argument(
        "--prompt",
        help="Path to a single 4-bar prompt MIDI file.",
    )
    p.add_argument(
        "--prompt_dir",
        help="Directory of MIDI files; all *.mid|*.midi inside will be processed.",
    )
    p.add_argument("--checkpoint", required=True, help="Path to model checkpoint.")
    p.add_argument(
        "--temps",
        nargs="+",
        type=float,
        default=[0.6, 0.8, 1.0, 1.2],
        help="Temperatures to try (space-separated).",
    )
    p.add_argument(
        "--top_ps",
        nargs="+",
        type=float,
        default=[0.9, 0.95, 0.98],
        help="Top-p values to try (space-separated).",
    )
    p.add_argument(
        "--min_ps",
        nargs="+",
        type=float,
        default=[0.0, 0.02, 0.04, 0.06],
        help="Min-p values to try (space-separated, 0 disables min-p).",
    )
    p.add_argument(
        "--prompt_duration",
        type=int,
        default=8,
        help="Seconds of the prompt to use (match your 4-bar duration). Must be int for aria CLI.",
    )
    p.add_argument(
        "--length",
        type=int,
        default=2048,
        help="Max new tokens to generate.",
    )
    p.add_argument(
        "--variations",
        type=int,
        default=1,
        help="Number of samples per combo.",
    )
    p.add_argument(
        "--device",
        choices=["torch_cuda", "torch_cpu"],
        default="torch_cuda",
        help="Backend for aria.generate.",
    )
    p.add_argument(
        "--out_root",
        type=str,
        default=None,
        help="Root folder for outputs. Default: <prompt_dir>/grid_outputs",
    )
    p.add_argument(
        "--skip_existing",
        action="store_true",
        default=True,
        help="Skip a combo if its output folder already exists and is non-empty (default: on).",
    )
    return p.parse_args()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def combo_name(temp: float, top_p: float, min_p: float) -> str:
    def fmt(x: float) -> str:
        return f"{x:.3g}".replace(".", "p")

    return f"t{fmt(temp)}_tp{fmt(top_p)}_mp{fmt(min_p)}"


def run_combo(
    prompt: Path,
    checkpoint: Path,
    out_dir: Path,
    temp: float,
    top_p: float,
    min_p: float,
    prompt_duration: float,
    length: int,
    variations: int,
    device: str,
) -> None:
    ensure_dir(out_dir)
    cmd: List[str] = [
        sys.executable,
        "-m",
        "aria.run",
        "generate",
        "--backend",
        device,
        "--checkpoint_path",
        str(checkpoint),
        "--prompt_midi_path",
        str(prompt),
        "--prompt_duration",
        str(prompt_duration),
        "--variations",
        str(variations),
        "--temp",
        str(temp),
        "--top_p",
        str(top_p),
        "--length",
        str(length),
        "--save_dir",
        str(out_dir),
    ]
    cmd.extend(["--min_p", str(min_p)])

    print(f"[run] {out_dir.name} -> aria.generate")
    subprocess.run(cmd, check=True)


def main() -> None:
    args = parse_args()

    # Collect prompts
    prompts: List[Path] = []
    if args.prompt:
        prompts.append(Path(args.prompt).expanduser().resolve())
    if args.prompt_dir:
        prompt_dir = Path(args.prompt_dir).expanduser().resolve()
        for ext in ("*.mid", "*.midi"):
            prompts.extend(prompt_dir.glob(ext))

    if not prompts:
        sys.exit("Please provide --prompt or --prompt_dir with MIDI files.")

    # Deduplicate while preserving order
    seen = set()
    deduped: List[Path] = []
    for pth in prompts:
        if pth not in seen:
            seen.add(pth)
            deduped.append(pth)
    prompts = deduped
    checkpoint = Path(args.checkpoint).expanduser().resolve()

    combos: Iterable[tuple[float, float, float]] = itertools.product(
        args.temps, args.top_ps, args.min_ps
    )
    combos = list(combos)
    print(f"[info] total combinations per prompt: {len(combos)}")
    print(f"[info] total prompts: {len(prompts)}")

    for prompt in prompts:
        prompt = prompt.expanduser().resolve()
        if args.out_root:
            out_root = Path(args.out_root).expanduser().resolve()
        else:
            out_root = prompt.parent / "grid_outputs"

        prompt_bucket = out_root / prompt.stem
        ensure_dir(prompt_bucket)

        for temp, top_p, min_p in combos:
            name = combo_name(temp, top_p, min_p)
            out_dir = prompt_bucket / name

            if args.skip_existing and out_dir.exists() and any(out_dir.iterdir()):
                print(f"[skip] {out_dir} (already has contents)")
                continue

            run_combo(
                prompt=prompt,
                checkpoint=checkpoint,
                out_dir=out_dir,
                temp=temp,
                top_p=top_p,
                min_p=min_p,
                prompt_duration=args.prompt_duration,
                length=args.length,
                variations=args.variations,
                device=args.device,
            )


if __name__ == "__main__":
    main()
