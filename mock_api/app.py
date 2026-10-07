from __future__ import annotations

import json
from pathlib import Path
import os

from fastapi import FastAPI, HTTPException

ROOT = Path(__file__).resolve().parents[1]


def _load() -> dict:
    # Read on each request so a swapped DATA_DIR is honoured without a restart.
    data_dir = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    return json.loads((data_dir / "vendor_risk.json").read_text(encoding="utf-8"))


DATA = _load()  # kept for backwards compatibility with code that imports it

app = FastAPI(title="FDE Mock Vendor Risk API", version="1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/vendor-risk/{vendor_name}")
def vendor_risk(vendor_name: str) -> dict:
    # FastAPI already URL-decodes path parameters; decoding again (the starter's
    # unquote) corrupted names containing '%'.
    name = vendor_name
    record = _load().get(name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No vendor-risk record for '{name}'")
    if record.get("force_error"):
        raise HTTPException(status_code=503, detail=record.get("error_message", "Vendor-risk service unavailable"))
    return {"vendor_name": name, **record}
