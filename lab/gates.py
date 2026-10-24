"""Public evidence contains fixed labels only, never guest text or credentials."""
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

GATES = ('BOOT_READY', 'AUTH_READY', 'ENTWARE_READY', 'KAS_PREFLIGHT',
         'KAS_INSTALL', 'KAS_DOCTOR', 'KAS_REBOOT', 'KAS_ROLLBACK', 'KAS_UNINSTALL')
STATUSES = ('PASS', 'FAIL', 'BLOCKED', 'NOT_TESTED')
CAPABILITIES = ('ndmc', 'native_rci', 'dns_filter', 'opkg_kmod_netfilter', 'dns_tls_or_https',
                'proxy_client', 'netfilter_mark', 'netfilter_connmark', 'tun',
                'routing', 'ext4_opt', 'ram', 'zram', 'external_swap', 'clock',
                'dns', 'local_http', 'package_mirror')
REASONS = {'unattempted', 'prerequisite_blocked', 'boot_stages_observed',
           'authenticated_version_and_reboot', 'authentication_rejected',
           'serial_timeout', 'runner_failure', 'ext4_opkg_abi_and_reboot',
           'entware_install_failed', 'required_component_absent',
           'runtime_prerequisites_passed', 'native_installer_passed',
           'native_doctor_passed', 'services_restored_after_reboot',
           'native_rollback_passed', 'native_uninstall_passed', 'native_uninstall_failed',
           'native_command_verified', 'execution_failed', 'component_absent', 'runtime_capability_unavailable',
           'native_installer_failed', 'native_doctor_failed', 'doctor_warning', 'verified_tls_source_failed',
           'native_remove_not_implemented', 'snapshot_gate_blocked', 'native_reboot_failed',
           'native_rollback_failed', 'native_update_failed', 'idempotence_failed', 'rollback_not_exercised'}
REASONS.update({'native_services_duplicate', 'native_services_not_ready', 'native_ownership_invalid',
    'native_proxy_invalid', 'native_policy_invalid', 'native_dns_invalid', 'native_rules_not_ready',
    'native_file_digest_invalid', 'foreign_proxy_changed', 'foreign_cron_changed',
    'foreign_setup_failed', 'native_socks_not_ready', 'native_command_failed', 'native_command_timed_out',
    'mihomo_config_changed', 'magi_config_changed', 'watchdog_changed', 'hook_changed',
    'crontab_changed', 'ledger_changed', 'proxy_changed', 'policy_changed', 'netfilter_changed'})
CHECKS = ('KAS_IDEMPOTENCE', 'KAS_INSTALL_INTERRUPTION', 'KAS_UPDATE', 'KAS_FOREIGN_RESOURCES',
          'KAS_UNINSTALL_SNAPSHOT', 'KAS_TRANSACTION_GATE', 'KAS_WAN_RESTART_GUARD', 'KAS_UNINSTALL_GUARD')


class Evidence:
    def __init__(self):
        self.gates = {name: {'status': 'NOT_TESTED', 'reason': 'unattempted'} for name in GATES}
        self.capabilities = {name: {'status': 'NOT_TESTED', 'reason': 'unattempted'} for name in CAPABILITIES}
        self.checks = {}
        self.references = {}
        self.measurements = {}

    def reference(self, name, value):
        if name not in ('emulator_commit', 'kas_commit', 'qemu_source_commit', 'qemu_binary_sha256',
                        'qemu_patches_sha256', 'firmware_sha256', 'keenboot_sha256',
                        'entware_lock_sha256', 'kas_assets_lock_sha256') or not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', value):
            raise ValueError('invalid public source reference')
        self.references[name] = value

    def measurement(self, name, value):
        if name not in ('doctor_ok', 'doctor_warn', 'doctor_fail') or not isinstance(value, int) or not 0 <= value <= 10000:
            raise ValueError('invalid public measurement')
        self.measurements[name] = value

    def check(self, name, status, reason):
        if name not in CHECKS or status not in STATUSES or reason not in REASONS:
            raise ValueError('unapproved lifecycle check label')
        self.checks[name] = {'status': status, 'reason': reason}

    def capability(self, name, status, reason):
        if name not in CAPABILITIES or status not in ('SUPPORTED', 'UNSUPPORTED', 'BLOCKED', 'NOT_TESTED') or reason not in REASONS:
            raise ValueError('unapproved capability evidence label')
        self.capabilities[name] = {'status': status, 'reason': reason}

    def set(self, name, status, reason):
        if name not in GATES or status not in STATUSES or reason not in REASONS:
            raise ValueError('unapproved public evidence label')
        self.gates[name] = {'status': status, 'reason': reason}

    def write(self, directory):
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        values = (*self.gates.values(), *self.checks.values())
        outcome = 'FAIL' if any(v['status'] == 'FAIL' for v in values) else 'BLOCKED' if any(v['status'] != 'PASS' for v in values) else 'PASS'
        report = {'schema': 1, 'platform': 'WR3000P_PORTED_5_1_8_QEMU', 'outcome': outcome,
                  'hardware_acceptance': 'NOT_TESTED', 'gates': self.gates,
                  'capabilities': self.capabilities, 'checks': self.checks,
                  'references': self.references, 'measurements': self.measurements}
        (out / 'gates.json').write_text(json.dumps(report, indent=2) + '\n')
        results = {**self.gates, **self.checks}
        suite = ET.Element('testsuite', name='Ported Entware KAS', tests=str(len(results)),
                           failures=str(sum(v['status'] == 'FAIL' for v in results.values())),
                           skipped=str(sum(v['status'] in ('BLOCKED', 'NOT_TESTED') for v in results.values())))
        lines = ['Laboratory outcome: **' + outcome + '**', '', '| Gate | Status | Evidence |', '|---|---|---|']
        for name, value in results.items():
            case = ET.SubElement(suite, 'testcase', name=name)
            if value['status'] == 'FAIL':
                ET.SubElement(case, 'failure', message=value['reason'])
            elif value['status'] in ('BLOCKED', 'NOT_TESTED'):
                ET.SubElement(case, 'skipped', message=value['status'] + ': ' + value['reason'])
            lines.append('| %s | %s | %s |' % (name, value['status'], value['reason']))
        ET.ElementTree(suite).write(out / 'junit.xml', encoding='utf-8', xml_declaration=True)
        lines += ['', '| Capability | Status | Evidence |', '|---|---|---|']
        lines += ['| %s | %s | %s |' % (name, value['status'], value['reason'])
                  for name, value in self.capabilities.items()]
        if self.references:
            lines += ['', '| Input | Immutable reference |', '|---|---|']
            lines += ['| %s | `%s` |' % (name, value) for name, value in self.references.items()]
        (out / 'summary.md').write_text('\n'.join(lines) + '\n\nPhysical-router acceptance: NOT_TESTED.\n')
