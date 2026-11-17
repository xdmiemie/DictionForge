"""Exercise the actual feature/preflight HTTP chain; no simulated models."""
import argparse
import json
from pathlib import Path
import time
import urllib.request
import uuid

def main():
    p = argparse.ArgumentParser()
    p.add_argument("version_id")
    p.add_argument("--url", default="http://127.0.0.1:7867")
    p.add_argument("--output", type=Path, default=Path("data/p04-http.json"))
    args = p.parse_args()
    def api(path, payload=None):
        req = urllib.request.Request(args.url+"/api"+path, data=json.dumps(payload).encode() if payload is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    def run(path, payload):
        job = api(path, dict(payload, request_id=str(uuid.uuid4())))
        deadline = time.monotonic()+1200
        while job["state"] in {"QUEUED", "RUNNING"}:
            if time.monotonic() > deadline:
                raise TimeoutError(job["id"])
            time.sleep(1)
            job = api("/jobs/"+job["id"])
        if job["state"] != "COMPLETED" or not job["result"].get("ready"):
            raise RuntimeError(json.dumps(job, ensure_ascii=False))
        return job
    features = run(f"/dataset-versions/{args.version_id}/features", {})
    config = dict(version_id=args.version_id, initial_model="official", stages=["s2", "s1"], seed=42,
                  s2=dict(batch_size=1, precision="fp16", rank=32, stop_mode="steps", target=2, learning_rate=.0001),
                  s1=dict(batch_size=1, precision="fp16", stop_mode="steps", target=2, learning_rate=.01, accumulate=4))
    preflight = run("/training/preflight", config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(features=features, preflight=preflight), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(features=features["id"], preflight=preflight["id"], ready=True)))

if __name__ == "__main__":
    main()
