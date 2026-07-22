"""
Real end-to-end pipeline: ISCE2/topsStack -> MintPy, via direct Python API
calls (no CLI subprocess involved) -- the library-import equivalent of
cli_e2e_isce_mintpy.sh.

Runs the actual insarhub library against real infrastructure, no mocking:
  - downloads real Sentinel-1 SLC scenes (~4GB each)
  - runs real ISCE2 stackSentinel processing for every real selected pair
  - runs ISCE_SBAS.prep_data() for real (real path discovery)
  - runs the real MintPy time-series workflow

Operates on p100_f466/ (repo root) by default -- a real S1 SLC stack
(path 100 / frame 466) with 27 already-selected real pairs and 13 real
scenes (p100_f466/stack_p100_f466.json).

Prerequisites -- see cli_e2e_isce_mintpy.sh for full details:
  - ~/.netrc (or ~/.credit_pool) with Earthdata credentials
  - A real ISCE2 + topsStack install (this repo's environment-isce2.yml):
        mamba env create -f environment-isce2.yml -n insarhub_isce
        conda activate insarhub_isce
        pip install -e .
  - ~50GB+ disk for the SLCs, plus processing intermediates
  - Time: real ISCE2 processing of 27 pairs on a single (non-HPC) machine
    can take many hours to multiple days.

Usage (run from the repo root, with the insarhub_isce env active):
    conda activate insarhub_isce
    python test/e2e/api_e2e_isce_mintpy.py [workdir]

This file is NOT auto-run by pytest (no test_ prefix, and everything lives
behind `if __name__ == "__main__":`) -- invoke it directly when you actually
want real ISCE2 processing to run. Safe to re-run: SLC download skips
already-complete files; ISCE_S1.submit() skips any run-file step already
marked SUCCEEDED (skip_existing, on by default).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _p100_f466_stack import real_date_pairs  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def run_pipeline(workdir: Path) -> None:
    try:
        import isce  # noqa: F401
    except ImportError:
        raise SystemExit(
            "[ERROR] Real ISCE2 is not importable in the active Python environment.\n"
            "        conda activate insarhub_isce\n"
            "        (build it first: mamba env create -f environment-isce2.yml -n insarhub_isce)"
        )

    from insarhub.config import S1_SLC_Config, ISCE_S1_Config, ISCE_SBAS_Config
    from insarhub.downloader.s1_slc import S1_SLC
    from insarhub.processor.isce_s1 import ISCE_S1
    from insarhub.analyzer.isce_sbas import ISCE_SBAS

    cfg_path = workdir / "insarhub_config.json"
    if not cfg_path.exists():
        raise SystemExit(
            f"[ERROR] {cfg_path} not found. Run search + select_pairs first."
        )

    print("== Stage 1/4: Download real Sentinel-1 SLC scenes " + "=" * 28)
    dl_cfg_dict = json.loads(cfg_path.read_text())["downloader"]["config"]
    dl_cfg = S1_SLC_Config(workdir=str(workdir), **dl_cfg_dict)
    downloader = S1_SLC(dl_cfg)
    downloader.search()
    downloader.download(max_workers=4)  # skips scenes already downloaded at full size

    print("== Stage 2/4: Submit real ISCE_S1 processing (runs detached in background) " + "=" * 5)
    pairs = real_date_pairs()  # all 27 real selected pairs for p100_f466
    proc = ISCE_S1(pairs=pairs, config=ISCE_S1_Config(workdir=str(workdir)))
    proc.submit()  # skip_existing (default True) makes re-running this safe

    print("== Stage 3/4: Watch real ISCE2 processing to completion " + "=" * 22)
    proc.watch()

    print("== Stage 4/4: ISCE_SBAS prep_data + real MintPy run " + "=" * 26)
    analyzer = ISCE_SBAS(ISCE_SBAS_Config(workdir=str(workdir)))
    analyzer.prep_data()
    analyzer.run()

    print(f"Done. MintPy outputs are under {workdir / 'mintpy'}/")


if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO_ROOT / "p100_f466"
    run_pipeline(target)
