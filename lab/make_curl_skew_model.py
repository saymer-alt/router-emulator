#!/usr/bin/env python3
"""Deterministic, *synthetic* Entware libcurl skew fixture. NEVER a release IPK.

Build from SHA256-pinned REAL libcurl 8.23.0-1 IPK. In the copied ELF .dynstr
rename the curl_multi_get_offt symbol to curl_multi_get_offu (one byte) and
label the new IPK as 8.15.0-2. It is NOT the historical 8.15 library.
No extraction to disk, network, elevated permissions or installation.
"""
import argparse
import copy
import gzip
import hashlib
import io
import re
import struct
import tarfile
from pathlib import Path

SOURCE_SHA256 = {
    "aarch64-3.10": "d5d3eca432e020efd92a3dc063bb5c4b2433190ce36680964451b9d76333eec1",
    "mipsel-3.4": "f2f5f3cfd08413806c7970305d3aa4f52a3bd828c6d06fc99ac58896eb5510c4",
}
MACHINE = {"aarch64-3.10": 183, "mipsel-3.4": 8}
OLD_SYMBOL = b"curl_multi_get_offt\x00"
NEW_SYMBOL = b"curl_multi_get_offu\x00"
TARGET_ELF = "opt/lib/libcurl.so.4.8.0"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _u(data, offset, fmt, endian):
    return struct.unpack_from(endian + fmt, data, offset)[0]


def remove_dynamic_symbol(elf, arch):
    """Modify exactly one symbol in .dynstr with unchanged ELF offsets."""
    if not elf.startswith(b"\x7fELF") or len(elf) < 64:
        raise ValueError("library is not a complete ELF file")
    elf_class, enc = elf[4], elf[5]
    if elf_class not in (1, 2) or enc not in (1, 2):
        raise ValueError("unsupported ELF class or endianness")
    endian = "<" if enc == 1 else ">"
    if _u(elf, 18, "H", endian) != MACHINE[arch]:
        raise ValueError("ELF machine mismatches requested Entware ABI")
    if arch == "aarch64-3.10" and elf_class != 2:
        raise ValueError("AArch64 should have ELFCLASS64")
    if arch == "mipsel-3.4" and (elf_class != 1 or enc != 1):
        raise ValueError("MIPSel should be little-endian ELFCLASS32")
    if elf_class == 2:
        shoff = _u(elf, 40, "Q", endian)
        entsize = _u(elf, 58, "H", endian)
        shnum = _u(elf, 60, "H", endian)
        shnames = _u(elf, 62, "H", endian)
        min_ent = 64
        off_pos, size_pos, size_fmt = 24, 32, "Q"
    else:
        shoff = _u(elf, 32, "I", endian)
        entsize = _u(elf, 46, "H", endian)
        shnum = _u(elf, 48, "H", endian)
        shnames = _u(elf, 50, "H", endian)
        min_ent = 40
        off_pos, size_pos, size_fmt = 16, 20, "I"
    if not shoff or not shnum or not entsize or entsize < min_ent:
        raise ValueError("missing or unsupported ELF section headers")
    if shnum > 1024 or shoff + shnum * entsize > len(elf) or shnames >= shnum:
        raise ValueError("ELF section headers out of bounds")

    def section(index):
        base = shoff + index * entsize
        name_pos = _u(elf, base, "I", endian)
        offset = _u(elf, base + off_pos, size_fmt, endian)
        size = _u(elf, base + size_pos, size_fmt, endian)
        if offset > len(elf) or size > len(elf) - offset:
            raise ValueError("ELF section data out of bounds")
        return name_pos, offset, size

    _, names_off, names_size = section(shnames)
    names = elf[names_off:names_off + names_size]
    candidates = []
    for i in range(shnum):
        name_pos, off, length = section(i)
        if name_pos >= len(names):
            raise ValueError("invalid ELF section name offset")
        end = names.find(b"\x00", name_pos)
        if end < 0:
            raise ValueError("unterminated ELF section name")
        if names[name_pos:end] == b".dynstr":
            candidates.append((off, length))
    if len(candidates) != 1:
        raise ValueError("expected exactly one .dynstr section")
    off, length = candidates[0]
    contents = elf[off:off + length]
    if contents.count(OLD_SYMBOL) != 1:
        raise ValueError("expected exactly one target exported symbol in .dynstr")
    replaced = contents.replace(OLD_SYMBOL, NEW_SYMBOL, 1)
    if len(replaced) != length:
        raise AssertionError("unexpected string-table size change")
    return elf[:off] + replaced + elf[off + length:]


