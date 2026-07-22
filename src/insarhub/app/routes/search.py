# -*- coding: utf-8 -*-
"""Scene search, download, and AOI parsing endpoints."""

import asyncio
import base64
import dataclasses
import os
import tempfile
import threading as _threading
import uuid
from pathlib import Path

import geopandas as gpd
from shapely import wkt as shapely_wkt
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

import insarhub.app.state as state
from insarhub.app.models import (
    AddJobRequest, AddMergedJobRequest, DownloadMergedRequest,
    DownloadSceneRequest, JobResponse, ParseAoiRequest, SearchRequest,
)
from insarhub.app.state import _apply_config_from_dict, _new_job, _finish_job, write_insarhub_config
from insarhub.commands.downloader import DownloadScenesCommand, SearchCommand
from insarhub.config import S1_SLC_Config
from insarhub.config.paths import StackPaths
from insarhub.core.registry import Downloader

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_geojson(results: dict) -> dict:
    features = []
    for stack_key, scenes in results.items():
        for scene in scenes:
            try:
                features.append({
                    "type": "Feature",
                    "geometry":   scene.geometry,
                    "properties": {**scene.properties, "_stack": str(stack_key)},
                })
            except Exception:
                continue
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/api/downloader-schema")
async def downloader_schema(downloaderType: str = "S1_SLC"):
    """Return the extra search-filter fields the given downloader declares.

    The frontend renders "Additional Filters" / "Path and Frame Filters"
    generically from this list instead of hardcoding one downloader's fields —
    every downloader gets AOI/date/maxResults/granule-names for free and can
    layer on whatever else (platform, path/frame range, ...) makes sense for it.
    """
    dl_cls = Downloader._registry.get(downloaderType)
    if dl_cls is None:
        raise HTTPException(status_code=404, detail=f"Unknown downloader: {downloaderType}")
    return {"fields": getattr(dl_cls, "search_filter_schema", [])}


@router.post("/api/parse-granule-file")
async def parse_granule_file(file: UploadFile = File(...)):
    from insarhub.utils.tool import parse_scene_names_from_file
    suffix  = Path(file.filename or '').suffix.lower() or '.tmp'
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
        names = parse_scene_names_from_file(tmp_path)
        return {"names": names, "count": len(names)}
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.post("/api/search", response_model=JobResponse)
async def start_search(req: SearchRequest, background_tasks: BackgroundTasks):
    return state.launch_job(background_tasks, _run_search, str(uuid.uuid4()), req,
                            start_message="Starting search...")


async def _run_search(job_id: str, session_id: str, req: SearchRequest):
    def run():
        try:
            dl_cls = Downloader._registry.get(req.downloaderType)
            if dl_cls is None:
                _finish_job(job_id, status="error", progress=0,
                            message=f"Unknown downloader: {req.downloaderType}")
                return
            cfg_cls = getattr(dl_cls, "default_config", S1_SLC_Config)

            if req.granule_names:
                config = cfg_cls(workdir=req.workdir)
                _apply_config_from_dict(config, {"granule_names": req.granule_names}, skip_keys={"workdir"})
            else:
                intersects_with = req.wkt if req.wkt else (req.west, req.south, req.east, req.north)
                if isinstance(intersects_with, str):
                    try:
                        geom = shapely_wkt.loads(intersects_with)
                        for tol in (0.001, 0.005, 0.01, 0.05, 0.1):
                            simplified = geom.simplify(tol, preserve_topology=True)
                            if len(simplified.wkt) <= 2000:
                                break
                        intersects_with = simplified.wkt
                    except Exception:
                        pass

                config = cfg_cls(workdir=req.workdir)
                # Universal fields every downloader gets for free; anything else
                # (platform, path/frame range, ...) comes through req.overrides,
                # sourced from that downloader's own search_filter_schema.
                _apply_config_from_dict(config, {
                    "intersectsWith": intersects_with,
                    "start":          req.start,
                    "end":            req.end,
                    "maxResults":     req.maxResults,
                    "beamMode":       req.beamMode or None,
                    "polarization":   req.polarization or None,
                }, skip_keys={"workdir"})
                if req.overrides:
                    _apply_config_from_dict(config, req.overrides, skip_keys={"workdir"})

            downloader = Downloader.create(req.downloaderType, config)

            cmd    = SearchCommand(downloader, progress_callback=state._make_progress(job_id))
            result = cmd.run()

            if result.success:
                state._sessions[session_id] = downloader
                _finish_job(job_id, status="done", message=result.message, data={
                    "session_id": session_id,
                    "geojson":    _to_geojson(result.data),
                    "summary":    result.message,
                })
            else:
                _finish_job(job_id, status="error", progress=0, message=result.message)
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))

    await asyncio.to_thread(run)


