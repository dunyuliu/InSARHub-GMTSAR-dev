# -*- coding: utf-8 -*-
"""Processor and HyP3 job management endpoints."""

import asyncio
import dataclasses
import json
import threading as _threading
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException

import insarhub.app.state as state
from insarhub.app.models import ProcessRequest, Hyp3ActionRequest, LocalActionRequest
from insarhub.app.state import _apply_config_from_dict, _new_job, _finish_job, write_insarhub_config
from insarhub.commands.processor import SaveJobsCommand, SubmitCommand
from insarhub.core.registry import Processor

router = APIRouter()


@router.post("/api/folder-process")
async def folder_process(req: ProcessRequest, background_tasks: BackgroundTasks):
    """Read pairs from folder, submit to processor, save job IDs."""
    folder = Path(req.folder_path).expanduser().resolve()
    stack_files = sorted(folder.glob("stack_p*_f*.json"))
    if not stack_files:
        raise HTTPException(status_code=404, detail="No stack file found in folder")
    job_id, _ = _new_job("Starting…")
    background_tasks.add_task(_run_folder_process, job_id, req)
    return {"job_id": job_id}


async def _run_folder_process(job_id: str, req: ProcessRequest):
    def run():
        try:
            folder = Path(req.folder_path).expanduser().resolve()
            stack_files = sorted(folder.glob("stack_p*_f*.json"))
            if not stack_files:
                state._jobs[job_id] = {"status": "error", "progress": 0, "message": "No stack file found", "data": None}
                return

            # Combine pairs from every stack file in the folder — a merged
            # download/select-pairs writes one file per path, and a folder
            # can otherwise legitimately hold more than one flat stack file.
            pairs: list[tuple[str, str]] = []
            for sf in stack_files:
                data = json.loads(sf.read_text())
                pairs.extend(tuple(p) for p in data.get("pairs", []))

            proc_cls = Processor._registry.get(req.processor_type)
            if proc_cls is None:
                _finish_job(job_id, status="error", progress=0, message=f"Unknown processor: {req.processor_type}")
                return
            cfg_cls = getattr(proc_cls, "default_config", None)
            if cfg_cls is None or not dataclasses.is_dataclass(cfg_cls):
                _finish_job(job_id, status="error", progress=0, message="Processor has no config")
                return

            cfg = cfg_cls(workdir=folder)
            _apply_config_from_dict(cfg, req.processor_config, skip_keys={"workdir", "pairs"})
            if hasattr(cfg, "__post_init__"):
                cfg.__post_init__()  # re-resolve any "auto"/"" values reintroduced by user dict
            cfg.pairs = pairs

            proc_cfg = {k: v for k, v in dataclasses.asdict(cfg).items() if k not in ("workdir", "pairs")}

            import inspect as _inspect
            _sig = _inspect.signature(proc_cls.__init__).parameters
            is_local = "pairs" in _sig

            if req.dry_run:
                n = len(pairs)
                write_insarhub_config(folder, {"processor": {"type": req.processor_type, "config": proc_cfg}})
                state._jobs[job_id] = {
                    "status": "done", "progress": 100,
                    "message": f"[Dry run] Would submit {n} pair{'s' if n != 1 else ''} via {req.processor_type} from {folder.name}",
                    "data": None,
                }
                return

            state._jobs[job_id]["message"] = "Submitting jobs…"
            if is_local:
                # Local processor (e.g. ISCE_S1): load sbatch options, submit directly.
                # Only relevant in HPC mode; auto-generates the file (steps 01-17) if missing.
                if getattr(cfg, "hpc_mode", False):
                    from insarhub.processor.isce_base import load_or_init_sbatch_options
                    per_step = load_or_init_sbatch_options(folder)
                    if per_step is None:
                        _finish_job(
                            job_id, status="error", progress=0,
                            message=f"No sbatch_options.json found — created a default at "
                                    f"{folder / 'sbatch_options.json'}. Review the resource "
                                    f"settings for each step, then submit again.")
                        return
                    cfg.sbatch_options_per_step = per_step
                processor = proc_cls(pairs=pairs, config=cfg)
                jobs = processor.submit(steps=req.steps or None)
                n_steps = len(jobs) if isinstance(jobs, dict) else len(pairs)
                write_insarhub_config(folder, {"processor": {"type": req.processor_type, "config": proc_cfg}})
                step_msg = f" (steps: {', '.join(req.steps)})" if req.steps else ""
                _finish_job(job_id, status="done", progress=100,
                            message=f"Submitted {n_steps} step(s){step_msg} via {req.processor_type}. Processing running in background.")
            else:
                processor = Processor.create(req.processor_type, cfg)
                submit_result = SubmitCommand(processor, progress_callback=state._make_progress(job_id)).run()
                if not submit_result.success:
                    state._jobs[job_id] = {"status": "error", "progress": 0, "message": submit_result.message, "data": None}
                    return
                state._jobs[job_id]["message"] = "Saving job IDs…"
                SaveJobsCommand(processor, progress_callback=state._make_progress(job_id)).run()
                write_insarhub_config(folder, {"processor": {"type": req.processor_type, "config": proc_cfg}})
                _finish_job(job_id, status="done", message=submit_result.message)
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))

    await asyncio.to_thread(run)


