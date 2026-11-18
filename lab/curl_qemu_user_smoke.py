#!/usr/bin/env python3
"""Issue #108: run real Entware curl + synthetic libcurl in QEMU user-mode.

Isolated userspace smoke, NOT full KeeneticOS guest. Official IPKs only;
extract to temporary root, never modify /opt on host.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from lab.make_curl_skew_model import build_model, sha256, _members

DATA = {
    "aarch64-3.10": {
        "feed": "aarch64-k3.10",
        "curl": "0a4763ba37644b4a02c7e8d9e737c91e3efd78d29e7286cf8a07c643e891bf1d",
        "libcurl": "d5d3eca432e020efd92a3dc063bb5c4b2433190ce36680964451b9d76333eec1",
        "model": "e5087a0734bbf766c786c17a4a97a93081e97cb70fe17f7fc0c3300b2822bd77",
        "qemu": "qemu-aarch64-static",
    },
    "mipsel-3.4": {
        "feed": "mipselsf-k3.4",
        "curl": "4b821e371d7efae77c35bd4dbd16cffa201db820d99ed8c4b270f77c9ba72fac",
        "libcurl": "f2f5f3cfd08413806c7970305d3aa4f52a3bd828c6d06fc99ac58896eb5510c4",
        "model": "1d21d649b3a8037efd6bc005844227853344c09d1a8ff253c506578a839ca114",
        "qemu": "qemu-mipsel-static",
    },
}


def fetch(url, digest=None):
    if not url.startswith("https://bin.entware.net/"):
        raise ValueError("unexpected download origin")
    last_error = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise ValueError("unexpected oversized feed or IPK")
            if digest and sha256(data) != digest:
                raise ValueError("SHA256 mismatch: " + url)
            return data
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
    raise RuntimeError("feed fetch unavailable: " + url) from last_error


def package_index(raw, arch):
    entries = {}
    for block in gzip.decompress(raw).decode("utf8", "replace").split("\n\n"):
        if not block.startswith("Package: "):
            continue
        fields = dict(line.split(": ", 1) for line in block.splitlines()
                      if ": " in line)
        name = fields.get("Package")
        if name in entries:
            raise ValueError("duplicate package name in feed: " + name)
        if fields.get("Architecture") not in (arch, "all"):
            continue
        if not re.fullmatch(r"[a-f0-9]{64}", fields.get("SHA256sum", "")):
            raise ValueError("unverifiable feed member: " + str(name))
        if not re.fullmatch(r"[A-Za-z0-9._+~-]+\.ipk", fields.get("Filename", "")):
            raise ValueError("unsafe package filename: " + str(name))
        entries[name] = fields
    return entries


def dependency_closure(index):
    queue = ["curl"]
    selected = []
    while queue:
        name = queue.pop(0)
        if name in selected:
            continue
        if name not in index:
            raise ValueError("dependency missing from feed: " + name)
        selected.append(name)
        if len(selected) > 40:
            raise ValueError("unbounded dependency closure")
        for spec in index[name].get("Depends", "").split(","):
            if "|" in spec:
                raise ValueError("unsupported alternative dependency: " + spec)
            match = re.match(r"^\s*([A-Za-z0-9._+~-]+)", spec)
            if match and match.group(1) not in selected:
                queue.append(match.group(1))
    return selected


def _safe_target(root, name):
    n = PurePosixPath(name.removeprefix("./"))
    if n.is_absolute() or not n.parts or any(x in ("", ".", "..") for x in n.parts):
        raise ValueError("unsafe package member path: " + str(name))
    return root.joinpath(*n.parts)


def extract_package(ipk, root):
    chunks = {m.name.removeprefix("./"): body
              for m, body in _members(ipk, "Entware IPK") if m.isfile()}
    if "data.tar.gz" not in chunks:
        raise ValueError("IPK without data.tar.gz")
    pending = []
    for member, body in _members(chunks["data.tar.gz"], "IPK data"):
        # Real Entware data archives start with a harmless root '.' entry.
        # It has no filesystem effect and must not be treated as traversal.
        if member.name.rstrip("/") in (".", "./"):
            if not member.isdir():
                raise ValueError("non-directory entry at archive root")
            continue
        target = _safe_target(root, member.name)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.issym():
            link = PurePosixPath(member.linkname)
            if link.is_absolute():
                if len(link.parts) < 2 or link.parts[1] != "opt":
                    raise ValueError("unexpected absolute symlink: " + str(link))
                adjusted = os.path.relpath(str(_safe_target(root, str(link)[1:])),
                                           str(target.parent))
            else:
                if ".." in link.parts:
                    raise ValueError("unsafe relative symlink: " + str(link))
                adjusted = str(link)
            pending.append((target, adjusted))
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                target.unlink()
            target.write_bytes(body)
            target.chmod(member.mode & 0o777)
        elif member.islnk():
            raise ValueError("unsupported archive hardlink: " + member.name)
    for path, link in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.is_file():
            path.unlink()
        if not path.exists():
            path.symlink_to(link)


def execute(qemu, root, eager=False):
    envs = ["-E", "LD_LIBRARY_PATH=" + str(root / "opt" / "lib")]
    if eager:
        envs += ["-E", "LD_BIND_NOW=1"]
    cmd = [qemu, "-L", str(root), *envs, str(root / "opt" / "bin" / "curl"), "-V"]
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=20)
    return result.returncode, (result.stdout + "\n" + result.stderr).strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arch", required=True, choices=tuple(DATA))
    ap.add_argument("--qemu", help="override QEMU user-mode executable")
    args = ap.parse_args()
    conf = DATA[args.arch]
    qemu = args.qemu or conf["qemu"]
    base = "https://bin.entware.net/" + conf["feed"] + "/"
    feed = fetch(base + "Packages.gz")
    index = package_index(feed, args.arch)
    closure = dependency_closure(index)
    print("feed_index_sha256=" + sha256(feed))
    print("dependency_closure=" + ",".join(closure), flush=True)
    packages = {}
    for name in closure:
        item = index[name]
        pin = conf.get(name)
        if pin and item["SHA256sum"] != pin:
            raise ValueError("feed drift for pinned package: " + name)
        packages[name] = fetch(base + item["Filename"], item["SHA256sum"])
        print("package=" + name + " version=" + item.get("Version", "?")
              + " sha256=" + item["SHA256sum"], flush=True)
    model = build_model(packages["libcurl"], args.arch)
    if sha256(model) != conf["model"]:
        raise ValueError("generated model bytes drifted from proven real-IPK CI")
    with tempfile.TemporaryDirectory(prefix="entware-qemu-curl-") as temp:
        root = Path(temp)
        for name in closure:
            extract_package(packages[name], root)
        expected = {
            "baseline": (True, False),
            "baseline_eager": (True, True),
            "skew": (args.arch == "mipsel-3.4", False),
            "skew_eager": (False, True),
            "recovered": (True, False),
            "recovered_eager": (True, True),
        }
        observations = {}
        for stage in expected:
            if stage == "skew":
                extract_package(model, root)
            elif stage == "recovered":
                extract_package(packages["libcurl"], root)
            should_pass, eager = expected[stage]
            code, output = execute(qemu, root, eager=eager)
            correct = ((code == 0 and "libcurl/8.23" in output)
                       if should_pass else
                       (code != 0 and "curl_multi_get_offt" in output))
            observations[stage] = {"rc": code, "status": "PASS" if correct else "FAIL",
                                   "output_excerpt": output[:300]}
            print(stage + " " + json.dumps(observations[stage]), flush=True)
        if not all(x["status"] == "PASS" for x in observations.values()):
            raise SystemExit("QEMU_USER_CURL_ACCEPTANCE_FAIL")
    print("QEMU_USER_CURL_ACCEPTANCE_PASS " + args.arch)


if __name__ == "__main__":
    main()
