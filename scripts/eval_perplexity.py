"""Objective fine-tune metric: held-out perplexity (mean next-token NLL) of a checkpoint.

Perplexity = exp(mean cross-entropy per predicted token) on a tokenized dataset the model
never trained on. Lower = the model predicts that style better. This is the exact loss the
training loop reports (`aria/training/train.py::val_loop`): masked next-token CrossEntropy with
`ignore_index=pad_id` — just measured over a fixed held-out set and token-weighted so the number
is a true perplexity.

Use it to prove a fine-tune worked: evaluate the SAME held-out set with the base and the
fine-tuned checkpoint. Perplexity should drop on the target style. Run it on an in-distribution
(classical) set too as a forgetting control — that number should stay roughly flat.

Build the tokenized dataset with the existing pipeline (see MODEL_TRAINING.md):
    aria pretrain-dataset --load_path <midi>.jsonl --save_dir data/finetune/val \
        --tokenizer_name abs --seq_len 8192

Then:
    python scripts/eval_perplexity.py --data data/finetune/val \
        --checkpoint models/model-gen.safetensors \
        --checkpoint experiments/0/finetuned.safetensors
"""

import argparse
import math
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader

from aria.config import load_model_config
from aria.model import ModelConfig, TransformerLM
from aria.utils import _load_weight
from aria.datasets import PretrainingDataset
from ariautils.tokenizer import AbsTokenizer


def load_model(checkpoint_path: str, model_name: str, tokenizer, device: str) -> TransformerLM:
    """Build the model at `model_name` config and load checkpoint weights (mirrors train.py)."""
    model_config = ModelConfig(**load_model_config(model_name))
    model_config.set_vocab_size(tokenizer.vocab_size)
    model = TransformerLM(model_config)
    try:
        model.load_state_dict(_load_weight(checkpoint_path))
    except RuntimeError as e:
        print(f"  strict load failed ({e}); retrying strict=False")
        model.load_state_dict(_load_weight(checkpoint_path), strict=False)
    return model.eval().to(device)


@torch.no_grad()
def perplexity(model: TransformerLM, loader: DataLoader, pad_id: int, device: str) -> tuple:
    """Return (mean_nll_nats, perplexity, n_tokens) over the loader, token-weighted."""
    loss_fn = nn.CrossEntropyLoss(ignore_index=pad_id, reduction="none")
    total_nll, total_tok = 0.0, 0
    for batch in loader:
        src, tgt, mask, _emb = batch
        src, tgt, mask = src.to(device), tgt.to(device), mask.to(device).float()
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=(device == "cuda")):
            logits = model(src).transpose(1, 2)  # (b, vocab, seq) for CrossEntropyLoss
        per_token = loss_fn(logits.float(), tgt) * mask  # (b, seq), 0 where masked/pad
        total_nll += per_token.sum().item()
        total_tok += int(mask.sum().item())
    if total_tok == 0:
        raise SystemExit("No scored tokens — is --data a tokenized pretrain-dataset dir?")
    mean_nll = total_nll / total_tok
    return mean_nll, math.exp(mean_nll), total_tok


def main():
    ap = argparse.ArgumentParser(description="Held-out perplexity of Aria checkpoint(s).")
    ap.add_argument("--data", required=True,
                    help="tokenized PretrainingDataset dir (from `aria pretrain-dataset --save_dir`)")
    ap.add_argument("--checkpoint", action="append", required=True,
                    help="path to a .safetensors checkpoint; repeat to compare base vs fine-tuned")
    ap.add_argument("--model", default="medium", help="model config name (default: medium)")
    ap.add_argument("--bs", type=int, default=1, help="batch size (default: 1)")
    ap.add_argument("--device", default=None, help="cuda|cpu (default: auto)")
    args = ap.parse_args()

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = AbsTokenizer()

    dataset = PretrainingDataset(dir_paths=args.data, tokenizer=tokenizer)
    loader = DataLoader(dataset, batch_size=args.bs, num_workers=0, shuffle=False)
    print(f"Data: {args.data}  |  device: {device}  |  model: {args.model}\n")

    results = []
    for cp in args.checkpoint:
        print(f"Evaluating {cp} ...")
        model = load_model(cp, args.model, tokenizer, device)
        nll, ppl, ntok = perplexity(model, loader, tokenizer.pad_id, device)
        results.append((cp, nll, ppl, ntok))
        del model
        if device == "cuda":
            torch.cuda.empty_cache()

    print(f"\n{'checkpoint':<48} {'NLL (nats)':>12} {'perplexity':>12} {'tokens':>10}")
    print("-" * 84)
    for cp, nll, ppl, ntok in results:
        print(f"{Path(cp).name:<48} {nll:>12.4f} {ppl:>12.3f} {ntok:>10,}")

    if len(results) >= 2:
        base, *_ = results[0]
        last = results[-1]
        drop = (results[0][2] - last[2]) / results[0][2] * 100
        verdict = "better ✓" if drop > 0 else "worse ✗"
        print(f"\n{Path(last[0]).name} vs {Path(base).name}: "
              f"perplexity {drop:+.1f}%  ({verdict} on this set)")


if __name__ == "__main__":
    main()
