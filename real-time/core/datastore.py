import csv
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from uuid import uuid4


class DataStore:
    INDEX_HEADER = [
        "episode_id",
        "timestamp_local",
        "status",
        "grade",
        "coherence",
        "repetition",
        "taste",
        "continuity",
        "temperature",
        "top_p",
        "min_p",
        "max_tokens",
        "seed",
        "mode",
    ]

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "index.csv"
        self.episodes_dir = self.base_dir / "episodes"
        self.episodes_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_index()

    def _ensure_index(self):
        if not self.index_path.exists():
            self.index_path.write_text(",".join(self.INDEX_HEADER) + os.linesep, encoding="utf-8")
            return

        with self.index_path.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))

        if not rows:
            self.index_path.write_text(",".join(self.INDEX_HEADER) + os.linesep, encoding="utf-8")
            return

        header = rows[0]
        if header == self.INDEX_HEADER:
            return

        normalized_rows = [self.INDEX_HEADER]
        for row in rows[1:]:
            if not any(row):
                continue
            normalized_rows.append(self._normalize_index_row(row, header))

        with tempfile.NamedTemporaryFile("w", delete=False, dir=self.index_path.parent, newline="", encoding="utf-8") as tmp:
            writer = csv.writer(tmp)
            writer.writerows(normalized_rows)
            tmp_path = Path(tmp.name)
        tmp_path.replace(self.index_path)

    def _normalize_index_row(self, row, header):
        normalized = {key: "" for key in self.INDEX_HEADER}

        if header:
            for idx, key in enumerate(header):
                if key in normalized and idx < len(row):
                    normalized[key] = row[idx]

        # Migrate older 10-column layout that predates feedback columns.
        if len(header) == 10 and header[:4] == ["episode_id", "timestamp_local", "status", "grade"]:
            legacy_keys = [
                "episode_id",
                "timestamp_local",
                "status",
                "grade",
                "temperature",
                "top_p",
                "min_p",
                "max_tokens",
                "seed",
                "mode",
            ]
            for idx, key in enumerate(legacy_keys):
                if idx < len(row):
                    normalized[key] = row[idx]

        return [normalized[key] for key in self.INDEX_HEADER]

    def _atomic_write_json(self, path: Path, data: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=2)
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)

    def _append_index_row(self, row: Dict[str, str]):
        self._ensure_index()
        with self.index_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    row.get("episode_id", ""),
                    row.get("timestamp_local", ""),
                    row.get("status", ""),
                    row.get("grade", ""),
                    row.get("coherence", ""),
                    row.get("repetition", ""),
                    row.get("taste", ""),
                    row.get("continuity", ""),
                    row.get("temperature", ""),
                    row.get("top_p", ""),
                    row.get("min_p", ""),
                    row.get("max_tokens", ""),
                    row.get("seed", ""),
                    row.get("mode", ""),
                ]
            )

    def _update_index_row(
        self,
        episode_id: str,
        status: str,
        grade: Optional[int],
        feedback: Optional[Dict[str, Optional[float]]] = None,
    ):
        if not self.index_path.exists():
            return
        rows = []
        with self.index_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if row and row[0] == "episode_id":
                    rows.append(row)
                    continue
                if row and row[0] == episode_id:
                    row[2] = status
                    row[3] = "" if grade is None else str(int(grade))
                    if feedback:
                        row[4] = "" if feedback.get("coherence") is None else str(feedback["coherence"])
                        row[5] = "" if feedback.get("repetition") is None else str(feedback["repetition"])
                        row[6] = "" if feedback.get("taste") is None else str(feedback["taste"])
                        row[7] = "" if feedback.get("continuity") is None else str(feedback["continuity"])
                rows.append(row)
        with tempfile.NamedTemporaryFile("w", delete=False, dir=self.index_path.parent, newline="", encoding="utf-8") as tmp:
            writer = csv.writer(tmp)
            writer.writerows(rows)
            tmp_path = Path(tmp.name)
        tmp_path.replace(self.index_path)

    def create_episode(
        self,
        prompt_bytes: bytes,
        output_bytes: bytes,
        params: Dict,
        mode: str,
    ) -> str:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        ts_str = now.strftime("%Y%m%d_%H%M%S")
        short_id = uuid4().hex[:6]
        episode_id = f"{ts_str}_{short_id}"

        episode_dir = self.episodes_dir / date_str / episode_id
        episode_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = episode_dir / "prompt.mid"
        output_path = episode_dir / "output.mid"
        prompt_path.write_bytes(prompt_bytes)
        output_path.write_bytes(output_bytes)

        prompt_hash = hashlib.sha256(prompt_bytes).hexdigest()
        output_hash = hashlib.sha256(output_bytes).hexdigest()

        meta = {
            "episode_id": episode_id,
            "timestamp_local": now.isoformat(),
            "status": "draft",
            "grade": None,
            "mode": mode,
            "coherence": params.get("coherence"),
            "repetition": params.get("repetition"),
            "taste": params.get("taste"),
            "continuity": params.get("continuity"),
            "temperature": params.get("temperature"),
            "top_p": params.get("top_p"),
            "min_p": params.get("min_p"),
            "max_tokens": params.get("max_tokens"),
            "seed": params.get("seed"),
            "hashes": {
                "prompt_mid_sha256": prompt_hash,
                "output_mid_sha256": output_hash,
            },
        }
        self._atomic_write_json(episode_dir / "meta.json", meta)

        self._append_index_row(
            {
                "episode_id": episode_id,
                "timestamp_local": meta["timestamp_local"],
                "status": "draft",
                "grade": "",
                "coherence": params.get("coherence"),
                "repetition": params.get("repetition"),
                "taste": params.get("taste"),
                "continuity": params.get("continuity"),
                "temperature": params.get("temperature"),
                "top_p": params.get("top_p"),
                "min_p": params.get("min_p"),
                "max_tokens": params.get("max_tokens"),
                "seed": params.get("seed"),
                "mode": mode,
            }
        )

        return episode_id

    def finalize_episode(
        self,
        episode_id: str,
        grade: int,
        feedback: Optional[Dict[str, Optional[float]]] = None,
    ):
        # Find meta.json
        meta_path = None
        for candidate in self.episodes_dir.rglob("meta.json"):
            try:
                with candidate.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("episode_id") == episode_id:
                    meta_path = candidate
                    meta = data
                    break
            except Exception:
                continue
        if not meta_path:
            return

        meta["status"] = "final"
        meta["grade"] = int(grade)
        if feedback:
            meta["coherence"] = feedback.get("coherence")
            meta["repetition"] = feedback.get("repetition")
            meta["taste"] = feedback.get("taste")
            meta["continuity"] = feedback.get("continuity")
        self._atomic_write_json(meta_path, meta)
        self._update_index_row(episode_id, "final", int(grade), feedback=feedback)

    def find_most_recent_draft_episode(self) -> Optional[str]:
        if not self.index_path.exists():
            return None

        with self.index_path.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))

        for row in reversed(rows[1:]):
            if row and len(row) >= 3 and row[2] == "draft":
                return row[0]

        return None
