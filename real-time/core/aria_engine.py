"""Aria model inference wrapper for real-time generation."""

import logging
import os
import time
import tempfile
from typing import List, Optional, Dict, Any

import torch

logger = logging.getLogger(__name__)


def _progress_tqdm(orig_tqdm, progress_cb):
    """Return a tqdm-compatible factory that also reports progress (0..1) to
    ``progress_cb`` on each step. Taps the sampler's existing terminal bar (the
    same one shown in the console) without modifying the vendored sampler.
    """
    class _Bar:
        def __init__(self, *args, **kwargs):
            self._bar = orig_tqdm(*args, **kwargs)

        def __iter__(self):
            total = getattr(self._bar, "total", None)
            for i, item in enumerate(self._bar):
                if total:
                    try:
                        progress_cb((i + 1) / total)
                    except Exception:
                        pass
                yield item
            try:
                progress_cb(1.0)
            except Exception:
                pass

        def __getattr__(self, name):
            return getattr(self._bar, name)

    return _Bar


def _strip_leading_notes(midi_obj, n_skip):
    """Return a copy of ``midi_obj`` with the first ``n_skip`` sounded notes removed
    and timing rebased so the next note starts at t=0.

    Aria returns ``prompt + continuation``; detokenizing it yields the prompt notes
    followed by the generated ones. Dropping the prompt notes makes playback start
    immediately at what Aria generated instead of replaying the input.
    """
    import mido

    if n_skip <= 0:
        return midi_obj

    merged = mido.merge_tracks(midi_obj.tracks)

    # Absolute-time pass: locate the onset of the first note past the prompt.
    abs_t = 0
    notes_seen = 0
    cutoff = None
    events = []
    for msg in merged:
        abs_t += msg.time
        events.append((abs_t, msg))
        if msg.type == "note_on" and (msg.velocity or 0) > 0:
            notes_seen += 1
            if notes_seen == n_skip + 1 and cutoff is None:
                cutoff = abs_t
    if cutoff is None:
        cutoff = abs_t  # generated <= prompt notes: nothing to keep after the prompt

    # Carry forward tempo/meta/program set before the cutoff; rebase the rest to 0.
    carried = []
    kept = []
    for abs_time, msg in events:
        if msg.type == "end_of_track":
            continue
        if abs_time < cutoff:
            if (msg.is_meta and msg.type in ("set_tempo", "time_signature", "key_signature")) \
                    or msg.type == "program_change":
                carried.append(msg)
            continue
        kept.append((abs_time, msg))

    out = mido.MidiTrack()
    for m in carried:
        out.append(m.copy(time=0))
    prev = cutoff
    for abs_time, msg in kept:
        out.append(msg.copy(time=abs_time - prev))
        prev = abs_time

    new_midi = mido.MidiFile(ticks_per_beat=midi_obj.ticks_per_beat)
    new_midi.tracks.append(out)
    return new_midi


