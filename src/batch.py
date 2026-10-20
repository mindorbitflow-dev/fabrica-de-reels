"""
Producción en lote.

Cada tema recibe un job_id estable derivado del nombre del lote, así que relanzar
el mismo lote salta los videos ya terminados y reanuda los que quedaron a medias.
Los trabajos corren en procesos separados (FFmpeg es intensivo en CPU y cada
proceso tiene su propio pipeline).
"""

from __future__ import annotations

import csv
import multiprocessing
import os
import re
import json
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, List, Optional

from src.core.job_manager import write_json_atomic


@dataclass
class BatchItem:
    topic: str
    niche: str = "curiosidades"
    duration: int = 30
    voice: Optional[str] = None
    aspect_ratio: str = "9:16"


@dataclass
class BatchOutcome:
    index: int
    topic: str
    job_id: str
    status: str            # completed | degraded | failed | skipped
    video_path: str = ""
    seconds: float = 0.0
    warnings: int = 0
    error: str = ""


def parse_batch_file(path: str, default_niche: str = "curiosidades", default_duration: int = 30) -> List[BatchItem]:
    """TXT: un tema por línea, opcionalmente 'tema | nicho | duración'. CSV: columnas topic,niche,duration,voice,aspect_ratio."""
    items: List[BatchItem] = []
    if path.lower().endswith(".csv"):
        with open(path, "r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                topic = (row.get("topic") or row.get("tema") or "").strip()
                if topic:
                    items.append(BatchItem(
                        topic=topic,
                        niche=(row.get("niche") or row.get("nicho") or default_niche).strip(),
                        duration=int(row.get("duration") or row.get("duracion") or default_duration),
                        voice=(row.get("voice") or row.get("voz") or "").strip() or None,
                        aspect_ratio=(row.get("aspect_ratio") or "9:16").strip(),
                    ))
        return items
    with open(path, "r", encoding="utf-8-sig") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            items.append(BatchItem(
                topic=parts[0],
                niche=parts[1] if len(parts) > 1 and parts[1] else default_niche,
                duration=int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else default_duration,
            ))
    return items


def batch_job_id(batch_name: str, index: int, topic: str) -> str:
    from src.pipeline import make_job_id
    slug = make_job_id(topic).split("_", 3)[-1]
    return f"{batch_name}_{index + 1:03d}_{slug}"


def _finished_manifest(job_id: str) -> Optional[Dict[str, Any]]:
    """Manifiesto del trabajo si ya terminó (completed/degraded) y su MP4 existe."""
    manifest_path = os.path.join("output", job_id, "manifest.json")
    if not (os.path.exists(manifest_path) and os.path.exists(os.path.join("output", f"{job_id}.mp4"))):
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    return data if data.get("status") in ("completed", "degraded") else None


# Un pipeline por proceso de trabajo, creado una sola vez.
_WORKER_PIPELINE = None


def _init_worker(image_gate=None):
    global _WORKER_PIPELINE
    from src.pipeline import ReelFactoryPipeline
    _WORKER_PIPELINE = ReelFactoryPipeline()
    if image_gate is not None:
        # Límite de Pollinations compartido por todos los procesos del lote.
        _WORKER_PIPELINE.media._pollinations_gate = image_gate


def _run_one(index: int, item: BatchItem, job_id: str, options: Dict[str, Any]) -> BatchOutcome:
    global _WORKER_PIPELINE
    if _WORKER_PIPELINE is None:
        _init_worker()
    started = time.time()
    try:
        result = _WORKER_PIPELINE.generate_reel(
            topic=item.topic, niche=item.niche, target_duration=item.duration,
            voice=item.voice or options.get("voice"), aspect_ratio=item.aspect_ratio,
            subtitle_style=options.get("subtitle_style", "yellow_pop"),
            production_profile=options.get("production_profile"),
            platform=options.get("platform"), research=options.get("research"),
            job_id=job_id, resume=True,
        )
        return BatchOutcome(index, item.topic, job_id, result.status, result.video_path,
                            round(time.time() - started, 1), len(result.warnings))
    except Exception as error:
        traceback.print_exc()
        return BatchOutcome(index, item.topic, job_id, "failed", seconds=round(time.time() - started, 1), error=str(error)[:500])


def run_batch(
    items: List[BatchItem],
    batch_name: str,
    workers: int = 1,
    options: Optional[Dict[str, Any]] = None,
    on_progress: Optional[Callable[[BatchOutcome, int, int], None]] = None,
) -> List[BatchOutcome]:
    options = options or {}
    batch_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", batch_name)[:40] or "lote"
    jobs = [(i, item, batch_job_id(batch_name, i, item.topic)) for i, item in enumerate(items)]
    outcomes: List[BatchOutcome] = []
    pending = []
    for i, item, job_id in jobs:
        finished = _finished_manifest(job_id)
        if finished:
            outcome = BatchOutcome(
                i, item.topic, job_id, "skipped", os.path.join("output", f"{job_id}.mp4"),
                warnings=len(finished.get("warnings", [])),
                error=f"ya producido ({finished.get('status')})",
            )
            outcomes.append(outcome)
            if on_progress:
                on_progress(outcome, len(outcomes), len(jobs))
        else:
            pending.append((i, item, job_id))

    if workers <= 1:
        for i, item, job_id in pending:
            outcome = _run_one(i, item, job_id, options)
            outcomes.append(outcome)
            _write_report(batch_name, outcomes)
            if on_progress:
                on_progress(outcome, len(outcomes), len(jobs))
    else:
        from src.core.env import load_env
        load_env()
        slots = options.get("image_concurrency", 2) if os.getenv("POLLINATIONS_TOKEN") else 1
        manager = multiprocessing.Manager()
        image_gate = manager.Semaphore(slots)
        with manager, ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(image_gate,)) as pool:
            futures = [pool.submit(_run_one, i, item, job_id, options) for i, item, job_id in pending]
            for future in as_completed(futures):
                outcome = future.result()
                outcomes.append(outcome)
                _write_report(batch_name, outcomes)
                if on_progress:
                    on_progress(outcome, len(outcomes), len(jobs))

    outcomes.sort(key=lambda o: o.index)
    _write_report(batch_name, outcomes)
    return outcomes


def _write_report(batch_name: str, outcomes: List[BatchOutcome]) -> str:
    report_dir = os.path.join("output", "lotes")
    os.makedirs(report_dir, exist_ok=True)
    rows = [asdict(o) for o in sorted(outcomes, key=lambda o: o.index)]
    write_json_atomic(os.path.join(report_dir, f"{batch_name}.json"), rows)
    csv_path = os.path.join(report_dir, f"{batch_name}.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(BatchOutcome.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(rows)
    return csv_path
