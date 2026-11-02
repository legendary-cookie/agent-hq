"""Minimal protobuf wire reader, based on llm-usage-metrics v0.12.1 (MIT)."""


def _varint(data, offset):
    value = 0
    for index in range(10):
        if offset >= len(data):
            raise ValueError('truncated varint')
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << (7 * index)
        if not byte & 128:
            return value, offset
    raise ValueError('varint too long')


def read_fields(data: bytes) -> dict[int, list[int | bytes]]:
    fields = {}
    offset = 0
    while offset < len(data):
        key, offset = _varint(data, offset)
        number, wire = divmod(key, 8)
        if not number or key > 2**53:
            raise ValueError('invalid field number')
        if wire == 0:
            value, offset = _varint(data, offset)
            if value <= 2**53:
                fields.setdefault(number, []).append(value)
        elif wire == 2:
            length, offset = _varint(data, offset)
            end = offset + length
            if end > len(data):
                raise ValueError('truncated bytes')
            fields.setdefault(number, []).append(data[offset:end])
            offset = end
        elif wire in (1, 5):
            offset += 8 if wire == 1 else 4
            if offset > len(data):
                raise ValueError('truncated fixed field')
        else:
            raise ValueError('unsupported wire type')
    return fields


def first_int(fields, n):
    return next((value for value in fields.get(n, []) if isinstance(value, int)), None)


def first_bytes(fields, n):
    return next((value for value in fields.get(n, []) if isinstance(value, bytes)), None)
