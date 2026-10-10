"""Decode the observed AutoZSM envelope and emulate its cipher, never execute PE code."""
import hashlib
import re
import struct
import uuid
from dataclasses import dataclass
from .des import TABLES, des, schedule

ZERO_ID = "00000000-0000-0000-0000-000000000000"
PROBE = b"tarce log algo auto compstr null value"
MAX_PACKAGE = 2 * 1024 * 1024
PACKAGE_KEY = (0x464b697c, 0x463b6d4d, 0x36496a5a, 0x4774496f,
               0x356d766d, 0x52783633, 0x2d4a4d38, 0x39384335)
SUPPORTED_SECTIONS = {
    b".text": "888c189c7d9ab436a124711524735b6c7ee810678bfa712a59777f1be6d549d1",
    b".rdata": "64764eb5c54a91844d6271f611ec84fe9a39ce8bccfa0ae528274d3db826bd58",
}
TEA_SECTIONS = {
    b".text": "61c7ced307a0c1674a302cd986013720282e4dcdeb04f883e609be2a6e1aa41a",
    b".rdata": "aa38c3f635d92580f82a7b2f58e98181c80cb1518e7cef09870fa4081085869d",
}
TABLE_LOCATIONS = {
    "pc1": 0x11100, "pc2": 0x11040, "ip": 0x113a0, "fp": 0x111e0,
    "expansion": 0x112e0, "p": 0x114a0, "shifts": 0xe170, "sbox": 0xd970,
}


class ProtocolError(Exception):
    """A sanitized, user-displayable protocol error."""


class UnsupportedAlgorithm(ProtocolError):
    pass


class PE:
    def __init__(self, data):
        self.data = data
        self.sections = {}
        try:
            if data[:2] != b"MZ":
                raise ValueError()
            p = struct.unpack_from("<I", data, 60)[0]
            if data[p:p+4] != b"PE\0\0":
                raise ValueError()
            machine, count = struct.unpack_from("<HH", data, p+4)
            if machine != 0x8664 or not 1 <= count <= 32:
                raise ValueError()
            offset = p + 24 + struct.unpack_from("<H", data, p+20)[0]
            for i in range(count):
                name, virtual_size, rva, size, raw = struct.unpack_from("<8sIIII", data, offset+i*40)
                name = name.rstrip(b"\0")
                if raw + size > len(data) or name in self.sections:
                    raise ValueError()
                self.sections[name] = (rva, raw, size)
        except (struct.error, ValueError):
            raise ProtocolError("Invalid algorithm PE image") from None

    def read(self, rva, length):
        for start, raw, size in self.sections.values():
            if start <= rva and rva + length <= start + size:
                return self.data[raw+rva-start:raw+rva-start+length]
        raise ProtocolError("Algorithm data points outside PE sections")


def modified_tea_decrypt(block, key, byte_order="<"):
    left, right = struct.unpack(byte_order + "II", block)
    total = (0x9e3779b9 * 32) & 0xffffffff
    for _ in range(32):
        right = (right - (((left << 4) ^ (left >> 5)) + (left ^ total)
                          + key[(total >> 11) & 3])) & 0xffffffff
        total = (total - 0x9e3779b9) & 0xffffffff
        left = (left - (((right << 4) ^ (right >> 5)) + (right ^ total)
                        + key[total & 3])) & 0xffffffff
    return struct.pack(byte_order + "II", left, right)


def modified_tea_encrypt(block, key, byte_order=">"):
    """Observed TEA variant: addition between the three terms, unlike XTEA."""
    left, right = struct.unpack(byte_order + "II", block)
    total = 0
    for _ in range(32):
        left = (left + (((right << 4) ^ (right >> 5)) + (right ^ total)
                       + key[total & 3])) & 0xffffffff
        total = (total + 0x9e3779b9) & 0xffffffff
        right = (right + (((left << 4) ^ (left >> 5)) + (left ^ total)
                         + key[(total >> 11) & 3])) & 0xffffffff
    return struct.pack(byte_order + "II", left, right)


def unpack_package(package):
    if not 114 < len(package) <= MAX_PACKAGE or package[:3] != b"001":
        raise UnsupportedAlgorithm("Unsupported AutoZSM envelope")
    offset = 3
    strings = []
    for _ in range(2):
        size = package[offset]
        offset += 1
        strings.append(package[offset:offset+size])
        offset += size
        if offset + 9 >= len(package):
            raise ProtocolError("Truncated AutoZSM header")
    digest, identifier = strings
    try:
        if not re.fullmatch(rb"[0-9A-Fa-f]{64}", digest):
            raise ValueError()
        identifier = str(uuid.UUID(identifier.decode("ascii")))
    except (ValueError, UnicodeError):
        raise ProtocolError("Invalid AutoZSM metadata") from None
    props = package[offset:offset+5]
    packed_size = struct.unpack_from("<I", package, offset+5)[0]
    size, kind = packed_size & 0x0fffffff, packed_size >> 28
    if kind != 2:
        raise UnsupportedAlgorithm("Only observed AutoZSM encryption type 2 is supported")
    if not 0 < size <= MAX_PACKAGE:
        raise ProtocolError("Unreasonable algorithm size")
    encrypted = package[offset+9:]
    if len(encrypted) % 8:
        raise ProtocolError("Misaligned AutoZSM ciphertext")
    compressed = bytearray()
    for i in range(0, len(encrypted), 8):
        block = modified_tea_decrypt(encrypted[i:i+8], PACKAGE_KEY[:4])
        compressed.extend(modified_tea_decrypt(block, PACKAGE_KEY[4:]))
    pad = compressed[-1]
    if not 1 <= pad <= 8:
        raise ProtocolError("Invalid AutoZSM padding")
    # Native envelope stores only the length in the last byte; preceding padding is arbitrary.
    del compressed[-pad:]
    return identifier, decompress_image(bytes(compressed), props, size)


