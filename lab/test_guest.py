"""Fast host regression: the EXT4 persistence marker is written exactly once.

A repeated no-argument entware() probe (the preflight ext4 capability check)
used to regenerate /opt/.lab-persistence, so the later Web reboot check
compared the file against a value the disk never held and failed with the
PERSISTENCE category although EXT4 persistence itself was intact.
"""
import json
import pathlib
import re
import unittest

from lab.guest import Guest

MARKER_PATH = 'cat /opt/.lab-persistence'
MARKER_WRITE = re.compile(r"printf '%s' '([0-9a-f]{32})' > /opt/\.lab-persistence; sync")


class GuestMarkerContractTests(unittest.TestCase):
    def setUp(self):
        lock = json.loads(pathlib.Path(__file__).with_name('entware.lock.json').read_text())
        self.installed = '\n'.join('%s - %s' % (p['Package'], p['Version'])
                                   for p in lock['packages'])
        self.written = []

    def fake_guest(self, marker):
        guest = Guest.__new__(Guest)
        state = {'marker': marker}

        def execute(text, timeout=30, input_data=None, on_stdout=None):
            if text == 'cat /opt/etc/config/dropbear.conf':
                return 0, 'PORT=222\nPIDFILE="/var/run/lab-dropbear.pid"', ''
            if text == 'uname -m':
                return 0, 'aarch64', ''
            if text == '/bin/sh -c "id -u"':
                return 0, '0', ''
            if text == 'cat /proc/mounts':
                return 0, '/dev/sda1 /opt ext4 rw,relatime 0 0', ''
            if text == '/opt/bin/opkg --version':
                return 0, 'opkg version 0.3.4-entware', ''
            if text == '/opt/bin/opkg print-architecture':
                return 0, 'all\naarch64-3.10', ''
            if text == '/opt/bin/opkg list-installed':
                return 0, self.installed, ''
            if text == MARKER_PATH:
                if state['marker'] is None:
                    return 1, '', "cat: can't open '/opt/.lab-persistence'"
                return 0, state['marker'], ''
            match = MARKER_WRITE.fullmatch(text)
            if match:
                self.assertIsNone(state['marker'],
                                  'marker file must never be regenerated')
                state['marker'] = match[1]
                self.written.append(match[1])
                return 0, '', ''
            raise AssertionError('unexpected guest command: %r' % text)

        guest.execute = execute
        return guest

    def test_repeated_probe_adopts_existing_marker_and_never_rewrites(self):
        guest = self.fake_guest(None)
        first = guest.entware()
        self.assertRegex(first, r'^[0-9a-f]{32}$')
        # Preflight-style repeat probe and the post-reset verification must
        # both keep the original file content.
        self.assertEqual(guest.entware(), first)
        self.assertEqual(guest.entware(first), first)
        self.assertEqual(self.written, [first])

    def test_mismatched_marker_still_fails_the_persistence_check(self):
        guest = self.fake_guest('f' * 32)
        with self.assertRaises(RuntimeError) as caught:
            guest.entware('a' * 32)
        self.assertEqual(str(caught.exception),
                         'EXT4 marker did not survive reboot')
        self.assertEqual(self.written, [])


if __name__ == '__main__':
    unittest.main()
