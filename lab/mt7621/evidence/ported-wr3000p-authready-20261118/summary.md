Laboratory outcome: **FAIL**

| Gate | Status | Evidence |
|---|---|---|
| BOOT_READY | PASS | boot_stages_observed |
| AUTH_READY | PASS | authenticated_version_and_reboot |
| ENTWARE_READY | FAIL | runner_failure |
| KAS_PREFLIGHT | BLOCKED | prerequisite_blocked |
| KAS_INSTALL | BLOCKED | prerequisite_blocked |
| KAS_DOCTOR | BLOCKED | prerequisite_blocked |
| KAS_REBOOT | BLOCKED | prerequisite_blocked |
| KAS_ROLLBACK | BLOCKED | prerequisite_blocked |
| KAS_UNINSTALL | BLOCKED | prerequisite_blocked |
| KAS_UNINSTALL_PACKAGES | BLOCKED | prerequisite_blocked |
| KAS_UPDATE_E2E | BLOCKED | prerequisite_blocked |

| Capability | Status | Evidence |
|---|---|---|
| ndmc | NOT_TESTED | unattempted |
| native_rci | NOT_TESTED | unattempted |
| dns_filter | NOT_TESTED | unattempted |
| opkg_kmod_netfilter | NOT_TESTED | unattempted |
| dns_tls_or_https | NOT_TESTED | unattempted |
| proxy_client | NOT_TESTED | unattempted |
| netfilter_mark | NOT_TESTED | unattempted |
| netfilter_connmark | NOT_TESTED | unattempted |
| tun | NOT_TESTED | unattempted |
| routing | NOT_TESTED | unattempted |
| ext4_opt | NOT_TESTED | unattempted |
| ram | NOT_TESTED | unattempted |
| zram | NOT_TESTED | unattempted |
| external_swap | NOT_TESTED | unattempted |
| clock | NOT_TESTED | unattempted |
| dns | NOT_TESTED | unattempted |
| local_http | NOT_TESTED | unattempted |
| package_mirror | NOT_TESTED | unattempted |

| Input | Immutable reference |
|---|---|
| emulator_commit | `206d22d05f2ec7d44f7cb6a28d403b988fbbefcf` |
| qemu_source_commit | `f8b2f64e2336a28bf0d50b6ef8a7d8c013e9bcf3` |
| qemu_binary_sha256 | `d0d343a847d247367141362c3c9a2dea17cdd0edd320046e94731404aed3ff4f` |
| qemu_patches_sha256 | `dd771dca35fdafa98a4790044513143ae5e3b12bc74c6c390bf9c6e47e80625d` |
| firmware_sha256 | `710e4a37b1aa9f3a65a5122edd0650f5c5ad3e6eca5df53b11ac28aab2664eb7` |
| keenboot_sha256 | `8b8615b345d912187853fea6bef6b1868760fd52ed92ba7c3a380ad5533cbf08` |
| entware_lock_sha256 | `1f2ee977fcac25f7e415b4d2b0e50a8686402da27b1d23b9750bcc43bf32f5d8` |
| kas_assets_lock_sha256 | `d7634f584bb1b695f3092c7d2d41c9dee16ebe78c6809a27ad6b182c650d7c0a` |

Physical-router acceptance: NOT_TESTED.