@router.get("/api/folder-hyp3-jobs")
async def get_folder_hyp3_jobs(path: str):
    """List hyp3*.json job files in a folder with stored job counts."""
    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    files = []
    for f in sorted(f for f in folder.glob("hyp3*.json") if not f.name.startswith("hyp3_retry_")):
        try:
            data = json.loads(f.read_text())
            job_ids = data.get("job_ids", {})
            total = sum(len(v) for v in job_ids.values())
            users = list(job_ids.keys())
        except Exception:
            total = 0
            users = []
        files.append({"name": f.name, "total": total, "users": users})
    # Prefer insarhub_config.json (new format), fall back to legacy processor_config.json
    proc_type = None
    try:
        from insarhub.app.state import read_insarhub_config
        cfg = read_insarhub_config(folder)
        proc_type = cfg.get("processor", {}).get("type")
    except Exception:
        pass
    if not proc_type:
        proc_cfg_path = folder / "processor_config.json"
        if proc_cfg_path.exists():
            try:
                pc = json.loads(proc_cfg_path.read_text())
                proc_type = pc.get("name") or pc.get("processor_type")
            except Exception:
                pass
    return {"files": files, "processor_type": proc_type}


@router.post("/api/folder-hyp3-action")
async def folder_hyp3_action(req: Hyp3ActionRequest, background_tasks: BackgroundTasks):
    job_id, _ = _new_job("Starting…")
    background_tasks.add_task(_run_hyp3_action, job_id, req)
    return {"job_id": job_id}


async def _run_hyp3_action(job_id: str, req: Hyp3ActionRequest):
    def run():
        try:
            folder = Path(req.folder_path).expanduser().resolve()
            job_file = folder / req.job_file
            if not job_file.exists():
                state._jobs[job_id] = {"status": "error", "progress": 0, "message": f"{req.job_file} not found", "data": None}
                return

            proc_cls = Processor._registry.get(req.processor_type)
            if proc_cls is None:
                _finish_job(job_id, status="error", progress=0, message=f"Unknown processor: {req.processor_type}")
                return
            cfg_cls = getattr(proc_cls, "default_config", None)
            if cfg_cls is None or not dataclasses.is_dataclass(cfg_cls):
                _finish_job(job_id, status="error", progress=0, message="Processor has no config")
                return

            cfg = cfg_cls(workdir=folder)
            cfg.saved_job_path = str(job_file)
            state._jobs[job_id]["message"] = "Initializing processor…"
            processor = Processor.create(req.processor_type, cfg)

            if req.action == "refresh":
                state._jobs[job_id]["message"] = "Refreshing job statuses…"
                batchs = processor.refresh()
                lines = []
                counts: dict[str, int] = {}
                filenames: list[str] = []
                for user, batch in batchs.items():
                    lines.append(f"[{user}]")
                    for j in batch.jobs:
                        sc = j.status_code
                        counts[sc] = counts.get(sc, 0) + 1
                        lines.append(f"  {j.name:<35} {j.job_id:<12} | {sc}")
                        if sc == "SUCCEEDED" and j.files:
                            for fm in j.files:
                                fn = fm.get("filename") or fm.get("s3", {}).get("key", "").split("/")[-1]
                                if fn and fn.endswith(".zip"):
                                    filenames.append(fn)
                try:
                    cache_path = folder / ".insarhub_cache.json"
                    existing: dict = {}
                    if cache_path.exists():
                        try:
                            existing = json.loads(cache_path.read_text())
                        except Exception:
                            pass
                    cache = {
                        "out_dir": processor.output_dir.as_posix(),
                        "filenames": filenames if filenames else existing.get("filenames", []),
                    }
                    cache_path.write_text(json.dumps(cache, indent=2))
                except Exception:
                    pass
                total = sum(counts.values())
                summary = f"{total} jobs — " + ", ".join(
                    f"{v} {k.lower()}" for k, v in sorted(counts.items())
                )
                lines.insert(0, summary)
                _finish_job(job_id, status="done", message="\n".join(lines))

            elif req.action == "retry":
                state._jobs[job_id]["message"] = "Retrying failed jobs…"
                processor.retry()
                _finish_job(job_id, status="done", message="Retry submitted. New job file saved.")

            elif req.action == "download":
                dl_stop = _threading.Event()
                state._stop_events[job_id] = dl_stop
                state._jobs[job_id]["message"] = "Downloading succeeded jobs…"

                def _dl_progress(msg: str, pct: int):
                    state._jobs[job_id]["progress"] = pct
                    state._jobs[job_id]["message"]  = msg

                _, dl_results = processor.download(progress_callback=_dl_progress, stop_event=dl_stop)
                state._stop_events.pop(job_id, None)
                r = dl_results
                summary = f"{r['downloaded']} downloaded, {r['skipped']} existing, {r['failed']} failed"
                if dl_stop.is_set():
                    summary = f"Stopped. {summary}"
                pct = 100 if not dl_stop.is_set() else state._jobs[job_id].get("progress", 0)
                _finish_job(job_id, status="done", progress=pct, message=summary)

            else:
                _finish_job(job_id, status="error", progress=0, message=f"Unknown action: {req.action}")

        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))

    await asyncio.to_thread(run)


