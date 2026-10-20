"""Manifiestos persistentes para trabajos de producción recuperables."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


VALID_STATUSES = {"pending", "running", "degraded", "completed", "failed"}


def write_json_atomic(path: str, data: Any) -> None:
    """Escribe a un temporal y reemplaza: un corte a mitad nunca deja un JSON corrupto."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class JobManifest:
    def __init__(self, work_dir: str, job_id: str, inputs: Optional[Dict[str, Any]] = None):
        self.work_dir = work_dir
        self.path = os.path.join(work_dir, "manifest.json")
        self.data = self._load() or {
            "job_id": job_id,
            "status": "pending",
            "inputs": inputs or {},
            "steps": {},
            "artifacts": {},
            "warnings": [],
            "errors": [],
            "created_at": self._now(),
            "updated_at": self._now(),
        }
        self.save()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                loaded = json.load(file)
                return loaded if isinstance(loaded, dict) else None
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def save(self) -> None:
        self.data["updated_at"] = self._now()
        write_json_atomic(self.path, self.data)

    @property
    def status(self) -> str:
        return self.data.get("status", "pending")

    def set_status(self, status: str) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Estado de trabajo inválido: {status}")
        self.data["status"] = status
        self.save()

    def begin_step(self, name: str) -> None:
        self.data.setdefault("steps", {})[name] = {"status": "running", "started_at": self._now()}
        self.set_status("running")

    def complete_step(self, name: str, artifacts: Optional[Dict[str, Any]] = None) -> None:
        step = self.data.setdefault("steps", {}).setdefault(name, {})
        step.update({"status": "completed", "completed_at": self._now()})
        if artifacts:
            self.data.setdefault("artifacts", {}).update(artifacts)
        self.save()

    def step_done(self, name: str) -> bool:
        return self.data.get("steps", {}).get(name, {}).get("status") == "completed"

    def warn(self, step: str, message: str) -> None:
        self.data.setdefault("warnings", []).append({"step": step, "message": message, "at": self._now()})
        self.save()

    def finish(self) -> str:
        """Cierra el trabajo: 'degraded' si hubo avisos (p. ej. imágenes de respaldo), si no 'completed'."""
        self.data["status"] = "degraded" if self.data.get("warnings") else "completed"
        self.data["finished_at"] = self._now()
        self.save()
        return self.data["status"]

    def reset_for_rerun(self) -> None:
        """Al reanudar se limpian avisos y errores del intento anterior."""
        self.data["warnings"] = []
        self.data["errors"] = []
        self.save()

    def fail(self, step: str, error: Exception | str) -> None:
        self.data.setdefault("errors", []).append({"step": step, "message": str(error), "at": self._now()})
        self.data["status"] = "failed"
        self.save()

    def artifact(self, name: str) -> Optional[Any]:
        return self.data.get("artifacts", {}).get(name)
