"""Reference vectors from a separate C translation of the supplied PE instructions.

Primitive RVA 0x6cc0, arithmetic instructions 0x6dc5..0x6e97; encrypt wrapper
0x8c7b..0x8cb7 uses three keys in ascending order. Synthetic data only.
"""
import hashlib
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from esurfing.crypto import (Algorithm, PE, ProtocolError, UnsupportedAlgorithm,
                             modified_tea_encrypt, modified_tea_decrypt, unpack_package)

ROOT = Path(__file__).resolve().parents[1]


class TeaTests(unittest.TestCase):
    def test_instruction_reference_vectors(self):
        key = struct.unpack(">4I", bytes(range(16)))
        for plain, encrypted in [
            ("0000000000000000", "e23e6285ba2b801c"),
            ("0123456789abcdef", "a04b8ece5fc71c84"),
            ("ffffffffffffffff", "2bdd49034ced43df"),
        ]:
            with self.subTest(plain=plain):
                self.assertEqual(modified_tea_encrypt(bytes.fromhex(plain), key).hex(), encrypted)
                self.assertEqual(modified_tea_decrypt(bytes.fromhex(encrypted), key, ">").hex(), plain)

    def test_three_pass_wire_reference(self):
        algorithm = Algorithm("test", bytes(range(48)), "triple-modified-tea")
        wire = b"2925DAF856FADB353698584F5827E57926252EF568A52846"
        self.assertEqual(algorithm.encrypt(b"<request>test</request>"), wire)
        self.assertEqual(algorithm.decrypt(wire), b"<request>test</request>")
        for plain in [b"", b"12345678", "测试认证".encode()]:
            encrypted = algorithm.encrypt(plain)
            self.assertEqual(len(encrypted), (len(plain)//8+1)*16)
            self.assertEqual(algorithm.decrypt(encrypted), plain)
        with self.assertRaises(ProtocolError):
            algorithm.decrypt(algorithm._transform(b"a\0b\0\0\0\0\0").hex().encode())

    def test_downloaded_template_and_rotating_key(self):
        path = ROOT / "analysis/algorithm-openwrt.bin"
        if not path.exists():
            self.skipTest("local algorithm fixture not present")
        package = path.read_bytes()
        self.assertEqual(hashlib.sha256(package).hexdigest(),
                         "2ad919c1e56db4cdf9b5af7210ab6102903974844b7840d67d583f499053f7bc")
        identifier, image = unpack_package(package)
        self.assertEqual(hashlib.sha256(image).hexdigest(),
                         "78ee94d9c7be402c339afcd39555b7e1c5888ee7c1289138840e14d21ca332b9")
        algorithm = Algorithm.from_package(package)
        self.assertEqual(algorithm.identifier, "a57b329c-32fb-11f1-a14c-fa163e7b1075")
        self.assertEqual(algorithm.cipher, "triple-modified-tea")
        pe = PE(image)
        self.assertEqual(algorithm.key, pe.read(0xf0f1, 48))
        # Same executable template may carry new key material in .data.
        rva, raw, _ = pe.sections[b".data"]
        changed = bytearray(image)
        key_offset = raw + 0xf0f1 - rva
        changed[key_offset:key_offset+48] = bytes(range(48))
        with patch("esurfing.crypto.unpack_package", return_value=(identifier, bytes(changed))):
            self.assertEqual(Algorithm.from_package(b"unused").key, bytes(range(48)))
        for address in [0x1000, 0xc000, 0xf1db, 0xf1d9, 0xf1de]:
            with self.subTest(address=hex(address)):
                changed = bytearray(image)
                for rva, raw, size in pe.sections.values():
                    if rva <= address < rva + size:
                        changed[raw + address - rva] = 255
                        break
                with patch("esurfing.crypto.unpack_package", return_value=(identifier, bytes(changed))):
                    with self.assertRaises(UnsupportedAlgorithm):
                        Algorithm.from_package(b"unused")
