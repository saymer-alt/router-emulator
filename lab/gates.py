"""Public evidence contains fixed labels only, never guest text or credentials."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

GATES = ('BOOT_READY', 'AUTH_READY', 'ENTWARE_READY', 'KAS_PREFLIGHT',
         'KAS_INSTALL', 'KAS_DOCTOR', 'KAS_REBOOT', 'KAS_ROLLBACK', 'KAS_UNINSTALL')
STATUSES = ('PASS', 'FAIL', 'BLOCKED', 'NOT_TESTED')
CAPABILITIES = ('ndmc', 'dns_filter', 'opkg_kmod_netfilter', 'dns_tls_or_https',
                'proxy_client', 'netfilter_mark', 'netfilter_connmark', 'tun',
                'routing', 'ext4_opt', 'ram', 'zram', 'external_swap', 'clock',
                'dns', 'local_http', 'package_mirror')
REASONS = {'unattempted', 'prerequisite_blocked', 'boot_stages_observed',
           'authenticated_version_and_reboot', 'authentication_rejected',
           'serial_timeout', 'runner_failure', 'ext4_opkg_abi_and_reboot',
           'entware_install_failed', 'required_component_absent',
           'runtime_prerequisites_passed', 'native_installer_passed',
           'native_doctor_passed', 'services_restored_after_reboot',
           'native_rollback_passed', 'native_uninstall_passed',
           'native_command_verified', 'execution_failed', 'component_absent', 'runtime_capability_unavailable',
           'native_installer_failed', 'native_doctor_failed', 'doctor_warning', 'verified_tls_source_failed',
           'native_remove_not_implemented', 'snapshot_gate_blocked', 'native_reboot_failed',
           'native_rollback_failed', 'native_update_failed', 'idempotence_failed'}
CHECKS = ('KAS_IDEMPOTENCE', 'KAS_UPDATE', 'KAS_FOREIGN_RESOURCES',
          'KAS_UNINSTALL_SNAPSHOT', 'KAS_TRANSACTION_GATE', 'KAS_WAN_RESTART_GUARD')


class Evidence:
    def __init__(self):
        self.gates = {name: {'status': 'NOT_TESTED', 'reason': 'unattempted'} for name in GATES}
        self.capabilities = {name: {'status': 'NOT_TESTED', 'reason': 'unattempted'} for name in CAPABILITIES}
        self.checks = {}

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
        report = {'schema': 1, 'platform': 'WR3000P_PORTED_5_1_8_QEMU',
                  'hardware_acceptance': 'NOT_TESTED', 'gates': self.gates,
                  'capabilities': self.capabilities, 'checks': self.checks}
        (out / 'gates.json').write_text(json.dumps(report, indent=2) + '\n')
        results = {**self.gates, **self.checks}
        suite = ET.Element('testsuite', name='Ported Entware KAS', tests=str(len(results)),
                           failures=str(sum(v['status'] == 'FAIL' for v in results.values())),
                           skipped=str(sum(v['status'] in ('BLOCKED', 'NOT_TESTED') for v in results.values())))
        lines = ['| Gate | Status | Evidence |', '|---|---|---|']
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
        (out / 'summary.md').write_text('\n'.join(lines) + '\n\nPhysical-router acceptance: NOT_TESTED.\n')
