"""
Real end-to-end pipeline: HyP3 -> MintPy, via direct Python API calls (no CLI
subprocess involved) -- the library-import equivalent of cli_e2e_hyp3_mintpy.sh.

Runs the actual insarhub library against real infrastructure, no mocking:
  - submits real InSAR_GAMMA jobs to HyP3 (consumes real processing credits
    on your Earthdata account)
  - watches HyP3 for real job status and downloads the real result ZIPs
  - runs Hyp3_SBAS.prep_data() for real (real rasterio clip/overlap)
  - runs the real MintPy time-series workflow

Operates on p100_f466/ (repo root) by default -- a real S1 SLC stack
(path 100 / frame 466) with 27 already-selected real pairs
(p100_f466/stack_p100_f466.json), so no search/select_pairs step is needed.

Prerequisites -- see cli_e2e_hyp3_mintpy.sh for full details:
  - ~/.netrc (or ~/.credit_pool) with Earthdata credentials
  - insarhub installed with MintPy (pip install insarhub[mintpy])
  - (optional) ~/.cdsapirc for the correct_troposphere MintPy step

Usage (run from the repo root):
    python test/e2e/api_e2e_hyp3_mintpy.py [workdir]

This file is NOT auto-run by pytest (no test_ prefix, and everything lives
behind `if __name__ == "__main__":`) -- invoke it directly when you actually
want to submit real jobs. Safe to re-run: submission is skipped (resumes
from the saved job file instead) if hyp3_jobs.json already exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _p100_f466_stack import real_pairs  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def run_pipeline(workdir: Path) -> None:
    from insarhub.config import Hyp3_S1_Config, Hyp3_SBAS_Config
    from insarhub.processor.hyp3_s1 import Hyp3_S1
    from insarhub.analyzer.hyp3_sbas import Hyp3_SBAS

    cfg_path = workdir / "insarhub_config.json"
    if not cfg_path.exists():
        raise SystemExit(
            f"[ERROR] {cfg_path} not found. Run search + select_pairs first."
        )

    jobs_path = workdir / "hyp3_jobs.json"

    print("== Stage 1/3: Submit real HyP3 jobs " + "=" * 46)
    if jobs_path.exists():
        print(f"  {jobs_path} already exists — resuming from it instead of "
              f"re-submitting (would duplicate real jobs/credits).")
        proc = Hyp3_S1(Hyp3_S1_Config(workdir=str(workdir), saved_job_path=str(jobs_path)))
    else:
        pairs = real_pairs()  # all 27 real selected pairs for p100_f466
        proc = Hyp3_S1(Hyp3_S1_Config(workdir=str(workdir), pairs=pairs))
        proc.submit()
        proc.save()

    print("== Stage 2/3: Watch real jobs to completion + download real results " + "=" * 15)
    # Polls every 5 min (default) until every job is SUCCEEDED/FAILED,
    # downloading each job's real output ZIP as it finishes.
    proc.watch()

    print("== Stage 3/3: Hyp3_SBAS prep_data + real MintPy run " + "=" * 30)
    analyzer = Hyp3_SBAS(Hyp3_SBAS_Config(workdir=str(workdir)))
    analyzer.prep_data()
    # troposphericDelay_method defaults to 'pyaps', which needs a CDS API
    # token (~/.cdsapirc) for the correct_troposphere step -- you'll be
    # prompted interactively the first time if it's not already set up.
    analyzer.run()

    print(f"Done. MintPy outputs are under {workdir / 'mintpy'}/")


if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO_ROOT / "p100_f466"
    run_pipeline(target)