@router.post("/api/download-scene", response_model=JobResponse)
async def download_single_scene(req: DownloadSceneRequest, background_tasks: BackgroundTasks):
    return state.launch_job(background_tasks, _run_download_scene, req,
                            start_message="Starting download...")


async def _run_download_scene(job_id: str, req: DownloadSceneRequest):
    def run(stop_ev):
        file_path = None
        try:
            import asf_search as asf
            from asf_search.download.download import _try_get_response

            workdir = Path(req.workdir)
            workdir.mkdir(parents=True, exist_ok=True)
            filename  = req.filename or req.url.rstrip("/").split("/")[-1].split("?")[0]
            file_path = workdir / filename
            state._jobs[job_id]["message"] = f"Downloading {filename}…"

            session   = asf.ASFSession()
            response  = _try_get_response(session=session, url=req.url)
            total_bytes = int(response.headers.get("content-length", 0))
            downloaded  = 0

            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if stop_ev.is_set():
                        response.close()
                        _finish_job(job_id, status="done", progress=0, message="Stopped.")
                        return
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_bytes:
                            pct = int(downloaded / total_bytes * 100)
                            state._jobs[job_id]["progress"] = pct
                            state._jobs[job_id]["message"]  = f"Downloading {filename}… {pct}%"

            _finish_job(job_id, status="done", message=f"Saved {filename}", data=str(file_path))
        except InterruptedError:
            _finish_job(job_id, status="done", progress=0, message="Stopped.")
        except Exception as e:
            if file_path and file_path.exists():
                file_path.unlink(missing_ok=True)
            _finish_job(job_id, status="error", progress=0, message=str(e))

    with state.stop_event(job_id) as stop_ev:
        await asyncio.to_thread(run, stop_ev)


@router.post("/api/download-stack", response_model=JobResponse)
async def download_stack(req: AddJobRequest, background_tasks: BackgroundTasks):
    job_id, _ = _new_job("Starting…")
    stop_ev = _threading.Event()
    state._stop_events[job_id] = stop_ev
    background_tasks.add_task(_run_download_stack, job_id, req, stop_ev)
    return {"job_id": job_id}


async def _run_download_stack(job_id: str, req: AddJobRequest, stop_ev: _threading.Event):
    def run():
        try:
            workdir = Path(req.workdir).expanduser().resolve()
            workdir.mkdir(parents=True, exist_ok=True)
            cfg = S1_SLC_Config(workdir=workdir)
            _apply_config_from_dict(cfg, state._settings.get("downloader_config", {}), skip_keys={"workdir"})
            _apply_config_from_dict(cfg, {
                "start": req.start, "end": req.end,
                "relativeOrbit": req.relativeOrbit, "frame": req.frame,
                "intersectsWith": req.wkt, "flightDirection": req.flightDirection,
                "platform": req.platform,
            })

            downloader    = Downloader.create("S1_SLC", cfg)
            state._jobs[job_id]["message"] = "Searching scenes…"
            search_result = SearchCommand(downloader).run()
            if not search_result.success:
                _finish_job(job_id, status="error", progress=0, message=search_result.message)
                return
            if stop_ev.is_set():
                _finish_job(job_id, status="done", progress=0, message="Stopped.")
                return

            total = sum(len(v) for v in downloader.results.values())
            state._jobs[job_id]["message"] = f"Downloading 0/{total}"

            dl_result = DownloadScenesCommand(
                downloader, stop_event=stop_ev, on_progress=state._make_download_progress(job_id)
            ).run()
            save_dir  = workdir / f"p{req.relativeOrbit}_f{req.frame}"
            if stop_ev.is_set():
                _finish_job(job_id, status="done", progress=0, message="Stopped.")
            else:
                _finish_job(job_id, status="done" if dl_result.success else "error",
                            message=dl_result.message, data=str(save_dir))
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))
        finally:
            state._stop_events.pop(job_id, None)

    await asyncio.to_thread(run)


@router.post("/api/add-job")
async def add_job(req: AddJobRequest):
    workdir = Path(req.workdir).expanduser().resolve()
    subdir  = StackPaths(workdir).stack_dir(req.relativeOrbit, req.frame)
    subdir.mkdir(parents=True, exist_ok=True)

    dl_cls      = Downloader._registry.get(req.downloaderType)
    cfg_cls     = getattr(dl_cls, "default_config", S1_SLC_Config) if dl_cls else S1_SLC_Config
    cfg_instance = cfg_cls(workdir=subdir)
    _apply_config_from_dict(cfg_instance, state._settings.get("downloader_config", {}), skip_keys={"workdir"})
    _apply_config_from_dict(cfg_instance, {
        "start": req.start, "end": req.end,
        "relativeOrbit": req.relativeOrbit, "frame": req.frame,
        "intersectsWith": req.wkt, "flightDirection": req.flightDirection,
        "platform": req.platform,
    })
    cfg = state._cfg_dict(cfg_instance)
    write_insarhub_config(subdir, {"downloader": {"type": req.downloaderType, "config": cfg}})
    return {"path": str(subdir), "name": subdir.name}


