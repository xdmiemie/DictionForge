"""Real HTTP dual-stage training acceptance using a frozen preflight."""
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
    p.add_argument("--output", type=Path, default=Path("data/p05-http.json"))
    args = p.parse_args()
    def api(path, payload=None):
        req = urllib.request.Request(args.url+"/api"+path, data=json.dumps(payload).encode() if payload is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    def wait(job):
        deadline = time.monotonic()+1800
        while job["state"] in {"QUEUED", "RUNNING"}:
            if time.monotonic() > deadline:
                raise TimeoutError(job["id"])
            time.sleep(1)
            job = api("/jobs/"+job["id"])
        if job["state"] != "COMPLETED":
            raise RuntimeError(json.dumps(job, ensure_ascii=False))
        return job
    config = dict(version_id=args.version_id, initial_model="official", stages=["s2", "s1"], seed=42,
                  s2=dict(batch_size=1, precision="fp16", rank=32, stop_mode="steps", target=2, learning_rate=.0001),
                  s1=dict(batch_size=1, precision="fp16", stop_mode="steps", target=2, learning_rate=.002, accumulate=4))
    preflight = wait(api("/training/preflight", dict(config, request_id=str(uuid.uuid4()))))
    if not preflight["result"]["ready"]:
        raise RuntimeError(str(preflight["result"]))
    request = dict(preflight_id=preflight["id"], request_id=str(uuid.uuid4()))
    submitted = api("/training", request)
    assert api("/training", request)["id"] == submitted["id"]
    trained = wait(submitted)
    result = trained["result"]
    for stage in ("s2", "s1"):
        metrics = result["stages"][stage]
        assert metrics["optimizer_updates"] == 2
        assert metrics["initial_parameters_sha256"] != metrics["final_parameters_sha256"]
    snapshot = api("/training/"+trained["id"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(preflight=preflight, training=trained, snapshot=snapshot), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(run_id=trained["id"], state=trained["state"], snapshots=result["snapshots"])))

if __name__ == "__main__":
    main()
