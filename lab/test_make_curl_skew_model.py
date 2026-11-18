"""No-network tests for deterministic synthetic Entware model construction."""
import struct
import unittest
from unittest.mock import patch

from make_curl_skew_model import (_gz, _tar, _members, sha256, build_model,
                                  remove_dynamic_symbol, SOURCE_SHA256, MACHINE,
                                  OLD_SYMBOL, NEW_SYMBOL, TARGET_ELF)
import tarfile


def member(name, body, mode=0o644):
    ti = tarfile.TarInfo(name)
    ti.mode = mode
    ti.mtime = 1720000000
    ti.size = len(body)
    return ti, body


def elf_blob(arch, symbol=OLD_SYMBOL):
    """Tiny ELF .dynstr fixture; no real libcurl binaries or IPK downloads."""
    is64 = arch == "aarch64-3.10"
    header = bytearray(64 if is64 else 52)
    header[:4] = b"\x7fELF"
    header[4] = 2 if is64 else 1
    header[5] = 1
    header[6] = 1
    struct.pack_into("<H", header, 18, MACHINE[arch])
    names = b"\x00.shstrtab\x00.dynstr\x00"
    dynstr = b"\x00hello\x00" + symbol + b"world\x00"
    namesoff, dynoff, shoff = 128, 192, 384
    size = shoff + 3 * (64 if is64 else 40)
    elf = bytearray(size)
    elf[:len(header)] = header
    elf[namesoff:namesoff + len(names)] = names
    elf[dynoff:dynoff + len(dynstr)] = dynstr
    if is64:
        struct.pack_into("<Q", elf, 40, shoff)
        struct.pack_into("<HHH", elf, 58, 64, 3, 1)
        for idx, name, off, length in ((1, 1, namesoff, len(names)),
                                       (2, 11, dynoff, len(dynstr))):
            struct.pack_into("<IIQQQQIIQQ", elf, shoff + idx * 64,
                             name, 3, 0, 0, off, length, 0, 0, 1, 0)
    else:
        struct.pack_into("<I", elf, 32, shoff)
        struct.pack_into("<HHH", elf, 46, 40, 3, 1)
        for idx, name, off, length in ((1, 1, namesoff, len(names)),
                                       (2, 11, dynoff, len(dynstr))):
            struct.pack_into("<IIIIIIIIII", elf, shoff + idx * 40,
                             name, 3, 0, 0, off, length, 0, 0, 1, 0)
    return bytes(elf)


def package(arch, symbol=OLD_SYMBOL):
    control = ("Package: libcurl\nVersion: 8.23.0-1\nArchitecture: " + arch +
               "\nDepends: libc\n").encode()
    control_gz = _gz(_tar([member("./control", control)]))
    data_gz = _gz(_tar([
        member("./opt/lib/libcurl.so.4.8.0", elf_blob(arch, symbol)),
        member("./opt/lib/libcurl.so.4", b"", 0o777)]))
    return _gz(_tar([member("./control.tar.gz", control_gz),
                     member("./data.tar.gz", data_gz),
                     member("./debian-binary", b"2.0\n")]))


class ModelTests(unittest.TestCase):
    def test_both_abis_are_deterministic(self):
        for arch in SOURCE_SHA256:
            with self.subTest(arch=arch):
                original = package(arch)
                with patch.dict(SOURCE_SHA256, {arch: sha256(original)}):
                    out1 = build_model(original, arch)
                    out2 = build_model(original, arch)
                self.assertEqual(out1, out2)
                self.assertEqual(out1[:2], b"\x1f\x8b")
                archive = dict((x.name.removeprefix("./"), body)
                               for x, body in _members(out1, "test"))
                controls = dict((x.name.removeprefix("./"), body)
                                for x, body in _members(archive["control.tar.gz"], "test"))
                data = dict((x.name.removeprefix("./"), body)
                            for x, body in _members(archive["data.tar.gz"], "test"))
                self.assertIn(b"Version: 8.15.0-2", controls["control"])
                self.assertNotIn(b"Version: 8.23.0-1", controls["control"])
                self.assertNotIn(OLD_SYMBOL, data[TARGET_ELF])
                self.assertIn(NEW_SYMBOL, data[TARGET_ELF])

    def test_digest_mismatch_refused(self):
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            build_model(package("aarch64-3.10"), "aarch64-3.10")

    def test_symbol_absent_refused(self):
        arch = "aarch64-3.10"
        original = package(arch, b"something_else\x00")
        with patch.dict(SOURCE_SHA256, {arch: sha256(original)}):
            with self.assertRaisesRegex(ValueError, "target exported symbol"):
                build_model(original, arch)

    def test_wrong_elf_arch_refused(self):
        with self.assertRaisesRegex(ValueError, "ELF machine"):
            remove_dynamic_symbol(elf_blob("aarch64-3.10"), "mipsel-3.4")

    def test_missing_control_refused(self):
        arch = "mipsel-3.4"
        raw = _gz(_tar([member("./data.tar.gz", b"hello")]))
        with patch.dict(SOURCE_SHA256, {arch: sha256(raw)}):
            with self.assertRaises(ValueError):
                build_model(raw, arch)

    def test_unsupported_arch_refused(self):
        with self.assertRaises(ValueError):
            build_model(b"", "mips-3.4")


if __name__ == "__main__":
    unittest.main()
