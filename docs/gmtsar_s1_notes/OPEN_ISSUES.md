# GMTSAR_S1 processor — open issues found by tracing real GMTSAR source

Tracked separately from code comments so they're easy to scan before the
next work session. Update/remove entries as they're resolved.

## RESOLVED: pre-processing gap (was "biggest open unknown")

Traced `p2p_stages.py::P2P1Preprocess` in the GMTSAR repo directly. It
calls `pre_proc SAT master aligned` internally on files in `raw/` before
any interferometry step runs. So `p2p_processing` DOES handle raw input
-> focused SLC/PRM preprocessing itself -- `_stage_case()`'s assumption
(stage raw files into `raw/`, don't pre-focus them) is correct as
written. No fix needed for this one.

## RESOLVED: `pairs=[(ref, sec), ...]` signature was wrong for S1_TOPS

Fixed 2026-07-21. `GMTSAR_S1` now supports BOTH real GMTSAR entry points,
selected via `config.frame_mode`:

- `frame_mode=False` (default): single-subswath `p2p_processing`, pairs =
  `[(ref_stem, sec_stem), ...]` -- raw per-subswath product basename
  stems (Sentinel-1's own naming:
  `s1a-iw<N>-slc-<pol>-<start>-<end>-<orbit>-<mission>-<swath>`), NOT
  plain `YYYYMMDD` dates.
- `frame_mode=True`: multi-subswath `p2p_S1_TOPS_Frame`, pairs =
  `[(ref_safe, ref_eof, sec_safe, sec_eof), ...]` -- .SAFE directory
  names + matching .EOF orbit filenames.

Verified: `proc._build_cmd(pair)` for both modes produces a command
**identical** to the real recipe lines in
`gmtsar/python/tests/recipes/README_S1_Ridgecrest_EQ.txt`, confirmed by
direct comparison, not just code review.

Frame mode uses a per-pair case subdirectory (`case_dir/<ref>_<sec>/`),
not one shared case_dir, because `p2p_S1_TOPS_Frame`'s output
(`F1/F2/F3/merge/`) is not itself pair-namespaced -- confirmed by
tracing the script (no per-pair subdirectory logic exists in it).

## RESOLVED: real end-to-end run revealed a genuine environment bug (not a logic bug)

Ran `GMTSAR_S1` for real (both modes) against real cached S1_Ridgecrest_EQ
data on 2026-07-21. Both runs got well past staging into actual multi-stage
GMTSAR processing (single-subswath mode ran preprocessing/alignment
successfully before failing; Frame mode successfully processed all three
subswaths' SLC/topo stages before failing) -- proving the pairs-signature
fix and staging logic are structurally correct.

The actual failure: GMTSAR's own Python stages (e.g. `dem2topo_ra`) shell
out to the standalone `gmt` binary, which is NOT provided by GMTSAR's own
`bin/` -- it comes from the specific conda environment GMTSAR was
installed into. InSARHub runs in its own separate conda env (different
numpy/GDAL stack, deliberately), which doesn't have `gmt` on PATH at all.
Confirmed directly (`which gmt` inside the InSARHub env: not found), and
confirmed the fix works (re-running `dem2topo_ra` manually with the
correct env active: it actually computes, instead of failing in ~1s).

**Fixed**: `GMTSAR_S1_Config` gained `gmtsar_root` and `gmtsar_env_bin`
fields. `_subprocess_env()` builds an explicit environment for every
GMTSAR subprocess call (both `pop_config` and the real `p2p_*` calls),
prepending both paths to PATH regardless of what environment the calling
InSARHub process itself is running under. This is NOT optional
configuration -- without it, every GMTSAR subprocess call fails near
instantly with no useful error surfaced to the caller (just a nonzero
GMTSAR-internal exit code buried in a per-pair log file).

**Real end-to-end validation is IN PROGRESS as of this commit** -- a
freshly-launched real run (Frame mode, with the environment fix, against
the same real S1_Ridgecrest_EQ data) is running in the background. This
file will be updated with the real result (success or the next real bug
found) once it completes -- do not trust "should work now" without that
confirmation landing first.
