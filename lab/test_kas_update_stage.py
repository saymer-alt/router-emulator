"""Host-side unit tests for the kas-update stage helpers; no guest, no QEMU.

The native transaction itself runs only in the emulator; these tests pin the
fixture digest, the mirror route injection, the inertness classifier and the
published exit-code contract so a wiring mistake cannot reach a lab run."""
import hashlib
import unittest
from unittest.mock import MagicMock

from lab.gates import Evidence
from lab.kas_update_e2e import (EXPECTED_EXIT_CODES, OLD_BODY_SHA256,
                                inject_release_routes, inert_state_changes,
                                fixture_old_body, snapshot_inert_state)


class FixtureTests(unittest.TestCase):
    def test_pinned_v186_body_matches_the_recorded_digest(self):
        body = fixture_old_body()
        self.assertEqual(hashlib.sha256(body).hexdigest(), OLD_BODY_SHA256)
        self.assertTrue(body.startswith(b'#!/bin/sh\n'))
        self.assertIn(b'MIHOMO WATCHDOG SCRIPT', body)

    def test_inject_release_routes_only_extends_the_existing_domain(self):
        mirror = MagicMock()
        mirror.routes = {}
        inject_release_routes(mirror)
        keys = list(mirror.routes)
        self.assertEqual(keys, [
            ('https', 'raw.githubusercontent.com',
             '/saymer-alt/keenetic-auto-setup/v1.8.6/mihomo-watchdog.sh')])
        self.assertEqual(hashlib.sha256(mirror.routes[keys[0]]).hexdigest(),
                         OLD_BODY_SHA256)


class InertnessTests(unittest.TestCase):
    def test_identical_state_reports_no_changes(self):
        before = {'config_md5': 'a', 'installed': 'i', 'crontab': 'c',
                  'service_count': '1\n1', 'bypass_rules': '5',
                  'ledger': 'd', 'wrapper_sha': 'w'}
        self.assertEqual(inert_state_changes(before, dict(before)), [])

    def test_each_change_publishes_its_fixed_label(self):
        before = {'config_md5': 'a', 'installed': 'i', 'crontab': 'c',
                  'service_count': '1\n1', 'bypass_rules': '5',
                  'ledger': 'd', 'wrapper_sha': 'w'}
        for field, label in (
                ('config_md5', 'mihomo_config_changed'),
                ('installed', 'magi_config_changed'),
                ('crontab', 'crontab_changed'),
                ('service_count', 'native_services_duplicate'),
                ('bypass_rules', 'netfilter_changed'),
                ('ledger', 'ledger_changed'),
                ('wrapper_sha', 'watchdog_changed')):
            after = dict(before)
            after[field] = 'changed'
            self.assertEqual(inert_state_changes(before, after), [label])

    def test_snapshot_requires_a_guest_command_object(self):
        digest = 'ab' * 32
        class Guest:
            def command(self, text):
                if text.startswith('md5sum'):
                    return 'md5hash  /opt/etc/mihomo/config.yaml'
                if text.startswith('cat'):
                    return 'crontab line'
                if text.startswith('pidof'):
                    return '1'
            def execute(self, text, timeout=60):
                code = 0
                data = '5' if 'grep -c' in text else digest + '  file'
                if 'list-installed' in text:
                    data = 'mihomo - 1.19.33-2\nmagitrickle - 0.8.2-1'
                return code, data, ''
        state = snapshot_inert_state(Guest())
        self.assertEqual(state['config_md5'], 'md5hash')
        self.assertEqual(state['wrapper_sha'], digest)
        self.assertEqual(state['ledger'], digest)
        self.assertIn('mihomo - 1.19.33-2', state['installed'])


class ExitCodeContractTests(unittest.TestCase):
    def test_expected_codes_cover_the_published_scenarios(self):
        self.assertEqual(EXPECTED_EXIT_CODES, {
            'release_upgrade_check': 1, 'release_upgrade_apply': 0,
            'noop_apply': 0, 'idempotent_apply': 0,
            'netfail_check': 2, 'netfail_apply': 1,
            'modified_check': 1, 'modified_apply': 1,
            'foreign_check': 2, 'foreign_apply': 1,
            'commit_failure_apply': 1, 'commit_recovery_apply': 0,
            'busy_apply': 1})


if __name__ == '__main__':
    unittest.main()
