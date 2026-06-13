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


class Scheduler:
    """File FIFO mono-worker + bus pub/sub par doc. Le worker exécute run_extraction
    et émet chaque event vers les abonnés (SSE)."""

    def __init__(self, cfg, store, *, run=run_extraction, data_dir=None):
        self.cfg = cfg
        self.store = store
        self._run = run
        self.data_dir = data_dir if data_dir is not None else cfg.data_dir
        self.queue: asyncio.Queue = asyncio.Queue()
        self._subs: dict[int, list[asyncio.Queue]] = {}
        self._pause: set[int] = set()
        self._task = None

    def enqueue(self, doc_id: int) -> None:
        self.store.set_status(doc_id, "queued")
        self.queue.put_nowait(doc_id)

    def subscribe(self, doc_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._subs.setdefault(doc_id, []).append(q)
        return q

    def unsubscribe(self, doc_id: int, q: asyncio.Queue) -> None:
        lst = self._subs.get(doc_id)
        if lst and q in lst:
            lst.remove(q)

    def emit(self, doc_id: int, ev: dict) -> None:
        for q in list(self._subs.get(doc_id, [])):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass

    async def _process(self, doc_id: int) -> None:
        segments = load_segments(self.data_dir, doc_id)
        doc = self.store.get_document(doc_id)
        ps, pc = doc["progress_segment"], doc["progress_chunk"]
        self.store.set_status(doc_id, "running")
        self.emit(doc_id, {"type": "status", "status": "running"})
        gen = self._run(self.cfg, self.store, doc_id, segments,
                        start_segment=ps, start_chunk=pc)
        async for ev in gen:
            self.emit(doc_id, ev)
            if ev.get("type") == "round_end":
                self.store.set_progress(doc_id, ev["segment"], ev["chunk"] + 1)
                if doc_id in self._pause:
                    self._pause.discard(doc_id)
                    self.store.set_status(doc_id, "paused")
                    self.emit(doc_id, {"type": "status", "status": "paused"})
                    await gen.aclose()
                    return

    async def _worker(self) -> None:
        while True:
            doc_id = await self.queue.get()
            try:
                await self._process(doc_id)
            except Exception:
                pass  # le pipeline a déjà émis 'error' et marqué le doc 'failed'
            finally:
                self.queue.task_done()

    def start(self) -> None:
        self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
