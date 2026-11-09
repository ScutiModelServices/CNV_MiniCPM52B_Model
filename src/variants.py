"""The MiniCPM5-2B quantizations an admin can choose as the base model, each with an estimate of what it takes and where this machine can run it.

The quantizations are the GGUF files of `openbmb/MiniCPM5-2B-GGUF` that Ollama can pull as `hf.co/<repo>:<quant>`; the
sizes are the real file sizes from the Hub (each tag was checked against the registry manifest). `Q5_K_M` and `Q6_K` are
not published. Project models (Ollama names with the project prefix) are never listed and never changed by the card.

The estimate is arithmetic, not a measurement: the file size times 1.1 plus 400 MB for the context and the runtime.
Ollama decides where a model runs; the card says "GPU" when the estimate fits the free GPU memory, "CPU" when it only
fits in free RAM (slower), and nothing is claimed when this service cannot see Ollama's machine.

shortcut: free memory is read when the page is opened and does not know what Ollama will load next."""

from __future__ import annotations

from typing import Any

REPO = "hf.co/openbmb/MiniCPM5-2B-GGUF"
QUANTS = (  # (Ollama tag, file size in MB, note)
    ("Q4_K_M", 1488, "The usual choice: small, little quality loss."),
    ("Q8_0", 2555, "Almost the original."),
    ("F16", 4805, "The original precision: largest, no quantization loss."),
)
FREE_SHARE = {"gpu": 0.9, "cpu": 0.85}  # keep some memory for the rest of the machine


def estimate_mb(file_mb: int) -> int:
    return round(file_mb * 1.1 + 400)


UNKNOWN = "Free memory could not be read."
REMOTE = "Ollama runs on another machine, so its memory cannot be checked."


def place(need_mb: int, hardware: dict[str, Any]) -> tuple[str | None, str]:
    """(`gpu`, `cpu`, or None when it fits neither, plus the reason). Unreadable memory is not a no: the reason is UNKNOWN."""
    gpu, free = hardware.get("gpu"), hardware.get("ram_available_mb")
    if gpu and need_mb <= gpu["vram_free_mb"] * FREE_SHARE["gpu"]:
        return "gpu", ""
    if free is None:
        return None, UNKNOWN
    if need_mb <= free * FREE_SHARE["cpu"]:
        return "cpu", ""
    gpu_text = f" or {gpu['vram_free_mb']} MB of GPU memory" if gpu else ""
    return None, f"Needs about {need_mb} MB; {free} MB of memory{gpu_text} is free."


def listing(hardware: dict[str, Any] | None, current: str, downloaded: set[str]) -> list[dict[str, Any]]:
    """Every quantization, smallest first. `hardware` is None when Ollama is not on this machine (its memory cannot be
    seen, so `fits` is None). `current` is the Ollama model name in use; it always fits, because it is loaded already.
    `downloaded` holds the Ollama model names that are on disk."""
    rows = []
    for quant, file_mb, note in QUANTS:
        name, need = f"{REPO}:{quant}", estimate_mb(file_mb)
        in_use = name == current
        where, reason = place(need, hardware) if hardware is not None else (None, REMOTE)
        fits = True if in_use or where else (None if reason in (UNKNOWN, REMOTE) else False)
        runs = {"gpu": " · runs on the GPU", "cpu": " · runs on the CPU (slower)"}.get(where, "")
        rows.append({"title": quant, "detail": f"about {need} MB · {file_mb} MB download{' · downloaded' if name in downloaded else ''}{runs} · {note}",
                     "id": quant, "model": name, "name": quant, "estimated_mb": need, "disk_mb": file_mb, "note": note,
                     "runs_on": where, "fits": fits, "reason": "" if in_use or fits else reason,
                     "downloaded": name in downloaded, "current": in_use})
    return sorted(rows, key=lambda row: row["estimated_mb"])


def find(rows: list[dict[str, Any]], variant_id: str) -> dict[str, Any] | None:
    return next((row for row in rows if row["id"] == variant_id), None)
