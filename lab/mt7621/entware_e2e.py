#!/usr/bin/env python3
"""One clean MT7621 Entware MIPSel bootstrap cycle.

Builds the fresh USB ext4 image and the pinned local mirror from
lab/entware-mipselsf.lock.json (arch=mipsel, fail-closed closure check),
serves the mirror on loopback and runs the committed scenario against a
pristine Ported NAND folder.  The guest reaches the mirror only through the
127.0.0.1 guestfwd; the runner keeps -w offline with loopback-only forwards.

Every run needs a FRESH workspace: the mirror and the USB image must not
exist yet (build() refuses to overwrite), and the NAND folder is copied
into a disposable workspace by the runner, so the source NAND stays
pristine across cycles.  Private credentials (--credentials-file) never
enter reports; username "root" authenticates the dropbear SSH probes.
"""
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lab.build_entware import build  # noqa: E402
from lab.mirror import Mirror  # noqa: E402
from lab.run import run  # noqa: E402

LOCK = Path(__file__).resolve().parents[2] / "lab" / "entware-mipselsf.lock.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--emulator-root", required=True)
    ap.add_argument("--scenario", required=True,
                    help="committed scenario JSON, e.g. lab/scenarios/ported-mt7621-entware.json")
    ap.add_argument("--nand-dir", required=True,
                    help="pristine Ported NAND folder (assembled by prepare_ported_mir3g.py)")
    ap.add_argument("--workspace", required=True,
                    help="fresh private directory for media, cache and reports")
    ap.add_argument("--credentials-file", required=True,
                    help='private {"username": "root", "password": "..."} JSON')
    ap.add_argument("--max-seconds", type=int, default=2400)
    args = ap.parse_args()

    workspace = Path(args.workspace)
    workspace.mkdir(mode=0o700, exist_ok=False)
    credentials = json.loads(Path(args.credentials_file).read_text(encoding="utf8"))
    if credentials.get("username") != "root":
        raise ValueError("dropbear SSH probes authenticate as root")
    image, mirror = workspace / "opt.img", workspace / "mirror"
    build(str(image), str(mirror), workspace / "cache", args.credentials_file,
          arch="mipsel", lock_path=str(LOCK))
    with Mirror(Path(mirror)) as port:
        return run(SimpleNamespace(
            scenario=args.scenario, emulator_root=args.emulator_root,
            nand_dir=args.nand_dir, output_dir=workspace / "report",
            max_seconds=args.max_seconds, preset="xiaomi-mi-router-3g-breed",
            lan_forwards="8080:80;8022:222;8023:23", opt_image=str(image),
            archive_image=None, mirror_port=port,
            credentials_file=args.credentials_file))


if __name__ == "__main__":
    sys.exit(main())
