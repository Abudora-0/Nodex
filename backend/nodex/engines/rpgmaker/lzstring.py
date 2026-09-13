"""LZString, the compression RPG Maker MV uses for its save files.

An `.rpgsave` is `LZString.compressToBase64(JSON.stringify(save))`, so reading
one means reimplementing the algorithm. This is a direct port of the reference
JavaScript (pieroxy/lz-string), kept deliberately close to the original
structure - the bit-packing is fiddly and a "tidier" rewrite is far more likely
to be subtly wrong than to be an improvement.

There is a PyPI package for this, but importing it calls
`future.standard_library.install_aliases()`, which monkey-patches the standard
library process-wide. That is not acceptable inside a long-running server, so
the algorithm lives here instead. The test suite still checks this
implementation against that package when it happens to be installed, and
against every real save file in the corpus.

The acceptance bar is byte-identical round-tripping: re-compressing a decoded
save must reproduce the original file exactly.
"""

from __future__ import annotations

KEY_BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="

_BASE64_INDEX = {char: index for index, char in enumerate(KEY_BASE64)}


class LZStringError(ValueError):
    """Raised when a stream is not valid LZString data."""


def _to_code_units(text: str) -> str:
    """Re-express a string the way JavaScript sees it: as UTF-16 code units.

    LZString stores literals via `charCodeAt`, which yields UTF-16 code units,
    so an emoji is *two* characters to it. Python strings index by code point
    instead, so U+1F3AE is a single character that does not fit the 16-bit
    literal encoding and would silently truncate to U+F3AE.

    Splitting astral characters into their surrogate pair first makes the two
    languages agree. Python tolerates lone surrogates inside a `str`, which is
    what makes this representable at all. For text in the basic plane - which
    is nearly every save - this is an identity transform.
    """
    if text.isascii():
        return text
    encoded = text.encode("utf-16-le", "surrogatepass")
    return "".join(
        chr(int.from_bytes(encoded[i : i + 2], "little"))
        for i in range(0, len(encoded), 2)
    )


def _from_code_units(text: str) -> str:
    """Reassemble surrogate pairs produced by `_to_code_units`."""
    if text.isascii():
        return text
    encoded = b"".join(ord(char).to_bytes(2, "little") for char in text)
    return encoded.decode("utf-16-le", "surrogatepass")


def decompress_from_base64(value: str) -> str | None:
    """Decode a base64 LZString stream. Returns None for malformed input."""
    if value is None:
        return ""
    if value == "":
        return None

    def next_value(index: int) -> int:
        # Characters outside the alphabet (whitespace, stray padding) read as
        # zero, matching indexOf returning -1 &-ed into the bit reader.
        return _BASE64_INDEX.get(value[index], 0)

    result = _decompress(len(value), 32, next_value)
    return _from_code_units(result) if result else result


def compress_to_base64(value: str) -> str:
    """Encode a string the way `LZString.compressToBase64` does."""
    if value is None:
        return ""

    compressed = _compress(_to_code_units(value), 6, lambda index: KEY_BASE64[index])

    # The reference implementation pads to a multiple of four.
    remainder = len(compressed) % 4
    if remainder == 0:
        return compressed
    return compressed + "=" * (4 - remainder)


