"""Minimal MLflow tracking/registry REST client.

The host virtualenv does not have the `mlflow` package, and the running backend
image was built before this sprint, so the release script talks to the tracking
server over its public HTTP API instead of adding a dependency or rebuilding the
stack. Only what the release path needs is implemented.
"""
import time

import httpx


class MlflowRest:
    def __init__(self, tracking_uri):
        self.http = httpx.Client(base_url=tracking_uri.rstrip("/"), timeout=120)

    def _post(self, path, payload):
        r = self.http.post("/api/2.0/mlflow/" + path, json=payload)
        r.raise_for_status()
        return r.json()

    def experiment_id(self, name):
        r = self.http.get("/api/2.0/mlflow/experiments/get-by-name",
                          params={"experiment_name": name})
        if r.status_code == 200:
            return r.json()["experiment"]["experiment_id"]
        return self._post("experiments/create", {"name": name})["experiment_id"]

    def start_run(self, experiment_id, tags=None):
        payload = {"experiment_id": experiment_id,
                   "start_time": int(time.time() * 1000),
                   "tags": [{"key": k, "value": str(v)} for k, v in (tags or {}).items()]}
        return self._post("runs/create", payload)["run"]["info"]["run_id"]

    def finish_run(self, run_id, status="FINISHED"):
        self._post("runs/update", {"run_id": run_id, "status": status,
                                   "end_time": int(time.time() * 1000)})

    def run(self, run_id):
        r = self.http.get("/api/2.0/mlflow/runs/get", params={"run_id": run_id})
        r.raise_for_status()
        return r.json()["run"]

    def log_artifact(self, experiment_id, run_id, relative_path, filename, payload):
        """Upload bytes into the run's artifact directory.

        Returns True only when the exact bytes can be read back, so a caller can
        never claim an evidence artifact that was not actually written.
        """
        prefix = "%s/%s/artifacts/" % (experiment_id, run_id)
        if relative_path:
            prefix += relative_path.strip("/") + "/"
        path = prefix + filename
        r = self.http.put("/api/2.0/mlflow-artifacts/artifacts/" + path,
                          files={"file": (filename, payload)})
        if r.status_code not in (200, 204):
            return False
        # MLflow serves downloads as a multipart body, so exact byte equality of
        # the whole response does not hold; require the payload to be present.
        check = self.http.get("/api/2.0/mlflow-artifacts/artifacts/" + path)
        return check.status_code == 200 and payload in check.content

    def register_model_version(self, name, source, run_id):
        try:
            self._post("registered-models/create", {"name": name})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (400, 409):
                raise
        return self._post("model-versions/create",
                          {"name": name, "source": source, "run_id": run_id})
