"""Bounded non-interlaced PNG decoder used before publishing image/png references."""

import struct
import zlib


def validate_png(data: bytes) -> tuple[int, int]:
    """Decode scanlines, filters and palette indices, not just a signature check."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("invalid_png")
    cursor = 8
    width = height = depth = color = 0
    compressed: list[bytes] = []
    palette: bytes | None = None
    seen_idat = ended_idat = ended = False
    while cursor < len(data):
        if len(data) - cursor < 12:
            raise ValueError("invalid_png")
        size = struct.unpack_from(">I", data, cursor)[0]
        kind = data[cursor + 4 : cursor + 8]
        end = cursor + 12 + size
        if end > len(data) or any(not (65 <= char <= 90 or 97 <= char <= 122) for char in kind):
            raise ValueError("invalid_png")
        body = data[cursor + 8 : cursor + 8 + size]
        crc = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise ValueError("invalid_png")
        if cursor == 8 and kind != b"IHDR":
            raise ValueError("invalid_png")
        if kind == b"IHDR":
            if cursor != 8 or size != 13:
                raise ValueError("invalid_png")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", body
            )
            if (
                not width
                or not height
                or width * height > 16 * 1024**2
                or compression
                or filtering
                or interlace
            ):
                raise ValueError("unsupported_png_profile")
            allowed = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if color not in allowed or depth not in allowed[color]:
                raise ValueError("invalid_png")
        elif kind == b"PLTE":
            if (
                palette is not None
                or seen_idat
                or not size
                or size % 3
                or size > 768
                or color in (0, 4)
            ):
                raise ValueError("invalid_png")
            palette = body
        elif kind == b"IDAT":
            if ended_idat:
                raise ValueError("invalid_png")
            seen_idat = True
            compressed.append(body)
        elif kind == b"IEND":
            if size or not seen_idat or end != len(data):
                raise ValueError("invalid_png")
            ended = True
        else:
            if kind not in (b"tEXt", b"pHYs", b"sRGB", b"gAMA", b"cHRM", b"tRNS"):
                raise ValueError("unsupported_png_profile")
            if kind == b"pHYs" and (size != 9 or body[-1] not in (0, 1)):
                raise ValueError("invalid_png")
            if kind == b"sRGB" and (size != 1 or body[0] > 3):
                raise ValueError("invalid_png")
            if kind == b"gAMA" and (size != 4 or body == b"\0\0\0\0"):
                raise ValueError("invalid_png")
            if kind == b"cHRM" and size != 32:
                raise ValueError("invalid_png")
            if kind == b"tEXt":
                key, separator, _ = body.partition(b"\0")
                if not separator or not 1 <= len(key) <= 79:
                    raise ValueError("invalid_png")
            if kind == b"tRNS" and (
                seen_idat
                or color not in (0, 2, 3)
                or color == 0
                and size != 2
                or color == 2
                and size != 6
                or color == 3
                and (palette is None or not 1 <= size <= len(palette) // 3)
            ):
                raise ValueError("invalid_png")
            if seen_idat:
                ended_idat = True
        cursor = end
    if not ended or color == 3 and (palette is None or len(palette) // 3 > 2**depth):
        raise ValueError("invalid_png")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    row_bytes = (width * depth * channels + 7) // 8
    expected = (row_bytes + 1) * height
    if expected > 64 * 1024**2:
        raise ValueError("png_decoded_limit")
    decoder = zlib.decompressobj()
    decoded = decoder.decompress(b"".join(compressed), expected + 1)
    if (
        len(decoded) != expected
        or not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
    ):
        raise ValueError("invalid_png")
    previous = bytearray(row_bytes)
    stride = max(1, (depth * channels + 7) // 8)
    cursor = 0
    for _ in range(height):
        mode = decoded[cursor]
        cursor += 1
        row = bytearray(decoded[cursor : cursor + row_bytes])
        cursor += row_bytes
        if mode not in (0, 1, 2, 3, 4):
            raise ValueError("invalid_png")
        for index in range(row_bytes) if mode else ():
            left = row[index - stride] if index >= stride else 0
            above = previous[index]
            diagonal = previous[index - stride] if index >= stride else 0
            if mode == 1:
                row[index] = (row[index] + left) & 255
            elif mode == 2:
                row[index] = (row[index] + above) & 255
            elif mode == 3:
                row[index] = (row[index] + (left + above) // 2) & 255
            elif mode == 4:
                estimate = left + above - diagonal
                distances = (abs(estimate - left), abs(estimate - above), abs(estimate - diagonal))
                predictor = (left, above, diagonal)[distances.index(min(distances))]
                row[index] = (row[index] + predictor) & 255
        if color == 3:
            assert palette is not None
            count = len(palette) // 3
            mask = (1 << depth) - 1
            for pixel in range(width):
                value = (row[(pixel * depth) // 8] >> (8 - depth - (pixel * depth) % 8)) & mask
                if value >= count:
                    raise ValueError("invalid_png")
        previous = row
    return width, height
