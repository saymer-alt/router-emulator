# curl/libcurl skew — night-lab report (issues #108 + KAS #254/#255, 2026-11-18)

Companion documents: KAS `docs/32-entware-curl-libcurl-skew.md` (PR #255 branch),
KAS `tests/kas-web-channel-install-regression.py` (new skew cases),
this file (emulator matrix + root-cause evidence).

## A. Root cause — PROVED from the real feed metadata

Fetched from the official feeds (2026-11-18, `Packages.gz` of both
`aarch64-k3.10` and `mipselsf-k3.4`):

```text
Package: curl      Version: 8.23.0-1
Depends: libc, libssp, librt, libpthread, libcurl      <- unversioned

Package: libcurl   Version: 8.23.0-1
Depends: libc, libssp, librt, libpthread, libopenssl, zlib, libnghttp2, ca-bundle
```

Mechanism (`opkg` semantics, not a hypothesis):

1. `opkg install curl` with an installed `libcurl 8.15.0-2` sees the
   unversioned `Depends: libcurl` **satisfied** → upgrades the `curl` binary
   alone → binary/lib library skew. Exactly matches the field transcript
   ("Upgrading curl on root from 8.15.0-2 to 8.23.0-1", no libcurl line).
2. The symbol story is real: `/opt/bin/curl` 8.23.0-1 (both ABIs) references
   `curl_multi_get_offt` (import in the dynamic symbol table); the matching
   `/opt/lib/libcurl.so.4.8.0` exports it; the 8.15 generation does not.
3. A mass `opkg upgrade` upgrades both packages, which is why the skew only
   appears on the selective path.
4. `opkg status` marks nothing inconsistent after the partial upgrade — there
   is no built-in detection, so operator-visible symptoms (loader error) are
   the only signal.

**Historical package availability:** `libcurl 8.15.0-2` (and 8.15.0-1/8.16.0-1)
answer **404** on `bin.entware.net` and `test.entware.net` for **both** ABIs
(probed 2026-11-18). Only current versions are published. Consequence: the
emulator skew reproduction is **SYNTHETIC at package level** (a model built
from the real 8.23.0-1 bytes with exactly one dynamic-symbol name altered and
control version 8.15.0-2) and **REAL at ELF/loader level**; every row is
labelled. Nothing synthetic is presented as real feed behavior.

## Fixed inputs (fail-closed SHA-256 pins)

| Artifact | SHA-256 |
|---|---|
| `curl_8.23.0-1_mipsel-3.4.ipk` (bin feed) | `4b821e371d7efae77c35bd4dbd16cffa201db820d99ed8c4b270f77c9ba72fac` |
| `libcurl_8.23.0-1_mipsel-3.4.ipk` (bin feed) | `f2f5f3cfd08413806c7970305d3aa4f52a3bd828c6d06fc99ac58896eb5510c4` |
| `curl_8.23.0-1_aarch64-3.10.ipk` (bin feed) | `0a4763ba37644b4a02c7e8d9e737c91e3efd78d29e7286cf8a07c643e891bf1d` |
| `libcurl_8.23.0-1_aarch64-3.10.ipk` (bin feed) | `d5d3eca432e020efd92a3dc063bb5c4b2433190ce36680964451b9d76333eec1` |
| synthetic `libcurl_8.15.0-2-model_mipsel-3.4.ipk` (built) | `fab22c86080a71b8f60b3f67f15487e413a675bdfe1a8976298f336df708a6f8` |
| synthetic `libcurl_8.15.0-2-model_aarch64-3.10.ipk` (built) | `7042f8cf4f4e689c30c1decd784fb50ac2b753a1fc4aced4944592271b896b5c` |
| KAS preflight (`development@0bbe0cd`) | `defb91a0fa819f06d6dcc6651704b183d45a6bf638f831167da2cb5c3132378e` |

Model construction is reproducible: real `data.tar.gz`, `ELF` members scanned
for `curl_multi_get_offt`, first occurrence in the library renamed
(`curl_multi_get_offu`, same length → no offset shifts), `control` Version
rewritten to `8.15.0-2`, ipk re-tarred. Builder: night-lab `curl-stageA.py`
(private workspace; digests above pin the outputs).

## B. Emulator matrix — MT7621 / MIPSel (`xiaomi-mi-router-3g-breed`, Ported 5.1.8, ABI `mipsel-3.4`)

Controller: `lab/mt7621/busybox_matrix.py` kinds `curlskew` / `curlneg`
(this branch). Mirror = pinned 25-package bootstrap **plus** real
curl/libcurl 8.23.0-1 entries, so `opkg install curl` exercises opkg's own
dependency resolution. QEMU v10.1.0-65-g955c37b; scenario chain 17/17 PASS per
run. Evidence: `lab/mt7621/evidence/ported-mt7621-curl-matrix-20261118/`.

| # | Scenario | Classification | Result | Guest evidence (verbatim in `evidence.json`) |
|---|---|---|---|---|
| 1 | Compatible pair via opkg's own resolution (`opkg install curl`) | REAL | PASS | C1–C2: both 8.23.0-1; `curl -V` OK |
| 2 | `LD_BIND_NOW=1 curl -V` healthy | REAL | PASS | C4 rc 0 |
| 3 | HTTP transport of a working curl | REAL | PASS | C5: `curl -o /dev/null -w %{http_code}` → 200 against the mirror |
| 4 | **curl 8.23 + 8.15-model library (field skew)** | **SYNTHETIC PASS / REAL loader** | **PASS (reproduced)** | C7–C8: real opkg `--force-downgrade` → status **curl 8.23.0-1 + libcurl 8.15.0-2** (exact field state); **C9: plain `curl -V` SUCCEEDS on the broken pair** (lazy binding never touches the symbol); **C10: `LD_BIND_NOW=1` → `/opt/bin/curl: symbol lookup error: undefined symbol: curl_multi_get_offt`** (exact field signature) |
| 5 | Recovery via explicit `opkg install /path/libcurl.ipk` | REAL | PASS | C12: "Upgrading libcurl on root from 8.15.0-2 to 8.23.0-1..."; C13 `curl -V` + LD_BIND_NOW OK |
| 6 | Missing libcurl | REAL | PASS (correctly diagnosed) | D2: `error while loading shared libraries: libcurl.so.4: cannot open shared object file`; D3 reinstall restores |
| 7 | Wrong-ABI library (real aarch64 .so over mipsel) | REAL bytes, synthetic placement | PASS (distinct diagnostic) | D4: `wrong ELF class: ELFCLASS64`; D5 reinstall restores |
| 8 | Corrupted library (truncated .so) | REAL | PASS (distinct diagnostic) | D6: `Bus error` (SIGBUS class — hard death, no shell message); D7 reinstall restores |
| 9 | Dangling `libcurl.so.4` symlink | REAL | PASS | D8: same "cannot open shared object file" class; D9 reinstall restores |
| 10 | Missing `ldd` on PATH | REAL | PASS | D10: `curl -V` works from a PATH without ldd — ldd is not needed by curl itself |
| 11 | curl works / HTTPS broken | NOT TESTED offline | — | the isolated namespace has no TLS endpoint; classified in docs/32, never merged into a PASS |
| 12 | Feed unavailable | REAL | PASS (clean refusal) | D12: `wget returned 1` / `Connection refused`, installed set untouched |
| 13 | Truncated (partial) IPK | REAL | PASS (no false install) | D11: `pkg_init_from_file: Malformed package file` + version unchanged |

Notes on the recorded statuses: the pre-registered expectation for row 4's
plain `curl -V` predicted a failure and the guest **proved it succeeds** —
that recorded FAIL is the evidence for the documented limitation "plain
`curl -V` is not a skew detector" and the reason the KAS installer gained the
package-database comparison before the LD_BIND_NOW probe. Row 6/7/8 error
texts are mutually distinct, which is what a diagnostic must distinguish.
The KAS preflight (rc 2 class, `WARN wget-capability` + hold-flag
`UNKNOWN opkg-view`) did **not** flag the skew before tonight's preflight
update — the gap is now closed by the new `curl-libcurl-skew` check.

## C. Emulator matrix — AArch64 (`cudy-wr3000p-v1`, Ported 5.1.8 reporting model KN-3811, ABI `aarch64-3.10` — THE FIELD ARCHITECTURE)

Controller: `lab/curl_aarch64_matrix.py` (this branch), same scenario kinds;
provisioning reuses the proven ported_e2e flow (synthetic Config + firstboot
scenario + `[5/5] "Entware" installed!`). Evidence:
`lab/mt7621/evidence/ported-wr3000p-curl-20261118/{aarch64-curlskew,aarch64-curlneg}/`.

| Row | Result | Guest evidence |
|---|---|---|
| Aligned install via opkg resolution | PASS | curl 8.23.0-1 + libcurl 8.23.0-1 from the real feed metadata |
| **Field skew reproduced** | **PASS — `REAL REPRO` class** | C8: `curl 8.23.0-1` + `libcurl 8.15.0-2` via real `opkg install --force-downgrade` of the model; **C9: plain `/opt/bin/curl -V` → `/opt/bin/curl: symbol lookup error: undefined symbol: curl_multi_get_offt`** — the exact field transcript, on the field ABI |
| `LD_BIND_NOW=1 curl -V` | PASS | C10: same signature, deterministic |
| Recovery `opkg install libcurl` | PASS | C12–C13: aligned again, both probes clean |
| Missing libcurl / wrong-ABI / corrupted / dangling / ldd / feed-down / truncated-IPK | all PASS (correct diagnostics, distinct error classes, no false PASS) | D1–D14, 7 PASS + 7 OBSERVED, 0 FAIL |

**ABI difference (new finding):** on `aarch64-3.10` the reference to
`curl_multi_get_offt` breaks even a plain `curl -V` (the loader resolves the
symbol for the skewed pair without `LD_BIND_NOW`), while on `mipsel-3.4`
plain `curl -V` **succeeds** on the identical skew and only `LD_BIND_NOW=1`
catches it. Consequences: (1) the field symptom is reproduced 1:1 on the
field architecture; (2) an installer check that relies on `curl -V` alone is
**architecture-dependent** — the package-database comparison + `LD_BIND_NOW`
probe pair (KAS PR #255) is the only combination that works on both ABIs.

## D. KAS changes (PR #255 branch `test/curl-libcurl-skew-field-20261117`, commit `01be327`)

1. **Package-database skew detector** in `kas-web-install.sh` (primary gate,
   runs before any download; exact message with the repair command).
2. Existing `LD_BIND_NOW=1 curl -V` probe kept (secondary, catches loader
   breakage the version comparison cannot see); its limits documented.
3. `tests/kas-web-channel-install-regression.py`: new `skewed_versions` case
   (opkg mock reports curl 8.23.0-1 + libcurl 8.15.0-2 → fail closed, no
   download) — suite **16/16 OK** in the WSL LF clone.
4. `docs/32-entware-curl-libcurl-skew.md`: root cause, detection limits,
   field-proven recovery, and the bootstrap-stage limitation (`curl | sh`
   cannot self-diagnose; documented operator pre-check; no weak alternative
   loader shipped).

1. **Package-database skew detector** in `kas-web-install.sh` (primary gate,
   runs before any download; exact message with the repair command).
2. Existing `LD_BIND_NOW=1 curl -V` probe kept (secondary, catches loader
   breakage the version comparison cannot see); its limits documented.
3. `tests/kas-web-channel-install-regression.py`: new `skewed_versions` case
   (opkg mock reports curl 8.23.0-1 + libcurl 8.15.0-2 → fail closed, no
   download) — suite **18/18 OK** in the WSL LF clone.
4. **`kas-entware-preflight.sh`: new read-only `curl-libcurl-skew` check** —
   FAIL on a version mismatch with repair guidance, PASS when aligned,
   UNKNOWN without opkg; `tests/busybox-preflight-regression.py` extended
   (skew → FAIL exit 1, aligned → PASS, mutating-opkg static scanner stays
   green) — suite passed.
5. `docs/32-entware-curl-libcurl-skew.md`: root cause, detection limits,
   field-proven recovery, and the bootstrap-stage limitation (`curl | sh`
   cannot self-diagnose; documented operator pre-check; no weak alternative
   loader shipped).

## E. Adjacent Entware scenarios — existing coverage map (issue #108 list)

| Topic | Already covered by | Gap |
|---|---|---|
| BusyBox 1.37→1.38 applet/symlink loss | PR #56 CI (aarch64, real 1.37 prerm) + night matrix 2026-11-18 (mipsel, remove-branch window + package-owned recovery) | true mid-transaction interruption |
| wget/HTTPS after BusyBox changes | preflight `WARN wget-capability` (Entware #887) + fresh-install inventory | full TLS path offline impossible (HTTP-only mirror) |
| jq without ONIGURUMA (mipsel) | `tests/kas-web-channel-install-regression.py` ONIGURUMA shim | — |
| Partial updates / inconsistent opkg DB | night matrix 2026-11-18 (kill -9 + rerun) + tonight's skew matrix | — |
| KAS Web/Core install errors | `kas-web-channel-install-regression.py` (bad digest, wrong arch, stale registry, unpublished stable, downgrade refusal) | — |
| Entware bootstrap | MT7621 33/33 tz-skip cycle + tonight's aligned/skew installs | — |

## F. Honest boundaries

- The skew package state is reached through a **synthetic model** (real
  8.23.0-1 bytes with one dynamic symbol renamed; the historical 8.15.0-2 is
  404 everywhere). The *mechanism* — opkg's unversioned-dependency resolution
  — was exercised with real packages and real metadata; the *loader failure*
  is produced by the real loader against real-but-patched ELF bytes. No row
  above claims a real-feed 8.15.0-2 reproduction.
- HTTPS behavior (row 11) is classified only: the isolated namespace has no
  TLS endpoint by design. The field HTTPS evidence after recovery
  (`HTTP/2 200` to github.com) is recorded in KAS issue #254.
- The `wget` HTTPS/BusyBox and BusyBox-prerm rows reuse the 2026-11-18 night
  matrix (companion report `lab/reports/entware-busybox-20261118/`) and PR #56
  CI evidence; they were not re-run here (unchanged SHAs, token economy).
- Emulator ≠ physical router: none of this is hardware acceptance; the field
  device's post-recovery state remains the owner's acceptance record.