class AriaEngine:
    """
    Wraps Aria generation: loads model once, provides generate() method.
    """

    def __init__(
        self,
        checkpoint_path: str,
        device: str = "cuda",
        config_name: str = "medium",
    ):
        """
        Load the Aria model once at initialization.

        Args:
            checkpoint_path: Path to .safetensors checkpoint
            device: 'cuda', 'mlx' (Apple Silicon), or 'cpu'
            config_name: Model config name (e.g., 'medium', 'large')
        """
        self.checkpoint_path = checkpoint_path
        self.device = device
        self.config_name = config_name
        self.model = None
        self.tokenizer = None
        self.dtype = None

        self._load_model()
        logger.info(
            f"AriaEngine initialized: {config_name} on {device}, "
            f"checkpoint={os.path.basename(checkpoint_path)}"
        )

    def _load_model(self) -> None:
        """Load model and tokenizer from checkpoint."""
        try:
            from aria.model import ModelConfig
            from aria.config import load_model_config
            from ariautils.tokenizer import AbsTokenizer
        except ImportError as e:
            raise ImportError(
                f"Failed to import Aria dependencies: {e}. "
                "Ensure aria is installed and in PYTHONPATH."
            )

        try:
            model_config = ModelConfig(**load_model_config(name=self.config_name))
            model_config.set_vocab_size(AbsTokenizer().vocab_size)
            self.tokenizer = AbsTokenizer()

            if self.device == "mlx":
                import mlx.core as mx
                from aria.inference.model_mlx import TransformerLM
                self.model = TransformerLM(model_config)
                self.model.load_weights(self.checkpoint_path, strict=False)
                mx.eval(self.model.parameters())
                self.dtype = None  # MLX manages its own dtypes
            else:
                from safetensors.torch import load_file
                from aria.inference.model_cuda import TransformerLM
                self.dtype = (
                    torch.bfloat16
                    if torch.cuda.is_available() and torch.cuda.is_bf16_supported()
                    else torch.float32
                )
                self.model = TransformerLM(model_config)
                state_dict = load_file(filename=self.checkpoint_path)
                self.model.load_state_dict(state_dict=state_dict, strict=False)
                self.model = self.model.to(self.device)
                self.model.eval()

            logger.debug(
                f"Model loaded: {self.model.__class__.__name__}, "
                f"device={self.device}, vocab_size={model_config.vocab_size}"
            )
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise

    def _sample_batch_mlx(self, prompt, max_new_tokens, temp, top_p, min_p, progress_cb=None):
        """MLX sampling driven from the bridge (O(n) KV-cache decode).

        Mirrors aria's ``sample_batch`` but calls the model directly so we can pass
        ``max_kv_pos`` (the vendored ``prefill``/``decode_one`` omit it, which crashes
        on ``max_kv_pos + 1``). Prefill the prompt once, then decode one token at a
        time against the cache. Correct + fast now that the RoPE layout bug in
        ``model_mlx.apply_rotary_emb_mlx`` is fixed. Reuses aria's pure samplers.
        """
        import mlx.core as mx
        from tqdm import tqdm
        from aria.inference.sample_mlx import (
            sample_top_p_mlx,
            sample_min_p_mlx,
            update_seq_ids_,
        )

        tok = self.tokenizer
        model = self.model
        model.eval()
        prompt_len = len(prompt)
        total_len = prompt_len + max_new_tokens
        seq = mx.stack(
            [
                mx.array(
                    tok.encode(prompt + [tok.pad_tok] * (total_len - prompt_len)),
                    dtype=mx.int32,
                )
            ]
        )
        model.setup_cache(batch_size=1, max_seq_len=total_len, dtype=mx.float32)
        dim_tok_inserted = [False]
        eos_tok_seen = [False]
        # tqdm bar = the terminal "generating" progress slider.
        for idx in tqdm(
            range(prompt_len, total_len),
            total=total_len - prompt_len,
            desc="Generating",
            leave=False,
        ):
            if idx == prompt_len:
                # Prefill the whole prompt; populates the KV cache for 0..idx-1.
                logits = model(
                    idxs=seq[:, :idx],
                    input_pos=mx.arange(0, idx, dtype=mx.int32),
                    offset=0,
                    max_kv_pos=idx - 1,
                )[:, -1]
            else:
                # Single-token decode against the cache (offset = current position).
                logits = model(
                    idxs=seq[:, idx - 1 : idx],
                    input_pos=mx.array([idx - 1], dtype=mx.int32),
                    offset=idx - 1,
                    max_kv_pos=idx - 1,
                )[:, -1]

            if temp > 0.0:
                probs = mx.softmax(logits / temp, axis=-1)
                if min_p is not None:
                    next_token_ids = sample_min_p_mlx(probs, min_p).flatten()
                else:
                    next_token_ids = sample_top_p_mlx(probs, top_p).flatten()
            else:
                next_token_ids = mx.argmax(logits, axis=-1).flatten()

            update_seq_ids_(
                seq=seq,
                idx=idx,
                next_token_ids=next_token_ids,
                dim_tok_inserted=dim_tok_inserted,
                eos_tok_seen=eos_tok_seen,
                max_len=total_len,
                force_end=False,
                tokenizer=tok,
            )
            # Emit generation progress (0..1) every few tokens for a M4L slider.
            if progress_cb is not None:
                done = idx - prompt_len + 1
                if done % 8 == 0 or done == max_new_tokens:
                    try:
                        progress_cb(done / max_new_tokens)
                    except Exception:
                        pass

            if all(eos_tok_seen):
                break

        if progress_cb is not None:
            try:
                progress_cb(1.0)
            except Exception:
                pass

        decoded = tok.decode(seq[0].tolist())
        if tok.eos_tok in decoded:
            decoded = decoded[: decoded.index(tok.eos_tok) + 1]
        return [decoded]

    def generate(
        self,
        prompt_midi_path: str,
        prompt_duration_s: int = 4,
        horizon_s: float = 0.6,
        temperature: float = 0.8,
        top_p: Optional[float] = 0.9,
        min_p: Optional[float] = None,
        max_new_tokens: Optional[int] = None,
        progress_cb=None,
        seed: Optional[int] = None,
    ) -> str:
        """
        Generate continuation from a prompt MIDI file.

        Args:
            prompt_midi_path: Path to .mid file
            prompt_duration_s: How many seconds of prompt to use
            horizon_s: How many seconds to generate (~0.6s for MVP)
            temperature: Sampling temperature (0.8 default = conservative)
            top_p: Top-p sampling (0.9 default = conservative)
            min_p: Min-p sampling (alternative to top_p)
            max_new_tokens: Max tokens to generate (auto-set if None)

        Returns:
            Path to the generated MIDI file (temporary file, caller must clean up).
        """
        try:
            from aria.inference import get_inference_prompt
            from ariautils.midi import MidiDict

            # Seed the RNG so a 5-per-prompt variant is reproducible and its seed can be recorded.
            # With no seed, sequential calls still differ (RNG advances) — this only pins it.
            if seed is not None:
                torch.manual_seed(seed)
                if torch.cuda.is_available():
                    torch.cuda.manual_seed_all(seed)
                try:
                    import mlx.core as _mx
                    _mx.random.seed(seed)
                except Exception:
                    pass

            # Get and tokenize prompt
            midi_dict = MidiDict.from_midi(prompt_midi_path)
            prompt = get_inference_prompt(
                midi_dict=midi_dict,
                tokenizer=self.tokenizer,
                prompt_len_ms=int(1e3 * prompt_duration_s),
            )
            # get_inference_prompt trims note_msgs in place to the prompt notes;
            # the model returns prompt + continuation, so we drop this many leading
            # notes from the output to play only what Aria generated.
            n_prompt_notes = len(midi_dict.note_msgs)

            if max_new_tokens is None:
                max_new_tokens = min(512, int(horizon_s * 200))

            max_new_tokens = min(8096 - len(prompt), max_new_tokens)

            if max_new_tokens <= 0:
                logger.warning("Prompt too long, no room to generate.")
                return None

            logger.debug(
                f"Generating: prompt_len={len(prompt)}, "
                f"max_new_tokens={max_new_tokens}, temp={temperature}, top_p={top_p}"
            )

            # Sample — route to the correct backend
            start_time = time.time()
            if self.device == "mlx":
                results = self._sample_batch_mlx(
                    prompt=prompt,
                    max_new_tokens=max_new_tokens,
                    temp=temperature,
                    top_p=top_p,
                    min_p=min_p,
                    progress_cb=progress_cb,
                )
            else:
                import aria.inference.sample_cuda as _sc
                # Tap the sampler's tqdm bar to emit progress to the M4L slider —
                # without modifying the vendored sampler (restored in finally).
                _orig_tqdm = _sc.tqdm
                if progress_cb is not None:
                    _sc.tqdm = _progress_tqdm(_orig_tqdm, progress_cb)
                try:
                    with torch.inference_mode():
                        results = _sc.sample_batch(
                            model=self.model,
                            tokenizer=self.tokenizer,
                            prompt=prompt,
                            num_variations=1,
                            max_new_tokens=max_new_tokens,
                            temp=temperature,
                            force_end=False,
                            top_p=top_p,
                            min_p=min_p,
                            compile=False,
                        )
                finally:
                    _sc.tqdm = _orig_tqdm

            gen_time = time.time() - start_time
            logger.debug(f"Generation took {gen_time:.2f}s, produced {len(results[0])} tokens")

            # Detokenize to MIDI dict and save to temp file
            if results:
                tokenized_seq = results[0]
                gen_midi_dict = self.tokenizer.detokenize(tokenized_seq)
                midi_obj = gen_midi_dict.to_midi()

                # Drop the prompt so playback starts at the generated continuation,
                # not a replay of the input.
                midi_obj = _strip_leading_notes(midi_obj, n_prompt_notes)

                # Save to temp file
                tmp = tempfile.NamedTemporaryFile(suffix='.mid', delete=False)
                tmp.close()
                midi_obj.save(tmp.name)

                logger.debug(
                    f"Generated MIDI saved to {tmp.name} "
                    f"({n_prompt_notes} prompt notes stripped)"
                )
                return tmp.name
            else:
                return None

        except Exception as e:
            logger.exception(f"Generation error: {e}")
            return None

    def _midi_to_events(self, midi_obj) -> List[Dict[str, Any]]:
        """
        Convert mido.MidiFile to event list with relative times.

        Returns a list of dicts:
        [
            {'type': 'note_on', 'note': p, 'velocity': v, 'time': t_sec},
            {'type': 'note_off', 'note': p, 'time': t_sec},
            ...
        ]
        """
        events = []
        current_time = 0.0  # In seconds

        # Standard: 120 BPM = 500ms per quarter note
        # Use MIDI file ticks_per_beat if available
        ticks_per_beat = midi_obj.ticks_per_beat or 480
        ms_per_beat = 500  # 120 BPM
        ms_per_tick = ms_per_beat / ticks_per_beat

        for track in midi_obj.tracks:
            current_time = 0.0
            for msg in track:
                # Accumulate time
                current_time += msg.time * ms_per_tick / 1000.0  # Convert to seconds

                if msg.type == 'note_on':
                    events.append({
                        'type': 'note_on',
                        'note': msg.note,
                        'velocity': msg.velocity,
                        'time': current_time,
                    })
                elif msg.type == 'note_off':
                    events.append({
                        'type': 'note_off',
                        'note': msg.note,
                        'time': current_time,
                    })
                elif msg.type == 'control_change' and msg.control == 64:
                    # Sustain pedal
                    events.append({
                        'type': 'control_change',
                        'control': 64,
                        'value': msg.value,
                        'time': current_time,
                    })

        return events
