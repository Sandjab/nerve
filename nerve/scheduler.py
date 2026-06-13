# nerve/scheduler.py
import os
import json
import asyncio
from nerve.pipeline import run_extraction


def _segments_path(data_dir: str, doc_id: int) -> str:
    return os.path.join(data_dir, "inputs", str(doc_id), "segments.jsonl")


def write_segments(data_dir: str, doc_id: int, segments: list[tuple[str, str]]) -> None:
    p = _segments_path(data_dir, doc_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for text, source_file in segments:
            f.write(json.dumps({"text": text, "source_file": source_file}) + "\n")


def load_segments(data_dir: str, doc_id: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    with open(_segments_path(data_dir, doc_id), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                out.append((d["text"], d["source_file"]))
    return out