@router.get("/api/folder-local-jobs")
async def get_folder_local_jobs(path: str):
    """List isce_jobs*.json files in a folder with stored job counts."""
    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    files = []
    # Search root and isce/ subdirectory (ISCE_S1 saves jobs to workdir/isce/)
    seen = set()
    for f in sorted(list(folder.glob("isce_jobs*.json")) + list((folder / "isce").glob("isce_jobs*.json"))):
        if f in seen:
            continue
        seen.add(f)
        try:
            data = json.loads(f.read_text())
            total = len(data.get("jobs", {}))
        except Exception:
            total = 0
        rel = f.relative_to(folder)
        files.append({"name": str(rel), "total": total, "users": []})
    proc_type = None
    try:
        from insarhub.app.state import read_insarhub_config
        cfg = read_insarhub_config(folder)
        proc_type = cfg.get("processor", {}).get("type")
    except Exception:
        pass
    return {"files": files, "processor_type": proc_type}


@router.post("/api/folder-local-action")
async def folder_local_action(req: LocalActionRequest, background_tasks: BackgroundTasks):
    job_id, _ = state._new_job("Starting…")
    background_tasks.add_task(_run_local_action, job_id, req)
    return {"job_id": job_id}


async def _run_local_action(job_id: str, req: LocalActionRequest):
    def run():
        try:
            folder = Path(req.folder_path).expanduser().resolve()
            job_file = folder / req.job_file
            if not job_file.exists():
                state._jobs[job_id] = {"status": "error", "progress": 0, "message": f"{req.job_file} not found", "data": None}
                return

            proc_cls = Processor._registry.get(req.processor_type)
            if proc_cls is None:
                state._finish_job(job_id, status="error", progress=0, message=f"Unknown processor: {req.processor_type}")
                return
            cfg_cls = getattr(proc_cls, "default_config", None)
            if cfg_cls is None or not dataclasses.is_dataclass(cfg_cls):
                state._finish_job(job_id, status="error", progress=0, message="Processor has no config")
                return

            cfg = cfg_cls(workdir=folder, saved_job_path=str(job_file))
            state._jobs[job_id]["message"] = "Initializing processor…"
            saved_data = json.loads(job_file.read_text())
            pairs = [(j["step"], j["step"]) for j in saved_data.get("jobs", {}).values()]
            processor = proc_cls(pairs=pairs or [("_", "_")], config=cfg)

            if req.action == "refresh":
                import io as _io, contextlib as _cl, re as _re
                state._jobs[job_id]["message"] = "Refreshing job statuses…"
                buf = _io.StringIO()
                with _cl.redirect_stdout(buf):
                    processor.refresh()
                raw = buf.getvalue()
                # strip ANSI colour codes for plain-text GUI display
                clean = _re.sub(r'\x1b\[[0-9;]*m', '', raw).strip()
                state._finish_job(job_id, status="done", message=clean)

            elif req.action == "retry":
                state._jobs[job_id]["message"] = "Retrying failed pairs…"
                processor.retry()
                state._finish_job(job_id, status="done", message="Retry submitted. New job file saved.")

            elif req.action == "cancel":
                state._jobs[job_id]["message"] = "Cancelling jobs…"
                processor.cancel()
                state._finish_job(job_id, status="done", message="Cancel sent.")

            elif req.action == "force_steps":
                if not req.steps:
                    state._finish_job(job_id, status="error", progress=0, message="No steps selected.")
                    return
                is_hpc = getattr(processor.config, "hpc_mode", False) or any(
                    m.get("slurm_job_ids") or m.get("hpc_manager") or m.get("hpc_array")
                    for m in processor.jobs.values()
                )
                if is_hpc:
                    from insarhub.processor.isce_base import load_or_init_sbatch_options
                    per_step = load_or_init_sbatch_options(folder)
                    if per_step is None:
                        state._finish_job(
                            job_id, status="error", progress=0,
                            message=f"No sbatch_options.json found — created a default at "
                                    f"{folder / 'sbatch_options.json'}. Review the resource "
                                    f"settings for each step, then try again.")
                        return
                    processor.config.sbatch_options_per_step = per_step
                state._jobs[job_id]["message"] = f"Forcing step(s): {', '.join(req.steps)}…"
                processor.submit(steps=req.steps)
                state._finish_job(job_id, status="done",
                                  message=f"Forced {len(req.steps)} step(s) to (re)run: {', '.join(req.steps)}")

            else:
                state._finish_job(job_id, status="error", progress=0, message=f"Unknown action: {req.action}")

        except Exception as e:
            state._finish_job(job_id, status="error", progress=0, message=str(e))

    await asyncio.to_thread(run)


