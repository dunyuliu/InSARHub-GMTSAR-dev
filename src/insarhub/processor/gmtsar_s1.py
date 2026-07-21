# -*- coding: utf-8 -*-
"""
GMTSAR_S1 — Sentinel-1 InSAR processor backed by GMTSAR's Python
p2p_processing / p2p_S1_TOPS_Frame pipelines.

STATUS: v1, real S1_TOPS pairs signature (2026-07-21). Supersedes the v0
draft that copied ISCE_S1's plain-date-tuple `pairs=[(ref,sec),...]`
convention without checking whether it matched GMTSAR's actual CLI
contract for S1_TOPS -- it didn't. See docs/gmtsar_s1_notes/OPEN_ISSUES.md
for that finding's full writeup.

Two distinct GMTSAR entry points, selected via config.frame_mode:

  frame_mode=False (default) -- single-subswath, via p2p_processing.
    pairs = [(ref_stem, sec_stem), ...], raw per-subswath product
    basename stems (confirmed against a real recipe,
    gmtsar/python/tests/recipes/README_S1_Ridgecrest_EQ.txt):
        p2p_processing S1_TOPS \
          s1a-iw2-slc-vv-20190704t135158-20190704t135223-027968-032877-005 \
          s1a-iw2-slc-vv-20190716t135159-20190716t135224-028143-032dc3-005 \
          config.py
    One shared case_dir for the whole pairs list -- p2p_processing's own
    output (intf/<ref>_<sec>/) is pair-namespaced, so concurrent pairs
    don't collide.

  frame_mode=True -- multi-subswath Frame, via p2p_S1_TOPS_Frame.
    pairs = [(ref_safe, ref_eof, sec_safe, sec_eof), ...] -- .SAFE
    directory names + matching .EOF orbit filenames (confirmed against
    gmtsar/python/utils/p2p_S1_TOPS_Frame's own usage string and a real
    recipe, tests/recipes/README_S1A_SLC_TOPS_LA.txt):
        p2p_S1_TOPS_Frame Master.SAFE Master.EOF Aligned.SAFE Aligned.EOF \
          config.py vv 1
    p2p_S1_TOPS_Frame is NOT pair-namespaced -- it always writes
    F1/F2/F3/merge/ into its current working directory (confirmed by
    tracing the script: no per-pair subdirectory logic exists). So each
    pair gets its OWN case subdirectory (case_dir/<ref>_<sec>/), not a
    shared one -- otherwise pair 2 would silently overwrite pair 1's
    merge/ output.

Interface mirrors ISCE_S1 / Hyp3_S1:
  submit()  -- stage case dir(s), launch the right GMTSAR entry point per
               pair (subprocess), up to max_workers concurrent.
  refresh() -- read per-pair status, print a table.
  retry()   -- re-run failed pairs (and only failed pairs).
  watch()   -- poll until every pair is SUCCEEDED or FAILED.
  save()    -- persist gmtsar_jobs.json, matching ISCE_S1's isce_jobs.json.

Why no custom output-normalization step (frame_mode=False only -- see
KNOWN GAP below for frame_mode=True): GMTSAR's native per-pair output
directory (intf/<ref>_<sec>/ -- corr_ll.grd, phasefilt_ll.grd,
unwrap_ll.grd, two numeric-named *.PRM files) is exactly what MintPy's
own prep_gmtsar.py already expects (confirmed by reading
mintpy/prep_gmtsar.py directly: it globs `{fbase}_ll*.grd` and digit-named
`*.PRM` files). KNOWN GAP: frame_mode=True's real output lands in
merge/ with the same file basenames but has NOT been checked against
prep_gmtsar.py's directory-discovery logic -- needs a real Frame-mode
run + a prep_gmtsar.py dry run before that claim can be made for Frame
mode too.

Deliberately kept as a subprocess-per-pair design, not in-process Python
calls into GMTSAR's own p2p_stages.py: (1) InSARHub and GMTSAR run in
separate conda environments with different numpy/GDAL stacks -- importing
GMTSAR's stage code in-process risks real dependency collisions; (2) most
wall-clock is spent in C binaries (gmt, snaphu) either way, so in-process
Python orchestration wouldn't meaningfully speed anything up; (3) this
matches both ISCE_S1's own external-process pattern AND GMTSAR's own test
harness (case_runner.py), which deliberately runs each case in its own
subprocess for process-group isolation.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

from insarhub.config import GMTSAR_S1_Config
from insarhub.core import LocalProcessor

logger = logging.getLogger(__name__)

_PENDING = "PENDING"
_RUNNING = "RUNNING"
_SUCCEEDED = "SUCCEEDED"
_FAILED = "FAILED"

JOBS_FILE = "gmtsar_jobs.json"

# GMTSAR's own multi-sensor coverage (see gmtsar/python/tests/cases.py
# upstream in the GMTSAR repo) -- GMTSAR_S1 only exercises S1_TOPS today,
# but every one of these is already a real, tested SAT argument to
# p2p_processing. Listed here so a future GMTSAR_<sensor> processor
# doesn't have to re-derive this from scratch.
SUPPORTED_SATS = (
    "ERS", "ENVI", "ENVI_SLC", "ALOS", "ALOS_SLC", "ALOS2", "ALOS2_SCAN",
    "S1_STRIP", "S1_TOPS", "CSK_RAW", "CSK_SLC", "TSX", "RS2", "GF3",
)

# Two pair shapes, one for each GMTSAR entry point.
SubswathPair = tuple           # (ref_stem, sec_stem) -- frame_mode=False
FramePair = tuple              # (ref_safe, ref_eof, sec_safe, sec_eof) -- frame_mode=True


def _pair_key(pair: tuple) -> str:
    if len(pair) == 2:
        ref, sec = pair
        return f"{ref}_{sec}"
    ref_safe, _ref_eof, sec_safe, _sec_eof = pair
    # .SAFE names are long; use just the date-ish middle segment where
    # possible, else fall back to the full stems joined.
    return f"{ref_safe}_{sec_safe}"


def _read_status(status_dir: Path) -> str:
    if (status_dir / ".succeeded").exists():
        return _SUCCEEDED
    if (status_dir / ".failed").exists():
        return _FAILED
    if status_dir.exists():
        return _RUNNING
    return _PENDING


def _write_status(status_dir: Path, status: str) -> None:
    status_dir.mkdir(parents=True, exist_ok=True)
    for name in (".succeeded", ".failed"):
        f = status_dir / name
        if f.exists():
            f.unlink()
    if status == _SUCCEEDED:
        (status_dir / ".succeeded").touch()
    elif status == _FAILED:
        (status_dir / ".failed").touch()


class GMTSAR_S1(LocalProcessor):
    """Sentinel-1 InSAR processor backed by GMTSAR.

    Usage, single-subswath (mirrors ISCE_S1's own docstring example)::

        from insarhub.processor import GMTSAR_S1
        from insarhub.config import GMTSAR_S1_Config

        proc = GMTSAR_S1(
            pairs  = [("s1a-iw2-slc-vv-20190704t135158-20190704t135223-027968-032877-005",
                       "s1a-iw2-slc-vv-20190716t135159-20190716t135224-028143-032dc3-005")],
            config = GMTSAR_S1_Config(
                workdir   = '/data/stack',
                slc_dir   = '/data/slcs',
                orbit_dir = '/data/orbits',
                dem_path  = '/data/dem.grd',
            ),
        )
        proc.submit()
        proc.watch()

    Usage, multi-subswath Frame::

        proc = GMTSAR_S1(
            pairs  = [("S1A_IW_SLC__1SSV_20150526T014935_20150526T015002_006086_007E23_679A.SAFE",
                       "S1A_OPER_AUX_POEORB_OPOD_20150627T155155_V20150606T225944_20150608T005944.EOF",
                       "S1A_IW_SLC__1SDV_20150607T014936_20150607T015003_006261_00832E_3626.SAFE",
                       "S1A_OPER_AUX_POEORB_OPOD_20150615T155109_V20150525T225944_20150527T005944.EOF")],
            config = GMTSAR_S1_Config(
                workdir = '/data/stack', slc_dir = '/data/slcs',
                orbit_dir = '/data/orbits', dem_path = '/data/dem.grd',
                frame_mode = True,
            ),
        )
        proc.submit()
        proc.watch()
    """

    name = "GMTSAR_S1"
    description = (
        "Sentinel-1 InSAR via GMTSAR (p2p_processing or p2p_S1_TOPS_Frame). "
        "Requires GMTSAR installed (gmtsar/python/install.py) and GMTSAR "
        "env vars set. Single-subswath output is directly consumable by "
        "MintPy's prep_gmtsar.py -- Frame-mode output not yet verified, "
        "see module docstring."
    )
    compatible_downloader = "S1_SLC"
    default_config = GMTSAR_S1_Config

    def __init__(
        self,
        pairs: list[Union[SubswathPair, FramePair]],
        config: GMTSAR_S1_Config | None = None,
    ):
        super().__init__(config)
        self.config: GMTSAR_S1_Config = self.config or GMTSAR_S1_Config()
        if not pairs and not getattr(self, "jobs", None):
            raise ValueError(
                "pairs must be non-empty: 2-tuples (ref_stem, sec_stem) if "
                "frame_mode=False, or 4-tuples (ref_safe, ref_eof, sec_safe, "
                "sec_eof) if frame_mode=True."
            )
        expected_len = 4 if self.config.frame_mode else 2
        for p in pairs:
            if len(p) != expected_len:
                raise ValueError(
                    f"config.frame_mode={self.config.frame_mode} expects "
                    f"{expected_len}-tuples, got a {len(p)}-tuple: {p!r}"
                )
        self.pairs = pairs
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  Paths                                                              #
    # ------------------------------------------------------------------ #

    @property
    def workdir(self) -> Path:
        return Path(self.config.workdir)

    @property
    def case_dir(self) -> Path:
        """Shared GMTSAR case directory -- used directly when
        frame_mode=False (p2p_processing is pair-namespaced via
        intf/<ref>_<sec>/). When frame_mode=True this is only the PARENT
        of each pair's own subdirectory; see pair_case_dir().
        """
        return self.workdir / "gmtsar_case"

    def pair_case_dir(self, pair: tuple) -> Path:
        """The directory a given pair's p2p_* invocation actually runs
        from. Shared case_dir for frame_mode=False; a dedicated
        per-pair subdirectory for frame_mode=True (p2p_S1_TOPS_Frame's
        F1/F2/F3/merge/ output is not itself pair-namespaced)."""
        if self.config.frame_mode:
            return self.case_dir / _pair_key(pair)
        return self.case_dir

    def _status_dir(self, pair: tuple) -> Path:
        if self.config.frame_mode:
            # p2p_S1_TOPS_Frame's real product lives in merge/; use that
            # directory's existence/markers as the status signal.
            return self.pair_case_dir(pair) / "merge"
        ref, sec = pair
        return self.case_dir / "intf" / f"{ref}_{sec}"

    # ------------------------------------------------------------------ #
    #  Case staging (the InSARHub <-> GMTSAR directory-convention bridge) #
    # ------------------------------------------------------------------ #

    def _symlink_dir_contents(self, src: Path, dest_dir: Path) -> None:
        """Symlink every entry in src into dest_dir, by name.

        Found via a real end-to-end test against messy real data: broken
        symlinks (e.g. a stray self-referencing .EOF left over from an
        unrelated process) must be skipped with a warning, not crash the
        whole staging step -- `Path.resolve()` raises RuntimeError on a
        symlink loop, and a single bad entry in a large real data
        directory shouldn't take down staging for every other pair.
        """
        for f in src.glob("*"):
            dest = dest_dir / f.name
            if dest.exists() or dest.is_symlink():
                continue
            try:
                target = f.resolve(strict=True)
            except (RuntimeError, OSError) as exc:
                logger.warning("skipping unresolvable entry %s (%s)", f, exc)
                continue
            dest.symlink_to(target)

    def _stage_one_case_dir(self, target: Path) -> None:
        """Populate target/{raw,topo}/ and target/config.py, matching what
        GMTSAR's own case.setup / p2p_config would produce for a
        manually-run case.

        KNOWN GAP: this assumes slc_dir already contains files
        p2p_processing / p2p_S1_TOPS_Frame can consume directly (raw
        .SAFE dirs plus .EOF orbits placed in raw/ -- confirmed this
        matches p2p_processing's own P2P1Preprocess, which calls
        `pre_proc SAT master aligned` internally on raw/ input, so raw
        .SAFE-derived files are the right thing to stage, NOT
        pre-focused SLCs). DEM auto-download from a bbox (dem_path=None)
        is NOT implemented -- config.dem_path must point at an existing
        GMTSAR-format DEM (topo/dem.grd) for now.
        """
        cfg = self.config
        target.mkdir(parents=True, exist_ok=True)
        raw_dir = target / "raw"
        topo_dir = target / "topo"
        raw_dir.mkdir(exist_ok=True)
        topo_dir.mkdir(exist_ok=True)

        if cfg.slc_dir and str(cfg.slc_dir) not in ("auto", ""):
            self._symlink_dir_contents(Path(cfg.slc_dir), raw_dir)

        if cfg.orbit_dir and str(cfg.orbit_dir) not in ("auto", ""):
            self._symlink_dir_contents(Path(cfg.orbit_dir), raw_dir)

        if cfg.dem_path:
            dest = topo_dir / "dem.grd"
            if not dest.exists():
                dest.symlink_to(Path(cfg.dem_path).resolve())
        else:
            raise NotImplementedError(
                "GMTSAR_S1_Config.dem_path is required in this version -- "
                "bbox-driven DEM auto-fetch (matching ISCE_S1's GLO-30 "
                "download) is not implemented yet."
            )

        config_py = target / "config.py"
        if not config_py.exists():
            if cfg.config_template:
                import shutil
                shutil.copy(cfg.config_template, config_py)
            else:
                subprocess.run(
                    ["pop_config", cfg.sat],
                    cwd=str(target),
                    check=True,
                    env=self._subprocess_env(),
                )

    def _stage_case(self) -> None:
        if not self.config.frame_mode:
            self._stage_one_case_dir(self.case_dir)
            return
        # Frame mode: one case dir PER PAIR, each independently staged
        # (raw/topo/config.py symlinked/copied per pair). Slightly more
        # I/O than sharing one case_dir, but required for correctness --
        # see pair_case_dir()'s docstring.
        for pair in self.pairs:
            self._stage_one_case_dir(self.pair_case_dir(pair))

    # ------------------------------------------------------------------ #
    #  LocalProcessor interface                                          #
    # ------------------------------------------------------------------ #

    def submit(self) -> dict:
        self._stage_case()

        pending = []
        for pair in self.pairs:
            key = _pair_key(pair)
            status = _read_status(self._status_dir(pair))
            if status == _SUCCEEDED and self.config.skip_existing:
                logger.info("%s already succeeded, skipping.", key)
                self.jobs[key] = self._job_meta(pair, _SUCCEEDED)
                continue
            _write_status(self._status_dir(pair), _PENDING)
            self.jobs[key] = self._job_meta(pair, _PENDING)
            pending.append(pair)

        if self.config.dry_run:
            logger.info("dry_run: would submit %d pair(s): %s", len(pending), pending)
            return self.jobs

        thread = threading.Thread(target=self._run_pairs, args=(pending,), daemon=True)
        thread.start()
        self._thread = thread
        self.save()
        return self.jobs

    def _run_pairs(self, pairs: list[tuple]) -> None:
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
            futures = {pool.submit(self._run_one_pair, pair): pair for pair in pairs}
            for fut in as_completed(futures):
                pair = futures[fut]
                key = _pair_key(pair)
                try:
                    ok = fut.result()
                except Exception:
                    logger.exception("pair %s raised", key)
                    ok = False
                status = _SUCCEEDED if ok else _FAILED
                _write_status(self._status_dir(pair), status)
                with self._lock:
                    self.jobs[key]["status"] = status
                self.save()

    def _subprocess_env(self) -> dict:
        """Build the environment GMTSAR subprocess calls actually need.

        REAL BUG FOUND in end-to-end testing (2026-07-21): GMTSAR's own
        Python stages (e.g. dem2topo_ra) shell out to the standalone `gmt`
        binary, which is NOT provided by GMTSAR's own bin/ directory --
        it comes from the conda environment GMTSAR was installed into
        (conda-forge's gmt package). InSARHub runs in its OWN separate
        conda environment (different numpy/GDAL stack, deliberately --
        see this module's docstring), which does not have `gmt` on PATH
        at all. Confirmed directly: `which gmt` inside InSARHub's env
        returns nothing; dem2topo_ra then fails in ~1s (not the tens of
        seconds a real DEM interpolation takes) because the `gmt`
        subprocess it shells out to doesn't exist.

        So this can't rely on inheriting the caller's PATH -- it must
        build an explicit PATH prepending both gmtsar_root/bin (GMTSAR's
        own scripts) and gmtsar_env_bin (the conda env providing `gmt`,
        numba, scipy, etc.), regardless of what environment the InSARHub
        process itself happens to be running under.
        """
        import os
        cfg = self.config
        env = os.environ.copy()
        prepend = []
        if cfg.gmtsar_env_bin:
            prepend.append(str(cfg.gmtsar_env_bin))
        if cfg.gmtsar_root:
            env["GMTSAR"] = str(cfg.gmtsar_root)
            prepend.append(str(Path(cfg.gmtsar_root) / "bin"))
        if prepend:
            env["PATH"] = ":".join(prepend) + ":" + env.get("PATH", "")
        return env

    def _run_one_pair(self, pair: tuple) -> bool:
        key = _pair_key(pair)
        with self._lock:
            self.jobs[key]["status"] = _RUNNING
        run_dir = self.pair_case_dir(pair)
        log_path = run_dir / "p2p.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._build_cmd(pair)
        with open(log_path, "w") as log_f:
            proc = subprocess.run(
                cmd, cwd=str(run_dir), stdout=log_f, stderr=subprocess.STDOUT,
                env=self._subprocess_env(),
            )
        return proc.returncode == 0

    def _build_cmd(self, pair: tuple) -> list[str]:
        cfg = self.config
        if not cfg.frame_mode:
            ref, sec = pair
            return ["p2p_processing", cfg.sat, ref, sec, "config.py"]
        ref_safe, ref_eof, sec_safe, sec_eof = pair
        return [
            "p2p_S1_TOPS_Frame", ref_safe, ref_eof, sec_safe, sec_eof,
            "config.py", cfg.polarization, "1" if cfg.parallel else "0",
        ]

    def refresh(self) -> dict:
        for pair in self.pairs:
            key = _pair_key(pair)
            status = _read_status(self._status_dir(pair))
            if key in self.jobs:
                self.jobs[key]["status"] = status
        self._print_table()
        return self.jobs

    def retry(self) -> dict:
        failed = [p for p in self.pairs if _read_status(self._status_dir(p)) == _FAILED]
        if not failed:
            logger.info("No failed pairs to retry.")
            return self.jobs
        for pair in failed:
            _write_status(self._status_dir(pair), _PENDING)
        thread = threading.Thread(target=self._run_pairs, args=(failed,), daemon=True)
        thread.start()
        self._thread = thread
        return self.jobs

    def watch(self, poll_interval: float = 10.0) -> dict:
        import time
        while True:
            self.refresh()
            statuses = {j["status"] for j in self.jobs.values()}
            if statuses <= {_SUCCEEDED, _FAILED}:
                break
            time.sleep(poll_interval)
        return self.jobs

    def save(self) -> None:
        jobs_path = self.case_dir / JOBS_FILE
        jobs_path.parent.mkdir(parents=True, exist_ok=True)
        jobs_path.write_text(json.dumps(self.jobs, indent=2))

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    def _job_meta(self, pair: tuple, status: str) -> dict:
        return {
            "pair": list(pair),
            "status": status,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }

    def _print_table(self) -> None:
        for key, meta in self.jobs.items():
            print(f"  {key:24s} {meta['status']}")