def _compress(uncompressed: str, bits_per_char: int, to_char) -> str:
    if uncompressed is None:
        return ""

    dictionary: dict[str, int] = {}
    pending: dict[str, bool] = {}
    context_w = ""
    enlarge_in = 2
    dict_size = 3
    num_bits = 2

    data: list[str] = []
    data_val = 0
    data_position = 0

    def emit_bits(value: int, count: int) -> None:
        """Push `count` low bits of `value`, least significant first."""
        nonlocal data_val, data_position
        for _ in range(count):
            data_val = (data_val << 1) | (value & 1)
            if data_position == bits_per_char - 1:
                data_position = 0
                data.append(to_char(data_val))
                data_val = 0
            else:
                data_position += 1
            value >>= 1

    def emit_zero_bits(count: int) -> None:
        nonlocal data_val, data_position
        for _ in range(count):
            data_val = data_val << 1
            if data_position == bits_per_char - 1:
                data_position = 0
                data.append(to_char(data_val))
                data_val = 0
            else:
                data_position += 1

    def emit_one_then_zero_bits(count: int) -> None:
        """First bit set, the rest clear - the wide-character marker."""
        nonlocal data_val, data_position
        value = 1
        for _ in range(count):
            data_val = (data_val << 1) | value
            if data_position == bits_per_char - 1:
                data_position = 0
                data.append(to_char(data_val))
                data_val = 0
            else:
                data_position += 1
            value = 0

    def write_word(word: str) -> None:
        """Emit a dictionary entry, defining it first if it is new."""
        nonlocal enlarge_in, num_bits
        if word in pending:
            code = ord(word[0])
            if code < 256:
                emit_zero_bits(num_bits)
                emit_bits(code, 8)
            else:
                emit_one_then_zero_bits(num_bits)
                emit_bits(code, 16)
            enlarge_in -= 1
            if enlarge_in == 0:
                enlarge_in = 2**num_bits
                num_bits += 1
            del pending[word]
        else:
            emit_bits(dictionary[word], num_bits)

    for char in uncompressed:
        if char not in dictionary:
            dictionary[char] = dict_size
            dict_size += 1
            pending[char] = True

        context_wc = context_w + char
        if context_wc in dictionary:
            context_w = context_wc
            continue

        write_word(context_w)

        enlarge_in -= 1
        if enlarge_in == 0:
            enlarge_in = 2**num_bits
            num_bits += 1

        dictionary[context_wc] = dict_size
        dict_size += 1
        context_w = char

    if context_w != "":
        write_word(context_w)
        enlarge_in -= 1
        if enlarge_in == 0:
            enlarge_in = 2**num_bits
            num_bits += 1

    # End-of-stream marker.
    emit_bits(2, num_bits)

    # Flush whatever is left in the part-filled character.
    while True:
        data_val = data_val << 1
        if data_position == bits_per_char - 1:
            data.append(to_char(data_val))
            break
        data_position += 1

    return "".join(data)


def _decompress(length: int, reset_value: int, next_value) -> str | None:
    dictionary: dict[int, str] = {}
    enlarge_in = 4
    dict_size = 4
    num_bits = 3
    result: list[str] = []

    for index in range(3):
        dictionary[index] = index  # type: ignore[assignment]

    data_val = next_value(0)
    data_position = reset_value
    data_index = 1

    def read_bits(count: int) -> int:
        """Read `count` bits, least significant first."""
        nonlocal data_val, data_position, data_index
        bits = 0
        maxpower = 2**count
        power = 1
        while power != maxpower:
            resb = data_val & data_position
            data_position >>= 1
            if data_position == 0:
                data_position = reset_value
                data_val = next_value(data_index)
                data_index += 1
            bits |= (1 if resb > 0 else 0) * power
            power <<= 1
        return bits

    first = read_bits(2)
    if first == 0:
        character = chr(read_bits(8))
    elif first == 1:
        character = chr(read_bits(16))
    elif first == 2:
        return ""
    else:
        raise LZStringError("unrecognised stream header")

    dictionary[3] = character
    word = character
    result.append(character)

    while True:
        if data_index > length:
            return ""

        code = read_bits(num_bits)

        if code == 0:
            dictionary[dict_size] = chr(read_bits(8))
            dict_size += 1
            code = dict_size - 1
            enlarge_in -= 1
        elif code == 1:
            dictionary[dict_size] = chr(read_bits(16))
            dict_size += 1
            code = dict_size - 1
            enlarge_in -= 1
        elif code == 2:
            return "".join(result)

        if enlarge_in == 0:
            enlarge_in = 2**num_bits
            num_bits += 1

        if code in dictionary:
            entry = dictionary[code]
        elif code == dict_size:
            # The classic LZW special case: the code refers to the entry we are
            # about to build.
            entry = word + word[0]
        else:
            return None

        result.append(entry)

        dictionary[dict_size] = word + entry[0]
        dict_size += 1
        enlarge_in -= 1

        word = entry

        if enlarge_in == 0:
            enlarge_in = 2**num_bits
            num_bits += 1