@router.get("/api/processor-steps")
async def get_processor_steps(processor: str):
    """List forceable step names for a local processor (e.g. ISCE_S1), for --step-style GUI submission."""
    if processor != "ISCE_S1":
        return {"steps": []}
    from insarhub.processor.isce_base import _SBATCH_DEFAULT_TEMPLATE
    steps = [
        f"run_{num}_{name}"
        for num, name in _SBATCH_DEFAULT_TEMPLATE["_steps"].items()
        if num != "17"  # SBAS is an analyzer step, not an ISCE_S1 processor step
    ]
    return {"steps": steps}


@router.get("/api/folder-pairs-files")
async def get_folder_pairs_files(path: str):
    """List stack/pairs JSON files in a folder for ISCE_S1 submit."""
    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    seen: set[str] = set()
    for pattern in ("stack_p*_f*.json", "pairs*.json", "*_pairs.json"):
        for f in folder.glob(pattern):
            seen.add(f.name)
    return {"files": sorted(seen)}


from insarhub.processor.isce_base import _SBATCH_DEFAULT_TEMPLATE


@router.get("/api/folder-sbatch-options")
async def get_sbatch_options(path: str):
    """Return sbatch_options.json, creating or upgrading to the full template (steps 01-16 + 17=SBAS)."""
    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    sbatch_path = folder / "sbatch_options.json"
    existing: dict = {}
    # Migrate from old srun_options.json if present
    old_path = folder / "srun_options.json"
    src = sbatch_path if sbatch_path.exists() else (old_path if old_path.exists() else None)
    if src:
        try:
            existing = json.loads(src.read_text())
        except Exception:
            pass
    # Rebuild in template order, preserving any user-set per-step flags
    merged = {}
    for k, v in _SBATCH_DEFAULT_TEMPLATE.items():
        if k.isdigit() or k in ("default",):
            merged[k] = existing.get(k, v)
        else:
            merged[k] = v
    sbatch_path.write_text(json.dumps(merged, indent=2))
    return {"content": sbatch_path.read_text()}


@router.post("/api/folder-sbatch-options")
async def save_sbatch_options(path: str, body: dict):
    """Save sbatch_options.json to the folder."""
    folder = Path(path).expanduser().resolve()
    if not folder.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    content = body.get("content", "")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid JSON: {exc}") from exc
    (folder / "sbatch_options.json").write_text(json.dumps(parsed, indent=2))
    return {"ok": True}
