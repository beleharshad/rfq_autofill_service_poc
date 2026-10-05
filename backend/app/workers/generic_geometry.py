"""Worker entry point. The API runner claims work and bounds runtime."""
import sys
from pathlib import Path
from app.storage.accepted_parts import AcceptedParts
from app.services.generic_geometry import build_geometry


def execute(job_path, build_id):
    store = AcceptedParts(job_path)
    task = store.build(build_id)
    if task["status"] != "running":
        return
    try:
        spec = store.get(task["spec_id"])
        output = job_path / "outputs" / "accepted" / build_id
        manifest = build_geometry(spec, store.registry, output)
        # A correction/source edit during construction prevents publishing as current.
        store.get(task["spec_id"])
        store.finish(build_id, manifest=manifest)
    except Exception as exc:
        store.finish(build_id, error=f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    execute(Path(sys.argv[1]), sys.argv[2])
