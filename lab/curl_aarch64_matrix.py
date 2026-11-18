#!/usr/bin/env python3
"""Issue #108: curl/libcurl skew matrix on the AArch64 stand (Cudy WR3000P
Ported 5.1.8, ABI aarch64-3.10 — the FIELD architecture of KAS issue #254).

Reuses the proven ported_e2e provisioning flow (synthetic Config + coldboot
scenario) and the busybox_matrix controller pieces.  The mirror is built
during --prep-only (network namespace safe); the guest phase runs on the
isolated loopback only.  Nothing outside the disposable NAND/EXT4 is touched.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lab.build_entware import build  # noqa: E402
from lab.make_curl_skew_model import build_model  # noqa: E402
from lab.guest import Guest  # noqa: E402
from lab.kas_lifecycle import put as guest_put  # noqa: E402
from lab.mirror import Mirror  # noqa: E402
from lab.provision_ported import (  # noqa: E402
    CONFIG_OFFSET, config_image, entware_config, synthetic_config)
from lab.prepare_ported_wr3000p import write_at  # noqa: E402
from lab.run import run  # noqa: E402

from lab.mt7621.busybox_matrix import (  # noqa: E402
    Matrix, Recorder, fetch, phase_curlneg, phase_curlskew, add_curl_packages)

AARCH64_BASE = "https://bin.entware.net/aarch64-k3.10/"
AARCH64_LOCK = REPO / "lab" / "entware.lock.json"
PRESET = "cudy-wr3000p-v1"
FORWARDS = "8080:80;8022:222"

CURL_IPK_SHA = "0a4763ba37644b4a02c7e8d9e737c91e3efd78d29e7286cf8a07c643e891bf1d"
LIBCURL_IPK_URL = AARCH64_BASE + "libcurl_8.23.0-1_aarch64-3.10.ipk"
LIBCURL_IPK_SHA = "d5d3eca432e020efd92a3dc063bb5c4b2433190ce36680964451b9d76333eec1"
MODEL_IPK_SHA = "a2879ac2c1b6a5b39a6119f2f96e437a1030b3c0b548f5ac003b44c726c13b7d"
FOREIGN_LIBCURL_URL = "https://bin.entware.net/mipselsf-k3.4/libcurl_8.23.0-1_mipsel-3.4.ipk"
FOREIGN_LIBCURL_SHA = "f2f5f3cfd08413806c7970305d3aa4f52a3bd828c6d06fc99ac58896eb5510c4"


def extract_foreign_so(path, sha):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError("foreign libcurl ipk digest mismatch")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as outer:
        data = next(outer.extractfile(m).read() for m in outer.getmembers()
                    if m.isfile() and m.name.removeprefix("./") == "data.tar.gz")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
        return next(t.extractfile(m).read() for m in t.getmembers()
                    if m.name.endswith("/opt/lib/libcurl.so.4.8.0"))


def boot_steps():
    coldboot = json.loads((REPO / "lab" / "scenarios" /
                           "ported-wr3000p-firstboot.json").read_text())["steps"]
    return coldboot + [
        {"action": "wait_serial", "pattern": "Core::Server: system ready",
         "timeout_sec": 240},
        {"action": "wait_serial", "pattern": r"\[5/5\] \"Entware\" installed!",
         "reject_pattern": "Critical error:|Please use ext2/ext3/ext4",
         "timeout_sec": 900},
    ]


def after_scenario_factory(args, recorder, phases):
    def after_scenario(monitor=None, console_dir=None, deadline=None, snapshot=None):
        guest = Guest(args.credentials_file, args.workspace, port=8022,
                      deadline=deadline)
        guest.connect(240)
        mx = Matrix(guest, recorder, deadline if deadline is not None
                    else time.monotonic() + 600)
        try:
            guest.command("mkdir -p /opt/lab")
            guest_put(guest, "/opt/lab/preflight.sh", args.preflight_data)
            guest.command("/opt/bin/busybox sha256sum /opt/lab/preflight.sh"
                          " | /opt/bin/busybox grep -q %s" % args.preflight_sha256)
            for guest_path, data, sha in (
                    ("/opt/lab/libcurl-model.ipk", args.model_ipk_data, args.model_ipk_sha256),
                    ("/opt/lab/libcurl-real.ipk", args.libcurl_ipk_data, LIBCURL_IPK_SHA)):
                guest_put(guest, guest_path, data)
                guest.command("/opt/bin/busybox sha256sum %s"
                              " | /opt/bin/busybox grep -q %s" % (guest_path, sha))
            if args.kind == "curlneg":
                guest_put(guest, "/opt/lab/libcurl-foreign.so", args.foreign_so_data)
                guest.command("/opt/bin/busybox test -s /opt/lab/libcurl-foreign.so")
            for phase in phases:
                try:
                    phase(mx)
                except Exception:
                    recorder.entries.append({
                        "id": "%s_PHASE_ABORT" % phase.__name__, "label": phase.__name__,
                        "command": "", "rc": None, "status": "FAIL",
                        "reasons": ["phase aborted"],
                        "output_tail": ""})
        finally:
            report_dir = Path(args.report_dir)
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / "evidence.json").write_text(
                json.dumps(recorder.to_json(), indent=2), encoding="utf8")
            recorder.write_junit(report_dir / "junit.xml")
            guest.close()
    return after_scenario


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emulator-root", required=True)
    ap.add_argument("--nand-dir", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--credentials-file", required=True)
    ap.add_argument("--preflight-file", required=True)
    ap.add_argument("--preflight-sha256", required=True)
    ap.add_argument("--model-ipk",
                    help="path to legacy SHA256-pinned synthetic aarch64 IPK")
    ap.add_argument("--generate-model", action="store_true",
                    help="rebuild model from SHA256-pinned current real libcurl")
    ap.add_argument("--kind", required=True, choices=("curlskew", "curlneg"))
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--max-seconds", type=int, default=3000)
    ap.add_argument("--prep-only", action="store_true")
    ap.add_argument("--skip-build", action="store_true")
    args = ap.parse_args()

    workspace = Path(args.workspace)
    workspace.mkdir(mode=0o700, exist_ok=args.skip_build)
    credentials = json.loads(Path(args.credentials_file).read_text(encoding="utf8"))

    Path(args.cache_dir).mkdir(parents=True, exist_ok=True)
    preflight_data = Path(args.preflight_file).read_bytes()
    if hashlib.sha256(preflight_data).hexdigest() != args.preflight_sha256:
        raise ValueError("preflight digest mismatch (fail closed)")
    args.preflight_data = preflight_data
    if bool(args.model_ipk) == bool(args.generate_model):
        ap.error("choose exactly one: --model-ipk or --generate-model")
    args.libcurl_ipk_data = fetch(
        LIBCURL_IPK_URL, LIBCURL_IPK_SHA,
        Path(args.cache_dir) / "libcurl_8.23.0-1_aarch64-3.10.ipk")
    if args.generate_model:
        args.model_ipk_data = build_model(args.libcurl_ipk_data, "aarch64-3.10")
        args.model_ipk_sha256 = hashlib.sha256(args.model_ipk_data).hexdigest()
    else:
        args.model_ipk_data = Path(args.model_ipk).read_bytes()
        args.model_ipk_sha256 = MODEL_IPK_SHA
        if hashlib.sha256(args.model_ipk_data).hexdigest() != MODEL_IPK_SHA:
            raise ValueError("legacy synthetic libcurl model digest mismatch (fail closed)")
    if args.kind == "curlneg":
        foreign_path = Path(args.cache_dir) / "libcurl_8.23.0-1_mipsel-3.4.ipk"
        fetch(FOREIGN_LIBCURL_URL, FOREIGN_LIBCURL_SHA, foreign_path)
        args.foreign_so_data = extract_foreign_so(foreign_path, FOREIGN_LIBCURL_SHA)

    nand = workspace / "nand"
    if not args.skip_build:
        shutil.copytree(args.nand_dir, nand)
        for n in range(6):
            os.chmod(next(nand.glob("mt7981.mtd%d.*.bin" % n)), 0o600)
        secret = json.loads(Path(args.credentials_file).read_text())
        write_at([next(nand.glob("mt7981.mtd%d.*.bin" % n)) for n in range(6)],
                 CONFIG_OFFSET,
                 config_image(synthetic_config(secret["username"],
                                               secret["password"],
                                               entware_config())))
        image, mirror = workspace / "opt.img", workspace / "mirror"
        build(str(image), str(mirror), str(args.cache_dir), args.credentials_file,
              arch="aarch64", lock_path=str(AARCH64_LOCK))
        add_curl_packages(mirror, args.cache_dir, base_url=AARCH64_BASE)
    else:
        image, mirror = workspace / "opt.img", workspace / "mirror"
        nand = workspace / "nand"
    if args.prep_only:
        print("PREP_ONLY_DONE", flush=True)
        return 0

    recorder = Recorder(args.kind, {
        "arch": "aarch64-3.10", "preset": PRESET,
        "model_ipk_sha256": args.model_ipk_sha256,
        "synthetic_model_generated": args.generate_model,
        "libcurl_real_sha256": LIBCURL_IPK_SHA,
        "foreign_so": "mipsel-3.4 libcurl.so.4.8.0 (real bytes)",
        "preflight_sha256": args.preflight_sha256,
        "classification": "skew rows are SYNTHETIC at package level (8.15.0-2 "
                          "not published), REAL at ELF/loader level",
    })
    phases = [{"curlskew": phase_curlskew, "curlneg": phase_curlneg}[args.kind]]
    scenario = workspace / "scenario.json"
    scenario.write_text(json.dumps({
        "name": "WR3000P AArch64 curl/libcurl skew matrix (%s)" % args.kind,
        "evidence": "KEENETIC_PORTED_EXPERIMENTAL",
        "steps": boot_steps()}), encoding="utf8")
    with Mirror(Path(mirror)) as port:
        return run(SimpleNamespace(
            scenario=str(scenario), emulator_root=args.emulator_root,
            nand_dir=nand, output_dir=workspace / "report",
            max_seconds=args.max_seconds, preset=PRESET, lan_forwards=FORWARDS,
            opt_image=str(image), archive_image=None, mirror_port=port,
            credentials_file=args.credentials_file,
            after_scenario=after_scenario_factory(args, recorder, phases)))


if __name__ == "__main__":
    sys.exit(main())
