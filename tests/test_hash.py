"""Unit tests for the pure-Python Cryptomatte hashing (no Blender / NumPy needed).

Run:  python -m unittest discover -s tests
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cryptoray"))
import core  # noqa: E402  (imported directly so the bpy-dependent package __init__ is skipped)


class MurmurHash3(unittest.TestCase):
    # Reference values for MurmurHash3_x86_32, seed 0 (from the reference C++ implementation).
    KNOWN = {
        "": 0x00000000,
        "hello": 0x248BFA47,
        "Hello, world!": 0xC0363E43,
        "The quick brown fox jumps over the lazy dog": 0x2E4FF723,
    }

    def test_known_vectors(self):
        for text, expected in self.KNOWN.items():
            self.assertEqual(core.mm3_32(text), expected, text)

    def test_seed(self):
        self.assertEqual(core.mm3_32(b"", 1), 0x514E28B7)

    def test_tail_lengths(self):
        # every tail length 0..3 must go through the tail path without error and stay 32-bit
        for n in range(9):
            h = core.mm3_32("x" * n)
            self.assertTrue(0 <= h <= 0xFFFFFFFF)

    def test_utf8(self):
        self.assertEqual(core.mm3_32("é"), core.mm3_32("é".encode("utf-8")))


class CryptomatteIds(unittest.TestCase):
    def test_exponent_fix(self):
        for name in ("Cube", "Sphere", "GlassBall", "a", "b", "chair_001", "日本"):
            bits = core.name_to_id(name)
            exp = (bits >> 23) & 0xFF
            self.assertNotIn(exp, (0, 255), name)
            f = core.id_to_float(bits)
            self.assertTrue(f == f and abs(f) != float("inf"), name)

    def test_fix_only_flips_bit23(self):
        for i in range(2000):
            name = "obj%d" % i
            raw, fixed = core.mm3_32(name), core.name_to_id(name)
            self.assertIn(raw ^ fixed, (0, 1 << 23))

    def test_float_roundtrip(self):
        bits = core.name_to_id("Cube")
        self.assertEqual(struct.unpack("<I", struct.pack("<f", core.id_to_float(bits)))[0], bits)

    def test_layer_key(self):
        self.assertEqual(len(core.layer_key("CryptoObject")[:7]), 7)

    def test_collision_check(self):
        core.check_collisions(["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
