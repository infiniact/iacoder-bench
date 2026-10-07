"""Private runtime files belong to the identity that actually runs the agent.

Commands contain paths and numeric IDs only, never file contents. Kept free
of Harbor imports so permission checks can exercise real OS users offline.
"""
from __future__ import annotations

import re
import shlex


def parse_identity(stdout: str) -> tuple[int, int]:
    fields = stdout.split()
    if len(fields) != 2 or any(re.fullmatch(r"[0-9]+", f) is None for f in fields):
        raise RuntimeError("Cannot resolve the agent's numeric UID/GID")
    return int(fields[0]), int(fields[1])


def private_files_command(root: str, uid: int, gid: int) -> str:
    """Secure the adapter-owned tree after uploads, including optional files.

    Directory ownership is required too: --secrets-file deletes the credential
    after reading it. A readable file in a root-owned directory is insufficient.
    Refuse symlinks rather than following them while running as root.
    """
    if type(uid) is not int or type(gid) is not int or min(uid, gid) < 0:
        raise ValueError("UID and GID must be non-negative integers")
    path = shlex.quote(root)
    return (
        f"set -eu; test -d {path}; test ! -L {path}; "
        f"test -z \"$(find {path} -type l -print -quit)\"; "
        f"chown -R {uid}:{gid} {path}; "
        f"find {path} -type d -exec chmod 700 {{}} +; "
        f"find {path} -type f -exec chmod 600 {{}} +; "
        f"echo 'iacoder: private runtime files owned by uid={uid} gid={gid}'"
    )