@router.post("/api/add-merged-job")
async def add_merged_job(req: AddMergedJobRequest):
    """Create one job folder for multiple frames sharing a relative orbit (path),
    ready for search/select-pairs/download with merge=True from within it —
    same lightweight "just create the folder + config" semantics as add_job,
    just for a merge group instead of one stack."""
    if not req.stacks:
        raise HTTPException(status_code=422, detail="Provide at least one stack")
    paths = {s.relativeOrbit for s in req.stacks}
    if len(paths) > 1:
        raise HTTPException(
            status_code=422,
            detail=f"All stacks must share one relative orbit (path), got {sorted(paths)}. "
                   f"Different tracks have unrelated viewing geometry and cannot be merged.",
        )
    path    = next(iter(paths))
    frames  = [s.frame for s in req.stacks]
    workdir = Path(req.workdir).expanduser().resolve()
    subdir  = StackPaths(workdir).merge_dir(path, frames)
    subdir.mkdir(parents=True, exist_ok=True)

    dl_cls       = Downloader._registry.get(req.downloaderType)
    cfg_cls      = getattr(dl_cls, "default_config", S1_SLC_Config) if dl_cls else S1_SLC_Config
    cfg_instance = cfg_cls(workdir=subdir)
    _apply_config_from_dict(cfg_instance, state._settings.get("downloader_config", {}), skip_keys={"workdir"})
    first = req.stacks[0]
    _apply_config_from_dict(cfg_instance, {
        "start": min(s.start for s in req.stacks),
        "end":   max(s.end for s in req.stacks),
        "relativeOrbit": path,
        # frame intentionally omitted — spans multiple; select_pairs(merge=True)
        # resolves the real frame set from the search itself.
        "intersectsWith": first.wkt,
        "flightDirection": first.flightDirection,
        # platform intentionally omitted — a merged track can span a satellite
        # handover (e.g. Sentinel-1C → Sentinel-1D); using only the first
        # selected stack's platform would silently restrict every future
        # re-search on this folder to one satellite and starve the network.
    })
    cfg = state._cfg_dict(cfg_instance)
    write_insarhub_config(subdir, {"downloader": {"type": req.downloaderType, "config": cfg}})
    return {"path": str(subdir), "name": subdir.name}


@router.post("/api/download-orbit-stack", response_model=JobResponse)
async def download_orbit_stack(req: AddJobRequest, background_tasks: BackgroundTasks):
    return state.launch_job(background_tasks, _run_download_orbit_stack, req,
                            start_message="Starting orbit download…")


async def _run_download_orbit_stack(job_id: str, req: AddJobRequest):
    def run(stop_ev):
        try:
            workdir  = Path(req.workdir).expanduser().resolve()
            save_dir = workdir / f"p{req.relativeOrbit}_f{req.frame}"
            save_dir.mkdir(parents=True, exist_ok=True)
            cfg = S1_SLC_Config(workdir=save_dir)
            _apply_config_from_dict(cfg, state._settings.get("downloader_config", {}), skip_keys={"workdir"})
            _apply_config_from_dict(cfg, {
                "start": req.start, "end": req.end,
                "relativeOrbit": req.relativeOrbit, "frame": req.frame,
                "intersectsWith": req.wkt, "flightDirection": req.flightDirection,
                "platform": req.platform,
            })

            downloader    = Downloader.create("S1_SLC", cfg)
            state._jobs[job_id]["message"] = "Searching scenes…"
            search_result = SearchCommand(downloader, progress_callback=state._make_progress(job_id)).run()
            if not search_result.success:
                _finish_job(job_id, status="error", progress=0, message=search_result.message)
                return
            state._jobs[job_id]["message"] = "Downloading orbit files…"
            downloader.download_orbit(save_dir=str(save_dir), stop_event=stop_ev)
            if stop_ev.is_set():
                _finish_job(job_id, status="done", progress=0, message="Stopped.")
            else:
                _finish_job(job_id, status="done", message="Orbit files downloaded.")
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))

    with state.stop_event(job_id) as stop_ev:
        await asyncio.to_thread(run, stop_ev)


