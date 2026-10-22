import struct
import unittest

from lab import provision_ported as provision


class ProvisionTests(unittest.TestCase):
    def test_native_config_entry_and_erased_space(self):
        text = "user synthetic\n    tag cli\n!\n"
        image = provision.config_image(text)
        self.assertEqual(len(image), 0x80000)
        self.assertEqual(image[:4], b"ndm3")
        offset, size, flags, original = struct.unpack_from("<4I", image, 4)
        self.assertEqual((offset, size, flags, original), (0x6200, len(text), 0, len(text)))
        self.assertEqual(image[offset:offset + size], text.encode())
        self.assertEqual(image[32:512], b"\xff" * 480)
        self.assertEqual(image[offset + size:], b"\xff" * (len(image) - offset - size))

    def test_native_password_command_in_private_seed(self):
        token = "SyntheticTokenForRegressionOnly_0123456789"
        text = provision.synthetic_config("admin", token)
        self.assertIn("password plain " + token, text)
        self.assertIn("tag cli", text)
        self.assertNotIn("no password", text)

    def test_invalid_user_or_weak_password_rejected(self):
        for user, password in (("admin\nno password", "a" * 32), ("labadmin", "weak")):
            with self.assertRaises(ValueError):
                provision.synthetic_config(user, password)

    def test_config_size_and_nul_rejected(self):
        for text in ("", "a\0b", "a" * provision.CONFIG_SIZE):
            with self.assertRaises(ValueError):
                provision.config_image(text)
