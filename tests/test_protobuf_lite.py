import unittest
from hq.protobuf_lite import read_fields, first_int, first_bytes


class ProtobufTests(unittest.TestCase):
    def test_varint_and_bytes(self):
        fields = read_fields(b'\x08\x96\x01\x12\x03abc\x19' + b'\0' * 8 + b'\x25' + b'\0' * 4)
        self.assertEqual(first_int(fields, 1), 150)
        self.assertEqual(first_bytes(fields, 2), b'abc')
        self.assertNotIn(3, fields)

    def test_truncated_raises(self):
        for data in (b'\x80', b'\x0a\x02x', b'\x09x', b'\x0dxx'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                read_fields(data)
