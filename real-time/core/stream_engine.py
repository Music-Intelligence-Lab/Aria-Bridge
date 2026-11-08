"""Token-level streaming engine for continuous Aria generation (MLX).

One note history; the model's KV cache always holds a window of it:
    header (prefix, <S>)  +  tokens for every note with onset >= w_seg0 * 5 s
Aria's tokenizer stores onsets relative to 5 s segments separated by <T>, so a window
that starts on a segment boundary is a valid piece starting at 0.

Operations
- step():     sample one token and advance. Completed notes come back as Note(src="aria").
- insert():   merge outside notes (live playing, or a clip to layer over) at their real
              onsets. Aria notes after `cut_ms` (not yet heard) are dropped; the cache is
              rolled back to the change point and only the new suffix is prefilled, so this
              costs a few dozen tokens, not a full rebuild.
- rebuild():  slide the window forward (re-prefill) when the cache is full.

Never ends: <E>, <D>, <S>, <P>, prefix and non-piano tokens are masked. An entropy guard
raises the temperature while the model's rolling predictive entropy sits below a floor,
because a self-fed model drifts into loops (see 01-tests/aria-sliding-window).
"""

from __future__ import annotations

import bisect
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

SEG_MS = 5000
CACHE_LIMIT_BYTES = 512 * 1024 * 1024


@dataclass(eq=False)
class Note:
    on: int          # onset, ms from stream start
    pitch: int
    vel: int
    dur: int         # ms
    src: str = "aria"  # "aria" | "human" | "seed" | "layer"
    played: bool = field(default=False, compare=False)

    @property
    def off(self) -> int:
        return self.on + self.dur