@router.post("/api/download-merged", response_model=JobResponse)
async def download_merged(req: DownloadMergedRequest, background_tasks: BackgroundTasks):
    if not req.stacks:
        raise HTTPException(status_code=422, detail="Provide at least one stack")
    job_id, _ = _new_job("Starting merged download…")
    stop_ev = _threading.Event()
    state._stop_events[job_id] = stop_ev
    background_tasks.add_task(_run_download_merged, job_id, req, stop_ev)
    return {"job_id": job_id}


async def _run_download_merged(job_id: str, req: DownloadMergedRequest, stop_ev: _threading.Event):
    def run():
        try:
            workdir = Path(req.workdir).expanduser().resolve()

            all_downloaders = []
            for i, spec in enumerate(req.stacks):
                if stop_ev.is_set():
                    _finish_job(job_id, status="done", progress=0, message="Stopped.")
                    return
                state._jobs[job_id]["message"] = (
                    f"Searching stack {i + 1}/{len(req.stacks)} "
                    f"(P{spec.relativeOrbit}/F{spec.frame})…"
                )
                cfg = S1_SLC_Config(workdir=workdir)
                _apply_config_from_dict(cfg, state._settings.get("downloader_config", {}), skip_keys={"workdir"})
                _apply_config_from_dict(cfg, {
                    "start": spec.start, "end": spec.end,
                    "relativeOrbit": spec.relativeOrbit, "frame": spec.frame,
                    "intersectsWith": spec.wkt, "flightDirection": spec.flightDirection,
                    # platform intentionally omitted — see add_merged_job for why
                    # (a frame's own scene history can span a satellite handover;
                    # spec.platform here is derived client-side from a single
                    # representative scene and must not gate the real search).
                })
                downloader    = Downloader.create(req.downloaderType, cfg)
                search_result = SearchCommand(downloader).run()
                if not search_result.success:
                    state._jobs[job_id]["message"] += f" [search failed: {search_result.message}]"
                    continue
                all_downloaders.append(downloader)

            if not all_downloaders:
                _finish_job(job_id, status="error", progress=0, message="All stack searches failed.")
                return

            # Merge all results into primary downloader
            primary = all_downloaders[0]
            for dl in all_downloaders[1:]:
                primary.results.update(dl.results)

            # Directory name must match what downloader.download(merge=True) itself
            # computes (path + every constituent frame number) — not a fixed
            # "merged" literal — so orbit files (saved explicitly below, bypassing
            # download_orbit()'s own merge-aware logic) land next to the SLCs.
            from insarhub.downloader.asf_base import _merge_frame_tag
            paths = {path for (path, _frame) in primary.active_results.keys()}
            if len(paths) > 1:
                _finish_job(
                    job_id, status="error", progress=0,
                    message=f"Merged download requires all stacks to share one "
                            f"relative orbit (path), got {sorted(paths)}.")
                return
            path = next(iter(paths))
            frames = [frame for (_path, frame) in primary.active_results.keys()]
            merged_dir = workdir / f"p{path}_{_merge_frame_tag(frames)}"

            if req.download_slc and not stop_ev.is_set():
                total = sum(len(v) for v in primary.results.values())
                state._jobs[job_id]["message"] = f"Downloading 0/{total} scenes → {merged_dir.name}/"

                dl_result = DownloadScenesCommand(
                    primary,
                    merge=True,
                    stop_event=stop_ev,
                    on_progress=state._make_download_progress(job_id),
                ).run()
                if not dl_result.success:
                    _finish_job(job_id, status="error", progress=0, message=dl_result.message)
                    return

            if req.download_orbit and not stop_ev.is_set():
                state._jobs[job_id]["message"] = f"Downloading orbit files → {merged_dir.name}/"
                primary.download_orbit(save_dir=str(merged_dir), stop_event=stop_ev)

            if stop_ev.is_set():
                _finish_job(job_id, status="done", progress=0, message="Stopped.")
            else:
                _finish_job(
                    job_id, status="done",
                    message=f"Merged download complete → {merged_dir.name}/",
                    data=str(merged_dir),
                )
        except Exception as e:
            _finish_job(job_id, status="error", progress=0, message=str(e))
        finally:
            state._stop_events.pop(job_id, None)

    await asyncio.to_thread(run)


@router.post("/api/parse-aoi")
async def parse_aoi(req: ParseAoiRequest):
    import json as _json
    suffix   = Path(req.filename).suffix.lower()
    tmp_path = None
    try:
        content = base64.b64decode(req.data)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        gdf = gpd.read_file(tmp_path)
        if gdf.empty:
            raise HTTPException(status_code=422, detail="No features found in file")
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
        feature = _json.loads(gdf.iloc[[0]].to_json())["features"][0]
        return {"feature": feature}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
