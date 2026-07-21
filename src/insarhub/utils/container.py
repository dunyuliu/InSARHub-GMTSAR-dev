# -*- coding: utf-8 -*-
"""
Wrap a shell command to run inside a user-provided container.

``container`` is either a path to an existing Apptainer/Singularity ``.sif``
image file, or a Docker image reference (name[:tag]) — detected by checking
whether it resolves to an existing file on disk. No new dependency is added:
this shells out to the ``docker``/``apptainer``/``singularity`` CLI via
``subprocess``, matching how this codebase already shells out to
``sbatch``/``squeue``/``scancel`` rather than using an SDK.
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path


def wrap_container_cmd(
    container: str,
    cmd: str,
    bind_dir: Path,
    *,
    workdir_in_container: str | None = None,
) -> str:
    """Return ``cmd`` wrapped to execute inside ``container``.

    ``bind_dir`` (typically the processing workdir) is bind-mounted into the
    container at the same path, so files written by the wrapped command are
    visible on the host at the identical path afterward.
    """
    bind_dir = Path(bind_dir)
    wd = workdir_in_container or str(bind_dir)
    quoted_cmd = shlex.quote(cmd)

    if Path(container).expanduser().exists():
        runtime = shutil.which("apptainer") or shutil.which("singularity") or "apptainer"
        return (
            f"{runtime} exec --bind {bind_dir}:{bind_dir} --pwd {wd} "
            f"{container} bash -c {quoted_cmd}"
        )

    user_flag = f"--user {os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else ""
    return (
        f"docker run --rm {user_flag} -v {bind_dir}:{bind_dir} -w {wd} "
        f"{container} bash -c {quoted_cmd}"
    ).replace("  ", " ")
