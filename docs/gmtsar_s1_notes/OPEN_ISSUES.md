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

## OPEN, real bug: `pairs=[(ref, sec), ...]` signature is wrong for S1_TOPS

Traced a real recipe (`gmtsar/python/tests/recipes/README_S1_Ridgecrest_EQ.txt`):

    p2p_processing S1_TOPS s1a-iw2-slc-vv-20190704t135158-20190704t135223-027968-032877-005 \
                            s1a-iw2-slc-vv-20190716t135159-20190716t135224-028143-032dc3-005 config.py

For `S1_TOPS`, the `master`/`aligned` CLI arguments are **raw per-subswath
product basename stems** (Sentinel-1's own TIFF/annotation naming:
`s1a-iw<N>-slc-<pol>-<start>-<end>-<orbit>-<mission>-<swath>`), not plain
`YYYYMMDD` dates. `GMTSAR_S1.__init__(pairs=[("20200101","20200113"), ...])`
copied ISCE_S1's date-tuple convention without checking this -- it's
wrong for GMTSAR. `p2p_stages.py::renameMasterAlignedForS1tops` parses
date/time/frame out of *fixed character positions* in these stems
internally, so a bare date string would not parse correctly.

There's also a SECOND, different entry point for multi-subswath Frame
cases (`p2p_S1_TOPS_Frame`, not `p2p_processing`), with a completely
different signature: `Master.SAFE Master.EOF Aligned.SAFE Aligned.EOF
config.py polarization parallel`. Real recipe example:
`gmtsar/python/tests/recipes/README_S1A_SLC_TOPS_LA.txt`.

**Design decision needed before fixing (not made unilaterally):** does
`GMTSAR_S1` target single-subswath `p2p_processing` calls (pairs = raw
product stems) or multi-subswath `p2p_S1_TOPS_Frame` calls (pairs =
.SAFE + .EOF file paths), or both via a config flag? These have
different input requirements and probably want different
`GMTSAR_S1_Config` fields (e.g. Frame needs orbit .EOF paths per date,
not just an orbit_dir). Whoever picks this up next should decide this
before touching `submit()`/`_run_one_pair()`.
