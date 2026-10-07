#!/usr/bin/env python3
"""Eval image cache: archive every image a benchmark needs, restore it on any host, check for updates.

Why: DeepSWE / Terminal-Bench pull their task images from public.ecr.aws and Docker Hub. Bursts of
first pulls hit anonymous rate limits (429) and kill trials at build time. With a cache the registry
is touched only when an image is new or has changed.

Storage: a local directory (`IMGCACHE_DIR`, default `./image-cache`), optionally mirrored to an
Alibaba Cloud OSS bucket when `OSS_BUCKET` is set and `ossutil` is installed (see `oss_env()`).
One zstd archive per image plus `manifest.json`. Every OSS setting comes from the environment
(or the file named by `OSS_ENV_FILE`); nothing about a bucket, endpoint or account is built in.

After `docker load` on a containerd image store, a loaded image only answers to its tag; this tool
also re-creates the `repo@sha256:<digest>` name (via `ctr -n moby images tag`) so that task
references pinned by digest resolve locally instead of going back to the registry.

Subcommands
  list   TASK_DIR...            print the image references the tasks need
  sync   TASK_DIR...            ensure every needed image is archived (pull if new/updated) and uploaded
  load   TASK_DIR...            restore every needed image locally from the cache; checks for updates
                                 first (remote digest of tag refs vs cache) and syncs anything new
  check  TASK_DIR...            report what `load` would do, change nothing

References come from task.toml `docker_image`, environment docker-compose `image:` lines,
Dockerfile `FROM` lines (only for tasks without a prebuilt `docker_image`) and `--extra REF`.
Digest-pinned references (`name:tag@sha256:...`) never change; tag references are checked against
the registry with a HEAD request (does not count against Docker Hub pull limits).
"""
from __future__ import annotations

import argparse, datetime, hashlib, json, os, re, shutil, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

CACHE = Path(os.environ.get("IMGCACHE_DIR", "image-cache")).resolve()
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
])


def log(msg: str) -> None:
    print(f"{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}", flush=True)


def sh(cmd, check=True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=True, **kw)


# ---------------------------------------------------------------- references
def parse_ref(ref: str) -> dict:
    """Split into registry / repository / tag / digest with Docker's normalisation."""
    digest = None
    if "@" in ref:
        ref, digest = ref.split("@", 1)
    first = ref.split("/", 1)[0]
    if "/" in ref and ("." in first or ":" in first or first == "localhost"):
        registry, rest = ref.split("/", 1)
    else:
        registry, rest = "docker.io", ref
    tag = None
    if ":" in rest.rsplit("/", 1)[-1]:
        rest, tag = rest.rsplit(":", 1)
    if registry == "docker.io" and "/" not in rest:
        rest = "library/" + rest
    if not tag and not digest:
        tag = "latest"
    return {"registry": registry, "repo": rest, "tag": tag, "digest": digest}


def full_name(p: dict) -> str:
    return f"{p['registry']}/{p['repo']}"


def refs_from_tasks(dirs: list[str], extra: list[str]) -> list[str]:
    out: set[str] = set(extra)
    for d in dirs:
        for toml in Path(d).glob("*/task.toml"):
            text = toml.read_text()
            prebuilt = re.findall(r'^\s*docker_image\s*=\s*"([^"]+)"', text, re.M)
            out.update(prebuilt)
            env = toml.parent / "environment"
            for comp in list(env.glob("docker-compose*.y*ml")):
                out.update(re.findall(r'^\s*image:\s*["\']?([^\s"\']+)', comp.read_text(), re.M))
            if not prebuilt:
                for df in toml.parent.rglob("Dockerfile"):
                    for m in re.findall(r'^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)', df.read_text(), re.M | re.I):
                        if m != "scratch" and "$" not in m:
                            out.add(m)
    return sorted(r for r in out if "${" not in r)


# ---------------------------------------------------------------- registry
def _token(www: str) -> str | None:
    fields = dict(re.findall(r'(\w+)="([^"]*)"', www))
    if "realm" not in fields:
        return None
    q = "&".join(f"{k}={v}" for k, v in fields.items() if k in ("service", "scope"))
    with urllib.request.urlopen(f"{fields['realm']}?{q}", timeout=30) as r:
        j = json.load(r)
    return j.get("token") or j.get("access_token")