def decompress_image(compressed, props, size):
    if not 0 < size <= MAX_PACKAGE or len(props) != 5:
        raise ProtocolError("Invalid LZMA output size/properties")
    prop = props[0]
    lc, remainder = prop % 9, prop // 9
    lp, pb = remainder % 5, remainder // 5
    dictionary = int.from_bytes(props[1:], "little")
    if prop >= 225 or lc + lp > 4 or not 4096 <= dictionary <= 16*1024*1024:
        raise UnsupportedAlgorithm("Unsupported LZMA properties")
    try:
        import lzma
    except ImportError:
        raise ProtocolError("Python lzma module is required (OpenWrt: python3-lzma)") from None
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[{
            "id": lzma.FILTER_LZMA1, "dict_size": dictionary, "lc": lc, "lp": lp, "pb": pb,
        }])
        # The native decoder uses the header size as its output limit (FINISH_ANY).
        # Markerless LZMA can decode extra symbols from range-coder flush bytes if
        # asked for size+1. Such an overrun is not evidence of a malformed package.
        image = decoder.decompress(bytes(compressed), max_length=size)
        while len(image) < size and not decoder.eof and not decoder.needs_input:
            more = decoder.decompress(b"", max_length=size-len(image))
            if not more:
                break
            image += more
    except lzma.LZMAError:
        raise ProtocolError("Cannot decompress algorithm package") from None
    # This size-delimited stream has no LZMA end marker; decoder.eof is normally False.
    if len(image) != size:
        raise ProtocolError(
            f"Algorithm decompressed size mismatch: expected={size}, actual={len(image)}, "
            f"eof={decoder.eof}, needs_input={decoder.needs_input}; use fetch-algorithm to save the package")
    return image


@dataclass(repr=False)
class Algorithm:
    identifier: str
    key: bytes
    cipher: str = "double-3des"

    def __post_init__(self):
        if len(self.key) != 48:
            raise ProtocolError("Expected 48 bytes of algorithm key material")
        if self.cipher == "double-3des":
            self.keys = [schedule(self.key[i:i+8]) for i in range(0, 48, 8)]
        elif self.cipher == "triple-modified-tea":
            self.keys = [struct.unpack(">4I", self.key[i:i+16]) for i in range(0, 48, 16)]
        else:
            raise UnsupportedAlgorithm("Unsupported protocol cipher")

    @classmethod
    def from_package(cls, package):
        identifier, image = unpack_package(package)
        pe = PE(image)
        hashes = {}
        for name in SUPPORTED_SECTIONS:
            if name not in pe.sections:
                raise UnsupportedAlgorithm("Missing known algorithm section")
            _, raw, size = pe.sections[name]
            hashes[name] = hashlib.sha256(image[raw:raw+size]).hexdigest()
        if hashes == SUPPORTED_SECTIONS:
            cipher, base, metadata = "double-3des", 0x115c0, 0x116b9
            for name, rva in TABLE_LOCATIONS.items():
                expected = TABLES[name]
                table = struct.unpack("<" + "I"*len(expected), pe.read(rva, 4*len(expected)))
                if list(table) != expected:
                    raise UnsupportedAlgorithm("Server supplied different DES tables")
        elif hashes == TEA_SECTIONS:
            cipher, base, metadata = "triple-modified-tea", 0xf0e0, 0xf1d9
        else:
            raise UnsupportedAlgorithm(
                "Server supplied a different algorithm template; use fetch-algorithm to save the package")
        length = pe.read(metadata + 2, 1)[0]
        iv_length = pe.read(metadata, 1)[0]
        offset = pe.read(metadata + 5, 1)[0]
        if length != 48 or iv_length != 0 or offset + length + 7 > 256:
            raise UnsupportedAlgorithm("Unsupported algorithm key layout or mode")
        return cls(identifier, pe.read(base + offset, length), cipher)

    def _transform(self, raw, decrypt=False):
        if self.cipher == "triple-modified-tea":
            transform = modified_tea_decrypt if decrypt else modified_tea_encrypt
            keys = self.keys[::-1] if decrypt else self.keys
            out = bytearray()
            for i in range(0, len(raw), 8):
                block = raw[i:i+8]
                for key in keys:
                    block = transform(block, key, ">")
                out.extend(block)
            return bytes(out)
        order = [(2, True), (1, False), (0, True), (5, True), (4, False), (3, True)] if decrypt else [
            (3, False), (4, True), (5, False), (0, False), (1, True), (2, False)]
        out = bytearray()
        for i in range(0, len(raw), 8):
            block = raw[i:i+8]
            for index, reverse in order:
                keys = self.keys[index]
                block = des(block, keys[::-1] if reverse else keys)
            out.extend(block)
        return bytes(out)

    def encrypt(self, plaintext):
        if b"\0" in plaintext:
            raise ProtocolError("XML contains a NUL byte")
        padded = plaintext + b"\0" * (8 - len(plaintext) % 8)
        return self._transform(padded).hex().upper().encode("ascii")

    def decrypt(self, body):
        if not body or len(body) > MAX_PACKAGE or len(body) % 16 or not re.fullmatch(rb"[0-9A-Fa-f]+", body):
            raise ProtocolError("Invalid hexadecimal protocol body")
        raw = self._transform(bytes.fromhex(body.decode("ascii")), decrypt=True)
        plaintext, marker, padding = raw.partition(b"\0")
        if not marker or len(padding) > 7 or padding != b"\0"*len(padding):
            raise ProtocolError("Invalid protocol NUL padding")
        return plaintext


def checksum(body):
    return hashlib.md5(body).hexdigest()
