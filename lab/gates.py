"""Public evidence contains fixed labels only, never guest text or credentials."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

GATES = ('BOOT_READY', 'AUTH_READY', 'ENTWARE_READY', 'KAS_PREFLIGHT',
         'KAS_INSTALL', 'KAS_DOCTOR', 'KAS_REBOOT', 'KAS_ROLLBACK', 'KAS_UNINSTALL')
STATUSES = ('PASS', 'FAIL', 'BLOCKED', 'NOT_TESTED')
REASONS = {'unattempted', 'prerequisite_blocked', 'boot_stages_observed',
           'authenticated_version_and_reboot', 'authentication_rejected',
           'serial_timeout', 'runner_failure', 'ext4_opkg_abi_and_reboot',
           'entware_install_failed', 'required_component_absent',
           'runtime_prerequisites_passed', 'native_installer_passed',
           'native_doctor_passed', 'services_restored_after_reboot',
           'native_rollback_passed', 'native_uninstall_passed'}


class Evidence:
    def __init__(self):
        self.gates = {name: {'status': 'NOT_TESTED', 'reason': 'unattempted'} for name in GATES}

    def set(self, name, status, reason):
        if name not in GATES or status not in STATUSES or reason not in REASONS:
            raise ValueError('unapproved public evidence label')
        self.gates[name] = {'status': status, 'reason': reason}

    def write(self, directory):
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        report = {'schema': 1, 'platform': 'WR3000P_PORTED_5_1_8_QEMU',
                  'hardware_acceptance': 'NOT_TESTED', 'gates': self.gates}
        (out / 'gates.json').write_text(json.dumps(report, indent=2) + '\n')
        suite = ET.Element('testsuite', name='Ported Entware KAS', tests=str(len(GATES)),
                           failures=str(sum(v['status'] == 'FAIL' for v in self.gates.values())),
                           skipped=str(sum(v['status'] in ('BLOCKED', 'NOT_TESTED') for v in self.gates.values())))
        lines = ['| Gate | Status | Evidence |', '|---|---|---|']
        for name, value in self.gates.items():
            case = ET.SubElement(suite, 'testcase', name=name)
            if value['status'] == 'FAIL':
                ET.SubElement(case, 'failure', message=value['reason'])
            elif value['status'] in ('BLOCKED', 'NOT_TESTED'):
                ET.SubElement(case, 'skipped', message=value['status'] + ': ' + value['reason'])
            lines.append('| %s | %s | %s |' % (name, value['status'], value['reason']))
        ET.ElementTree(suite).write(out / 'junit.xml', encoding='utf-8', xml_declaration=True)
        (out / 'summary.md').write_text('\n'.join(lines) + '\n\nPhysical-router acceptance: NOT_TESTED.\n')
