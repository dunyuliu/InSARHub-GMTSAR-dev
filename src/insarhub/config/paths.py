"""
Workdir path layout definitions for each module family.

Each class takes a workdir Path and exposes sub-paths as properties.
Add a new class per satellite/processor family to keep paths centralized.

Usage:
    paths = Hyp3Paths(workdir)
    paths.output_dir        # workdir/hyp3
    paths.jobs_file         # workdir/hyp3_jobs.json
    paths.retry_file(ts)    # workdir/hyp3_retry_jobs_<ts>.json

    paths = ISCEPaths(workdir)
    paths.isce_dir              # workdir/isce
    paths.run_files_dir         # workdir/isce/run_files
    paths.step_log_dir("run_01")  # workdir/isce/run_files/run_01_logs
    paths.step_sbatch_dir("run_01")  # workdir/isce/run_files/run_01_sbatch
    paths.slc_dir               # workdir/slc
    paths.dem_dir               # workdir/dem

    paths = MintPyPaths(workdir)
    paths.mintpy_dir        # workdir/mintpy
    paths.tmp_dir           # workdir/mintpy/tmp
    paths.clip_dir          # workdir/mintpy/clip

    paths = StackPaths(workdir)
    paths.stack_dir(100, 466)                    # workdir/p100_f466
    paths.stack_file(100, 466)                   # workdir/p100_f466/stack_p100_f466.json
    paths.merge_tag([89, 90])                    # "merged_f89_f90"
    paths.merge_dir(87, [89, 90])                # workdir/p87_merged_f89_f90
    paths.dir_for(100, 466)                       # same as stack_dir — from a select_pairs() key
    paths.dir_for(87, "merged_f89_f90")           # workdir/p87_merged_f89_f90 — merge key variant
    paths.stack_file_for(100, 466)                # same as stack_file — from a select_pairs() key
    paths.stack_file_for(87, "merged_f89_f90")    # workdir/p87_merged_f89_f90/stack_p87_merged_f89_f90.json
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Hyp3Paths:
    """Path layout for HyP3 processor outputs (any satellite)."""
    workdir: Path

    @property
    def output_dir(self) -> Path:
        return self.workdir / "hyp3"

    @property
    def jobs_file(self) -> Path:
        return self.workdir / "hyp3_jobs.json"

    def retry_file(self, ts: str) -> Path:
        return self.workdir / f"hyp3_retry_jobs_{ts}.json"


@dataclass
class ISCEPaths:
    """Path layout for ISCE2 stackSentinel processor (any SAR satellite)."""
    workdir: Path

    @property
    def isce_dir(self) -> Path:
        return self.workdir / "isce"

    @property
    def run_files_dir(self) -> Path:
        return self.isce_dir / "run_files"

    def step_log_dir(self, step: str) -> Path:
        return self.run_files_dir / f"{step}_logs"

    def step_sbatch_dir(self, step: str) -> Path:
        return self.run_files_dir / f"{step}_sbatch"

    @property
    def slc_dir(self) -> Path:
        return self.workdir / "slc"

    @property
    def dem_dir(self) -> Path:
        return self.workdir / "dem"


@dataclass
class MintPyPaths:
    """Path layout for MintPy SBAS analyzer outputs (any SAR satellite)."""
    workdir: Path

    @property
    def mintpy_dir(self) -> Path:
        return self.workdir / "mintpy"

    @property
    def tmp_dir(self) -> Path:
        return self.mintpy_dir / "tmp"

    @property
    def clip_dir(self) -> Path:
        return self.mintpy_dir / "clip"


@dataclass
class StackPaths:
    """Path layout for downloader search / pair-selection output.

    One stack is (path, frame). A merge group combines every frame sharing
    one path into a single stack — its directory/file names encode every
    constituent frame number (merge_tag) so two independent merge groups on
    the same path never collide, and so the stack file always ends up
    co-located with wherever download(merge=True) put the SLCs.
    """
    workdir: Path

    @staticmethod
    def merge_tag(frames: list[int]) -> str:
        return "merged_" + "_".join(f"f{f}" for f in sorted(set(frames)))

    @staticmethod
    def is_merge_key(frame: int | str) -> bool:
        return isinstance(frame, str) and frame.startswith("merged")

    def stack_dir(self, path: int, frame: int) -> Path:
        return self.workdir / f"p{path}_f{frame}"

    def stack_file(self, path: int, frame: int) -> Path:
        return self.stack_dir(path, frame) / f"stack_p{path}_f{frame}.json"

    def merge_dir(self, path: int, frames: list[int]) -> Path:
        return self.workdir / f"p{path}_{self.merge_tag(frames)}"

    def dir_for(self, path: int, frame: int | str) -> Path:
        """Directory for a (path, frame) key from select_pairs() output —
        frame is either a plain frame number, or an already-computed merge
        tag (str starting with "merged", e.g. from merge_tag()). Handles
        both without the caller needing to know which one it has."""
        if self.is_merge_key(frame):
            return self.workdir / f"p{path}_{frame}"
        return self.stack_dir(path, frame)

    def stack_file_for(self, path: int, frame: int | str) -> Path:
        if self.is_merge_key(frame):
            return self.dir_for(path, frame) / f"stack_p{path}_{frame}.json"
        return self.stack_file(path, frame)