class StreamEngine:
    def __init__(self, model, tokenizer, window: int = 3072, entropy_floor: float = 0.8,
                 max_temp_boost: float = 0.5, rng_seed: Optional[int] = None):
        import mlx.core as mx

        self.mx = mx
        # MLX keeps freed buffers for reuse, and each re-prefill leaves about 1.3 GB behind.
        # Uncapped, a long stream grows until macOS kills the process.
        mx.set_cache_limit(CACHE_LIMIT_BYTES)
        self.model = model
        self.tok = tokenizer
        self.window = int(window)
        self.cap = self.window + 256  # cache length; rebuild before reaching it
        self.entropy_floor = entropy_floor
        self.max_temp_boost = max_temp_boost
        self.rng = np.random.default_rng(rng_seed)

        self.header = [("prefix", "instrument", "piano"), tokenizer.bos_tok]
        self.header_ids = tokenizer.encode(self.header)
        self.ban = self._banned_ids()
        self.eos_id = tokenizer.tok_to_id[tokenizer.eos_tok]
        self.vq = np.array(tokenizer.velocity_quantizations)

        self.events: List[Note] = []   # sorted by onset
        self.w_seg0 = 0                # absolute segment where the window starts
        self.toks: list = []           # window body tokens
        self.tok_ev: List[Optional[Note]] = []
        self.seg = 0                   # <T> count inside the window
        self._partial: list = []       # [pitch, vel, onset] of the note being generated
        self.logits = None
        self.entropy = deque(maxlen=128)
        self.temp_boost = 0.0
        self.n_tokens = 0
        self.n_rebuilds = 0

    # ---------- setup ----------
    def _banned_ids(self):
        tok = self.tok
        ban = []
        for i in range(tok.vocab_size):
            t = tok.id_to_tok[i]
            if isinstance(t, str):
                if t in (tok.eos_tok, tok.dim_tok, tok.bos_tok, tok.pad_tok, "<U>"):
                    ban.append(i)
            elif t[0] == "prefix" or t[0] not in ("piano", "onset", "dur"):
                ban.append(i)
        return np.array(ban, dtype=np.int64)

    def reset(self, seed_notes: Iterable[Note] = ()):
        self.events = sorted(seed_notes, key=lambda n: n.on)
        self.w_seg0 = 0
        self.temp_boost = 0.0
        self.entropy.clear()
        self.rebuild()  # newest notes that fit the window (a long history must not overflow)

    # ---------- tokenization ----------
    def _vel(self, v):
        v = int(self.vq[np.abs(self.vq - v).argmin()])
        return max(v, int(self.vq[1]))

    def _note_tokens(self, n: Note, seg: int) -> list:
        onset = min(max(0, (n.on - (self.w_seg0 + seg) * SEG_MS) // 10 * 10), SEG_MS - 10)
        dur = self.tok._quantize_dur(max(10, min(int(n.dur), 5000)))
        return [("piano", int(n.pitch), self._vel(n.vel)), ("onset", int(onset)), ("dur", int(dur))]

    def _tokenize(self, notes: List[Note], seg: int) -> Tuple[list, list, int]:
        toks, evs = [], []
        for n in notes:
            target = n.on // SEG_MS - self.w_seg0
            while seg < target:
                toks.append(self.tok.time_tok)
                evs.append(None)
                seg += 1
            nt = self._note_tokens(n, seg)
            if all(t in self.tok.tok_to_id for t in nt):
                toks.extend(nt)
                evs.extend([n, n, n])
        return toks, evs, seg

    # ---------- model calls ----------
    def _forward(self, ids: list, pos: int):
        mx = self.mx
        out = self.model(
            idxs=mx.array([ids], dtype=mx.int32),
            input_pos=mx.arange(pos, pos + len(ids), dtype=mx.int32),
            offset=pos,
            max_kv_pos=pos + len(ids) - 1,
        )[:, -1]
        mx.eval(out)
        self.logits = out
        return out

    @property
    def pos(self) -> int:
        return len(self.header_ids) + len(self.toks)

    def rebuild(self, start_seg: Optional[int] = None):
        """Re-window: keep the newest notes that fit in ~75% of the window, re-prefill."""
        if start_seg is None:
            budget = int(self.window * 0.75) - len(self.header_ids)
            last_seg = (self.events[-1].on // SEG_MS) if self.events else 0
            start_seg = last_seg
            # walk back segment by segment while the token count fits
            counts = {}
            for n in self.events:
                counts[n.on // SEG_MS] = counts.get(n.on // SEG_MS, 0) + 3
            used = 0
            s = last_seg
            while s >= 0:
                cost = counts.get(s, 0) + 1
                if used + cost > budget:
                    break
                used += cost
                start_seg = s
                s -= 1
        self.w_seg0 = max(0, start_seg)
        lo = self.w_seg0 * SEG_MS
        notes = [n for n in self.events if n.on >= lo]
        self.toks, self.tok_ev, self.seg = self._tokenize(notes, 0)
        self._partial = []
        self.model.setup_cache(batch_size=1, max_seq_len=self.cap, dtype=self.mx.float32)
        self._forward(self.header_ids + self.tok.encode(self.toks), 0)
        self.mx.clear_cache()
        self.n_rebuilds += 1

    # ---------- sampling ----------
    def _sample(self, temp: float, top_p: float, min_p: float) -> int:
        lg = np.array(self.logits[0].astype(self.mx.float32), dtype=np.float64)
        p1 = np.exp(lg - lg.max())
        p1 /= p1.sum()
        self.entropy.append(float(-(p1 * np.log(p1 + 1e-30)).sum()))
        if len(self.entropy) >= 64:
            if np.mean(self.entropy) < self.entropy_floor:
                self.temp_boost = min(self.max_temp_boost, self.temp_boost + 0.004)
            else:
                self.temp_boost = max(0.0, self.temp_boost - 0.004)
        lg[self.ban] = -np.inf
        z = lg / max(0.05, temp + self.temp_boost)
        z -= z.max()
        p = np.exp(z)
        p /= p.sum()
        if min_p and min_p > 0:
            keep = p >= min_p * p.max()
        else:
            order = np.argsort(-p)
            cum = np.cumsum(p[order])
            keep = np.zeros_like(p, dtype=bool)
            keep[order[(cum - p[order]) < top_p]] = True
        p = np.where(keep, p, 0.0)
        p /= p.sum()
        return int(self.rng.choice(len(p), p=p))

    def step(self, temp=0.95, top_p=0.95, min_p=0.0) -> Optional[Note]:
        """Generate one token. Returns a Note when one is completed."""
        if self.pos >= self.cap - 1:
            self.rebuild()
        tid = self._sample(temp, top_p, min_p)
        t = self.tok.id_to_tok[tid]
        note = None
        if t == self.tok.time_tok:
            self.seg += 1
            self._partial = []
            ev = None
        elif t[0] == "piano":
            self._partial = [t[1], t[2], None]
            ev = None
        elif t[0] == "onset":
            if self._partial:
                self._partial[2] = t[1]
            ev = None
        else:  # dur
            ev = None
            if len(self._partial) == 3 and self._partial[2] is not None:
                on = (self.w_seg0 + self.seg) * SEG_MS + self._partial[2]
                note = Note(on=on, pitch=self._partial[0], vel=self._partial[1], dur=t[1], src="aria")
                self._add_event(note)
                ev = note
            self._partial = []
        self.toks.append(t)
        self.tok_ev.append(ev)
        if ev is not None and len(self.tok_ev) >= 3:  # tag the whole triple with the note
            self.tok_ev[-3:] = [ev, ev, ev]
        self._forward([tid], self.pos - 1)
        self.n_tokens += 1
        return note

    def _add_event(self, n: Note):
        keys = [e.on for e in self.events]
        self.events.insert(bisect.bisect_right(keys, n.on), n)

    # ---------- merging outside notes ----------
    def insert(self, notes: List[Note], cut_ms: int) -> List[Note]:
        """Merge `notes` (e.g. live playing) and drop unheard Aria notes after cut_ms.

        Returns the dropped Aria notes so the player can unschedule them.
        """
        if not notes:
            return []
        dropped = [e for e in self.events if e.src == "aria" and e.on > cut_ms and not e.played]
        if dropped:
            gone = set(map(id, dropped))
            self.events = [e for e in self.events if id(e) not in gone]
        for n in sorted(notes, key=lambda x: x.on):
            self._add_event(n)
        change = min([n.on for n in notes] + [d.on for d in dropped])
        if change < self.w_seg0 * SEG_MS:
            self.rebuild()
            return dropped

        # roll back to just after the last window note that starts before the change point
        new = set(map(id, notes))
        j = 0
        for i, ev in enumerate(self.tok_ev):
            if ev is not None and ev.on <= change and id(ev) not in new:
                j = i + 1
        while 0 < j < len(self.tok_ev) and self.tok_ev[j] is not None and self.tok_ev[j] is self.tok_ev[j - 1]:
            j += 1  # never cut inside a triple
        seg_j = sum(1 for t in self.toks[:j] if t == self.tok.time_tok)
        kept = set(map(id, (e for e in self.tok_ev[:j] if e is not None)))
        lo = self.w_seg0 * SEG_MS
        suffix = [e for e in self.events if id(e) not in kept and e.on >= lo]
        new_toks, new_ev, seg_end = self._tokenize(suffix, seg_j)
        self.toks = self.toks[:j] + new_toks
        self.tok_ev = self.tok_ev[:j] + new_ev
        self.seg = seg_end
        self._partial = []
        if self.pos >= self.cap - 64:
            self.rebuild()
        elif new_toks:
            self._forward(self.tok.encode(new_toks), len(self.header_ids) + j)
        else:
            last = self.toks[j - 1] if j > 0 else self.header[-1]
            self._forward(self.tok.encode([last]), len(self.header_ids) + j - 1)
        return dropped

    # ---------- info ----------
    def stats(self) -> dict:
        return {"ctx": self.pos, "window_start_s": self.w_seg0 * SEG_MS / 1000,
                "entropy": float(np.mean(self.entropy)) if self.entropy else None,
                "temp_boost": round(self.temp_boost, 3), "tokens": self.n_tokens,
                "rebuilds": self.n_rebuilds, "events": len(self.events),
                "mem_gb": round((self.mx.get_active_memory() + self.mx.get_cache_memory()) / 1e9, 1)}
