"""Build a fine-tuning dataset from graded feedback episodes.

Each feedback episode stores `output.mid`, which already contains the tokenized prompt +
the model's continuation as one continuous performance (verified: output.mid begins with the
prompt notes). So for a high-graded take, `output.mid` *is* the training example — no
concatenation needed.

This tool scans the feedback datastore, keeps the episodes the musician liked
(status == "final" and grade >= --min-grade), and copies their `output.mid` into an output
folder ready for the existing aria dataset pipeline. See MODEL_TRAINING.md.

Usage:
    python scripts/build_finetune_dataset.py --feedback-dir feedback --out data/finetune/midi --min-grade 4

Then (as printed at the end):
    aria midi-dataset data/finetune/midi data/finetune/midi.jsonl --recursive --shuffle --split 0.9
    aria pretrain-dataset --load_path data/finetune/midi_train.jsonl --save_dir data/finetune/train --tokenizer_name abs --seq_len 8192
    aria pretrain-dataset --load_path data/finetune/midi_val.jsonl   --save_dir data/finetune/val   --tokenizer_name abs --seq_len 8192
"""

import argparse
import json
import shutil
from pathlib import Path


def iter_episodes(feedback_dir: Path):
    """Yield (episode_id, grade, status, output_mid_path) for every episode on disk."""
    episodes_dir = feedback_dir / "episodes"
    if not episodes_dir.is_dir():
        return
    for meta_path in sorted(episodes_dir.rglob("meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        yield (
            meta.get("episode_id"),
            meta.get("grade"),
            meta.get("status"),
            meta_path.parent / "output.mid",
        )


def collect(feedback_dir: Path, out_dir: Path, min_grade: int, allow_drafts: bool):
    out_dir.mkdir(parents=True, exist_ok=True)
    kept, skipped = 0, 0
    for episode_id, grade, status, output_mid in iter_episodes(feedback_dir):
        status_ok = (status == "final") or (allow_drafts and status == "draft")
        try:
            grade_ok = grade is not None and int(grade) >= min_grade
        except (TypeError, ValueError):
            grade_ok = False

        if status_ok and grade_ok and output_mid.exists():
            shutil.copy2(output_mid, out_dir / f"{episode_id}.mid")
            kept += 1
        else:
            skipped += 1
    return kept, skipped


def main():
    ap = argparse.ArgumentParser(
        description="Collect high-graded feedback outputs into a fine-tune MIDI folder."
    )
    ap.add_argument(
        "--feedback-dir",
        default="feedback",
        help="datastore base dir containing episodes/ and index.csv (default: feedback)",
    )
    ap.add_argument(
        "--out",
        default="data/finetune/midi",
        help="folder to collect the selected MIDI into (default: data/finetune/midi)",
    )
    ap.add_argument(
        "--min-grade",
        type=int,
        default=4,
        help="keep episodes with grade >= this, on a 1-5 scale (default: 4)",
    )
    ap.add_argument(
        "--allow-drafts",
        action="store_true",
        help="also include graded-but-uncommitted (draft) episodes (default: final only)",
    )
    args = ap.parse_args()

    feedback_dir = Path(args.feedback_dir)
    out_dir = Path(args.out)

    kept, skipped = collect(feedback_dir, out_dir, args.min_grade, args.allow_drafts)

    print(
        f"Kept {kept} episode(s) (grade >= {args.min_grade}"
        f"{', incl. drafts' if args.allow_drafts else ', final only'}), skipped {skipped}."
    )
    print(f"MIDI collected in: {out_dir.resolve()}")

    if kept == 0:
        print(
            f"\nNo qualifying episodes yet. Grade some takes >= {args.min_grade} and commit them, "
            "then run this again."
        )
        return

    stem = out_dir.name
    parent = out_dir.parent
    print("\nNext steps (existing aria pipeline — see MODEL_TRAINING.md):")
    print(f"  aria midi-dataset {out_dir} {parent / (stem + '.jsonl')} --recursive --shuffle --split 0.9")
    print(
        f"  aria pretrain-dataset --load_path {parent / (stem + '_train.jsonl')} "
        f"--save_dir {parent / 'train'} --tokenizer_name abs --seq_len 8192"
    )
    print(
        f"  aria pretrain-dataset --load_path {parent / (stem + '_val.jsonl')} "
        f"--save_dir {parent / 'val'} --tokenizer_name abs --seq_len 8192"
    )


if __name__ == "__main__":
    main()