def _gz(raw):
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", filename="", mtime=0,
                       compresslevel=9) as gz:
        gz.write(raw)
    return out.getvalue()


def _tar(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for member, body in files:
            member = copy.copy(member)
            if body is not None:
                member.size = len(body)
                tar.addfile(member, io.BytesIO(body))
            else:
                tar.addfile(member)
    return out.getvalue()


def _members(raw, label):
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
            members = []
            seen = set()
            for member in tar.getmembers():
                name = member.name.removeprefix("./")
                if name in seen:
                    raise ValueError("duplicate archive member: " + name)
                seen.add(name)
                if member.isfile():
                    if member.size > 32 * 1024 * 1024:
                        raise ValueError("oversized archive member")
                    f = tar.extractfile(member)
                    if f is None:
                        raise ValueError("cannot read archive member " + name)
                    body = f.read()
                else:
                    body = None
                members.append((member, body))
            return members
    except (tarfile.TarError, EOFError) as exc:
        raise ValueError("invalid " + label + " tar archive") from exc


def build_model(real_ipk, arch):
    """Return a deterministic synthetic IPK from verified official bytes."""
    if arch not in SOURCE_SHA256:
        raise ValueError("unsupported target ABI")
    if sha256(real_ipk) != SOURCE_SHA256[arch]:
        raise ValueError("official source IPK SHA256 mismatch")
    if not real_ipk.startswith(b"\x1f\x8b"):
        raise ValueError("expected gzip-compressed Entware IPK")
    outer = _members(real_ipk, "outer")
    found = {"control.tar.gz": 0, "data.tar.gz": 0}
    rebuilt = []
    for member, body in outer:
        name = member.name.removeprefix("./")
        if name in found:
            found[name] += 1
            if not member.isfile() or body is None:
                raise ValueError(name + " is not a regular file")
            inner = _members(body, name)
            changed = 0
            newer = []
            for m, b in inner:
                inner_name = m.name.removeprefix("./")
                if name == "data.tar.gz" and inner_name == TARGET_ELF:
                    if not m.isfile() or b is None:
                        raise ValueError("libcurl ELF is not a regular file")
                    b = remove_dynamic_symbol(b, arch)
                    changed += 1
                if name == "control.tar.gz" and inner_name == "control":
                    if not m.isfile() or b is None:
                        raise ValueError("package control is not a regular file")
                    if not re.search(rb"(?m)^Package: libcurl\r?$", b):
                        raise ValueError("unexpected package name")
                    if not re.search(rb"(?m)^Architecture: " + re.escape(arch.encode()) + rb"\r?$", b):
                        raise ValueError("unexpected package architecture")
                    b, n = re.subn(rb"(?m)^Version: 8\.23\.0-1(?=\r?$)",
                                  b"Version: 8.15.0-2", b)
                    if n != 1:
                        raise ValueError("unexpected package control version")
                    changed += 1
                newer.append((m, b))
            if changed != 1:
                raise ValueError("expected exactly one edited member in " + name)
            body = _gz(_tar(newer))
        rebuilt.append((member, body))
    if found != {"control.tar.gz": 1, "data.tar.gz": 1}:
        raise ValueError("missing or duplicate package control/data archive")
    return _gz(_tar(rebuilt))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--arch", required=True, choices=tuple(SOURCE_SHA256))
    p.add_argument("--source-ipk", required=True, type=Path)
    p.add_argument("--output-ipk", required=True, type=Path)
    args = p.parse_args()
    source = args.source_ipk.read_bytes()
    output = build_model(source, args.arch)
    args.output_ipk.parent.mkdir(parents=True, exist_ok=True)
    with args.output_ipk.open("xb") as f:
        f.write(output)
    print("SYNTHETIC MODEL ONLY (not historical libcurl 8.15.0-2)")
    print("source_sha256=" + sha256(source))
    print("model_sha256=" + sha256(output))
    print("model_path=" + str(args.output_ipk))


if __name__ == "__main__":
    main()
