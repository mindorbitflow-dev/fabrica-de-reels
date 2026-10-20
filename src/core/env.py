"""Carga del archivo .env sin dependencias externas."""

import os

_loaded = False


def load_env(path: str = ".env") -> None:
    """Carga variables de .env una sola vez sin pisar las ya definidas en el sistema."""
    global _loaded
    if _loaded or not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value and not os.environ.get(key):
                os.environ[key] = value
    _loaded = True


def save_env(updates: dict, path: str = ".env") -> None:
    """Reescribe solo estas claves en .env (el resto, comentarios incluidos, se conserva) y las aplica ya."""
    lines = open(path, "r", encoding="utf-8").read().splitlines() if os.path.exists(path) else []
    pending = dict(updates)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in pending and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
    for key, value in updates.items():
        os.environ[key] = value
