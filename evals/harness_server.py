"""Vendor-risk service used only by the evaluation harness. Same behaviour as
mock_api/app.py, plus per-case data directories and injected faults:
  /{ctx}/{fault}/vendor-risk/{name}   fault in: none | timeout | malformed | down"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException

CONTEXTS: dict[str, Path] = {}
app = FastAPI(title="Eval vendor-risk service")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/{ctx}/{fault}/vendor-risk/{vendor_name}")
def vendor_risk(ctx: str, fault: str, vendor_name: str) -> dict:
    if fault == "timeout":
        time.sleep(4)
    if fault == "down":
        raise HTTPException(status_code=503, detail="injected outage")
    if fault == "malformed":
        return {"vendor": vendor_name, "status": "ok"}
    data = json.loads((CONTEXTS[ctx] / "vendor_risk.json").read_text(encoding="utf-8"))
    record = data.get(vendor_name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No vendor-risk record for '{vendor_name}'")
    if record.get("force_error"):
        raise HTTPException(status_code=503, detail=record.get("error_message", "unavailable"))
    return {"vendor_name": vendor_name, **record}


def start(port: int) -> None:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    import requests
    for _ in range(50):
        try:
            if requests.get(f"http://127.0.0.1:{port}/health", timeout=0.3).ok:
                return
        except requests.RequestException:
            time.sleep(0.1)
    raise RuntimeError("eval vendor-risk service did not start")