def remote_digest(ref: str) -> str | None:
    """Current digest of a tag reference via HEAD; None when unreachable."""
    p = parse_ref(ref)
    if p["digest"]:
        return p["digest"]
    host = "registry-1.docker.io" if p["registry"] == "docker.io" else p["registry"]
    url = f"https://{host}/v2/{p['repo']}/manifests/{p['tag']}"
    headers = {"Accept": ACCEPT}
    for _ in range(2):
        req = urllib.request.Request(url, method="HEAD", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.headers.get("Docker-Content-Digest")
        except urllib.error.HTTPError as e:
            if e.code == 401 and "Authorization" not in headers:
                tok = _token(e.headers.get("WWW-Authenticate", ""))
                if tok:
                    headers["Authorization"] = f"Bearer {tok}"
                    continue
            return None
        except Exception:
            return None
    return None


def dockerhub_remaining() -> int | None:
    try:
        tok = _token('Bearer realm="https://auth.docker.io/token",service="registry.docker.io",'
                     'scope="repository:ratelimitpreview/test:pull"')
        req = urllib.request.Request("https://registry-1.docker.io/v2/ratelimitpreview/test/manifests/latest",
                                     method="HEAD", headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            v = r.headers.get("ratelimit-remaining")
        return int(v.split(";")[0]) if v else None
    except Exception:
        return None


# ---------------------------------------------------------------- local docker
def local_digests(ref: str) -> set[str]:
    p = parse_ref(ref)
    cp = sh(["docker", "image", "inspect", "-f", "{{json .RepoDigests}}", ref], check=False)
    names = []
    if cp.returncode == 0:
        names = json.loads(cp.stdout.strip() or "[]") or []
    if p["digest"]:
        cp2 = sh(["docker", "image", "inspect", "-f", "{{.Id}}", f"{full_name(p)}@{p['digest']}"], check=False)
        if cp2.returncode == 0:
            names.append(f"x@{p['digest']}")
    return {n.split("@", 1)[1] for n in names if "@" in n}


def containerd_store() -> bool:
    cp = sh(["docker", "info", "-f", "{{json .DriverStatus}}"], check=False)
    return "containerd" in cp.stdout


def name_image(image_id: str, p: dict, digest: str) -> None:
    """Make the loaded image answer to both `repo:tag` and `repo@digest`."""
    if p["tag"]:
        sh(["docker", "tag", image_id, f"{full_name(p)}:{p['tag']}"], check=False)
    if containerd_store():
        src = f"moby-dangling@{image_id}"
        names = sh(["ctr", "-n", "moby", "images", "ls", "-q"], check=False).stdout.split()
        if src not in names:
            # pick any existing name pointing at this id
            src = next((n for n in names if n.endswith(image_id)), None) or (
                f"{full_name(p)}:{p['tag']}" if p["tag"] else None)
        if src:
            sh(["ctr", "-n", "moby", "images", "tag", "--force", src, f"{full_name(p)}@{digest}"], check=False)
            if src.startswith("moby-dangling@"):
                sh(["ctr", "-n", "moby", "images", "rm", src], check=False)


# ---------------------------------------------------------------- manifest / storage
def manifest_path() -> Path:
    return CACHE / "manifest.json"


def read_manifest() -> dict:
    oss_get("manifest.json", manifest_path(), quiet=True)
    if manifest_path().exists():
        return json.loads(manifest_path().read_text())
    return {"images": {}}


def write_manifest(m: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=1, sort_keys=True))
    tmp.replace(manifest_path())
    oss_put(manifest_path(), "manifest.json")


def archive_name(p: dict, digest: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{p['registry']}_{p['repo']}_{p['tag'] or 'nodigest'}")
    return f"{safe}__{digest.split(':')[-1][:16]}.tar.zst"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def oss_env() -> dict | None:
    """OSS settings from the environment, or from the file named by OSS_ENV_FILE (KEY=VALUE lines):
    OSS_BUCKET, OSS_ENDPOINT, OSS_REGION, OSS_PREFIX, and either OSS_ACCESS_KEY_ID/OSS_ACCESS_KEY_SECRET
    or OSS_ECS_ROLE=1 when the host has an instance RAM role attached. Returns None (local only)
    when OSS_BUCKET is unset or ossutil is missing."""
    env = dict(os.environ)
    f = Path(os.environ["OSS_ENV_FILE"]) if os.environ.get("OSS_ENV_FILE") else None
    if f and f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip().removeprefix("export ").strip(), v.strip().strip('"\''))
    if not env.get("OSS_BUCKET") or not shutil.which("ossutil"):
        return None
    return env


def _oss(args: list[str], env: dict) -> subprocess.CompletedProcess:
    cmd = ["ossutil", *args, "-e", env.get("OSS_ENDPOINT", "")]
    if env.get("OSS_REGION"):
        cmd += ["--region", env["OSS_REGION"]]
    if env.get("OSS_ACCESS_KEY_ID"):
        cmd += ["-i", env["OSS_ACCESS_KEY_ID"], "-k", env["OSS_ACCESS_KEY_SECRET"]]
    elif env.get("OSS_ECS_ROLE"):  # ossutil v2 reads the instance's attached role from metadata
        cmd += ["--mode", "EcsRamRole"]
    return sh(cmd, check=False)


def oss_url(key: str, env: dict) -> str:
    prefix = env.get("OSS_PREFIX", "eval-image-cache").strip("/")
    return f"oss://{env['OSS_BUCKET']}/{prefix}/{key}"


def oss_put(local: Path, key: str) -> bool:
    env = oss_env()
    if not env:
        return False
    cp = _oss(["cp", "-f", str(local), oss_url(key, env)], env)
    if cp.returncode != 0:
        log(f"OSS upload FAILED {key}: {cp.stderr.strip()[-300:]}")
    return cp.returncode == 0


def oss_get(key: str, local: Path, quiet=False) -> bool:
    env = oss_env()
    if not env:
        return False
    local.parent.mkdir(parents=True, exist_ok=True)
    cp = _oss(["cp", "-f", oss_url(key, env), str(local)], env)
    if cp.returncode != 0 and not quiet:
        log(f"OSS download FAILED {key}: {cp.stderr.strip()[-300:]}")
    return cp.returncode == 0


# ---------------------------------------------------------------- operations
def pull(ref: str) -> bool:
    p = parse_ref(ref)
    if p["registry"] == "docker.io":
        while (rem := dockerhub_remaining()) is not None and rem < 5:
            log(f"Docker Hub anonymous quota low ({rem}); waiting 10 min")
            time.sleep(600)
    for attempt in range(1, 6):
        cp = sh(["docker", "pull", "-q", ref], check=False)
        if cp.returncode == 0:
            return True
        log(f"pull failed attempt={attempt} {ref}: {cp.stderr.strip()[-200:]}")
        time.sleep(attempt * 60)
    return False


def archive(ref: str, digest: str, m: dict) -> bool:
    p = parse_ref(ref)
    name = archive_name(p, digest)
    out = CACHE / name
    CACHE.mkdir(parents=True, exist_ok=True)
    save_ref = f"{full_name(p)}@{p['digest']}" if p["digest"] else ref
    part = out.with_suffix(".part")
    with open(part, "wb") as f:
        save = subprocess.Popen(["nice", "-n", "19", "docker", "save", save_ref], stdout=subprocess.PIPE)
        z = subprocess.run(["nice", "-n", "19", "zstd", "-q", "-T2", "-3"], stdin=save.stdout, stdout=f)
        save.wait()
    if save.returncode or z.returncode:
        part.unlink(missing_ok=True)
        log(f"save FAILED {ref}")
        return False
    part.replace(out)
    entry = {"ref": ref, "digest": digest, "file": name, "sha256": sha256(out), "bytes": out.stat().st_size,
             "saved_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    uploaded = oss_put(out, name)
    entry["oss"] = uploaded
    m["images"][ref] = entry
    write_manifest(m)
    log(f"archived {ref} {entry['bytes'] >> 20} MiB oss={'yes' if uploaded else 'no'}")
    if uploaded and os.environ.get("IMGCACHE_KEEP_LOCAL", "1") == "0":
        out.unlink()  # OSS holds it; restore() downloads on demand
    return True


def restore(ref: str, entry: dict) -> bool:
    p = parse_ref(ref)
    path = CACHE / entry["file"]
    if not path.exists() or sha256(path) != entry["sha256"]:
        if not oss_get(entry["file"], path) or sha256(path) != entry["sha256"]:
            log(f"archive missing or checksum mismatch: {ref}")
            return False
    z = subprocess.Popen(["zstd", "-dc", str(path)], stdout=subprocess.PIPE)
    ld = subprocess.run(["docker", "load"], stdin=z.stdout, capture_output=True, text=True)
    z.wait()
    if ld.returncode:
        log(f"docker load FAILED {ref}: {ld.stderr.strip()[-200:]}")
        return False
    ids = re.findall(r"Loaded image ID: (sha256:[0-9a-f]+)", ld.stdout)
    image_id = ids[0] if ids else sh(["docker", "image", "inspect", "-f", "{{.Id}}", ref], check=False).stdout.strip()
    if image_id:
        name_image(image_id, p, entry["digest"])
    ok = entry["digest"] in local_digests(ref) or sh(
        ["docker", "image", "inspect", f"{full_name(p)}@{entry['digest']}"], check=False).returncode == 0
    log(f"{'restored' if ok else 'restore UNVERIFIED'} {ref}")
    return ok


def plan(refs: list[str], m: dict) -> list[tuple[str, str, str | None]]:
    """(ref, action, wanted_digest) where action in ok|load|pull|unknown."""
    out = []
    for ref in refs:
        want = remote_digest(ref)
        entry = m["images"].get(ref)
        have = local_digests(ref)
        if want is None:  # registry unreachable: trust the cache, else the local image
            want = entry["digest"] if entry else (next(iter(have)) if len(have) == 1 else None)
        if want and want in have:
            out.append((ref, "ok", want))
        elif entry and want == entry["digest"]:
            out.append((ref, "load", want))
        elif want or not entry:
            out.append((ref, "pull", want))
        else:
            out.append((ref, "unknown", want))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["list", "sync", "load", "check"])
    ap.add_argument("task_dirs", nargs="*")
    ap.add_argument("--extra", action="append", default=[], help="additional image reference (repeatable)")
    ap.add_argument("--refs-file", help="file with one reference per line, added to the task references")
    ap.add_argument("--all", action="store_true", help="also include every image already in the cache manifest")
    a = ap.parse_args()
    extra = list(a.extra)
    if a.refs_file:
        extra += [l.strip() for l in Path(a.refs_file).read_text().splitlines() if l.strip()]
    m = read_manifest()
    if a.all:
        extra += list(m["images"])
    refs = refs_from_tasks(a.task_dirs, extra)
    if a.cmd == "list":
        print("\n".join(refs))
        return 0
    steps = plan(refs, m)
    counts = {k: sum(1 for _, s, _ in steps if s == k) for k in ("ok", "load", "pull", "unknown")}
    log(f"{len(refs)} images: {counts} oss={'on' if oss_env() else 'off'}")
    if a.cmd == "check":
        for ref, action, want in steps:
            entry = m["images"].get(ref)
            if action == "ok" and not (entry and entry["digest"] == want):
                action = "stale"  # local current, cache missing or behind
            if action != "ok":
                print(f"{action:7} {ref} {want or ''}")
        return 0 if counts["unknown"] == 0 else 1
    failed = []
    for ref, action, want in steps:
        entry = m["images"].get(ref)
        if action == "ok":
            # the local image is current; refresh the cache when it lags behind (new or updated image)
            if not (entry and entry["digest"] == want and ((CACHE / entry["file"]).exists() or entry.get("oss"))):
                log(f"{'UPDATE' if entry else 'NEW'} cache entry {ref}")
                archive(ref, want, m) or failed.append(ref)
            elif entry and not entry.get("oss") and oss_env():
                entry["oss"] = oss_put(CACHE / entry["file"], entry["file"])
                write_manifest(m)
            continue
        if action == "load":
            if restore(ref, entry):
                continue
        if action in ("pull", "load", "unknown"):
            if a.cmd == "load":
                log(f"{'UPDATE' if entry else 'NEW'} {ref}: pulling from registry")
            if not pull(ref):
                failed.append(ref)
                continue
            digest = want or next(iter(local_digests(ref)), None)
            if digest:
                archive(ref, digest, m) or failed.append(ref)
    log(f"done: {len(refs) - len(failed)}/{len(refs)} ready" + (f"; FAILED {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
