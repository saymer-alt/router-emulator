import unittest
from lab.preflight import components


class ComponentTests(unittest.TestCase):
    def test_wrapped_native_field_only(self):
        text = '  components: base,dns-filter,opkg-kmod-\n              netfilter,ext,ext-utils,proxy,dns-https\n  model: synthetic\n  running-config: dns-tls,foreign\n'
        self.assertEqual(components(text), {'base', 'dns-filter', 'opkg-kmod-netfilter',
                                          'ext', 'ext-utils', 'proxy', 'dns-https'})

    def test_missing_field_never_joins_unrelated_config(self):
        with self.assertRaises(ValueError):
            components('running-config: dns-filter,proxy,opkg-kmod-netfilter\n')
