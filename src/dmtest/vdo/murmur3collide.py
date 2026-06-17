"""MurmurHash3 collision generator.

Generates blocks with different data but the same MurmurHash3_x64_128 hash,
using the differential technique described in the SipHash DOS paper
(https://131002.net/siphash/siphashdos_appsec12_slides.pdf).

The technique modifies a 32-byte aligned region (two consecutive 16-byte
MurmurHash3 blocks) by applying carefully chosen single-bit XOR deltas in
k-space. The deltas are chosen so that after the hash's rotate/add/multiply
mixing, the differences cancel perfectly across the two blocks. This works
for ALL inputs, not probabilistically.

For a 4096-byte block with 128 non-overlapping 32-byte chunks, each chunk
can be independently toggled, giving 2^128 (~3.4e38) distinct blocks sharing
the same hash. The operation is an involution: applying it twice to the same
chunk restores the original data (since XOR in k-space is self-inverse).
"""

import os
import struct
from typing import Optional, Sequence

MASK64 = 0xFFFFFFFFFFFFFFFF

C1 = 0x87c37b91114253d5
C2 = 0x4cf5ad432745937f

R1 = 0xa81e14edd9de2c7f  # modular inverse of C2 mod 2^64
R2 = 0xa98409e882ce4d7d  # modular inverse of C1 mod 2^64

D1 = 0x0000001000000000  # bit 36 -> rotl64(.,27) -> bit 63 (MSB)
D2 = 0x0000000100000000  # bit 32 -> rotl64(.,31) -> bit 63 (MSB)
D3 = 0x8000000000000000  # bit 63 -> cancels carry from block j


def _rotl64(x: int, r: int) -> int:
    return ((x << r) | (x >> (64 - r))) & MASK64


def _mul64(a: int, b: int) -> int:
    return (a * b) & MASK64


def _m3forward(k1: int, k2: int) -> tuple[int, int]:
    """MurmurHash3 data-to-K transform (multiply, rotate, multiply)."""
    k1 = _mul64(k1, C1)
    k1 = _rotl64(k1, 31)
    k1 = _mul64(k1, C2)
    k2 = _mul64(k2, C2)
    k2 = _rotl64(k2, 33)
    k2 = _mul64(k2, C1)
    return k1, k2


def _m3backward(k1: int, k2: int) -> tuple[int, int]:
    """Inverse of MurmurHash3 data-to-K transform."""
    k1 = _mul64(k1, R1)
    k1 = _rotl64(k1, 33)
    k1 = _mul64(k1, R2)
    k2 = _mul64(k2, R2)
    k2 = _rotl64(k2, 31)
    k2 = _mul64(k2, R1)
    return k1, k2


def murmur3_collide(data: bytes,
                    chunk_indices: Optional[Sequence[int]] = None) -> bytes:
    """Create a new block with the same MurmurHash3-128 hash but different data.

    Modifies one or more 32-byte chunks in a way that preserves the hash.
    The original data is not modified.

    Args:
        data: Input data block. Length must be a multiple of 32 bytes.
        chunk_indices: Which 32-byte chunk(s) to modify (0-based).
            Defaults to [0]. For a 4096-byte block, valid range is 0..127.

    Returns:
        New bytes object with the same MurmurHash3-128 hash but different data.
    """
    if len(data) % 32 != 0:
        raise ValueError("Data length must be a multiple of 32 bytes")

    n_chunks = len(data) // 32
    if chunk_indices is None:
        chunk_indices = [0]

    for idx in chunk_indices:
        if not 0 <= idx < n_chunks:
            raise ValueError(f"chunk_index {idx} out of range [0, {n_chunks - 1}]")

    result = bytearray(data)

    for idx in chunk_indices:
        offset = idx * 32
        v0, v1, v2, v3 = struct.unpack_from('<4Q', result, offset)

        v0, v1 = _m3forward(v0, v1)
        v2, v3 = _m3forward(v2, v3)

        v0 ^= D1
        v1 ^= D2
        v2 ^= D3

        v0, v1 = _m3backward(v0, v1)
        v2, v3 = _m3backward(v2, v3)

        struct.pack_into('<4Q', result, offset, v0, v1, v2, v3)

    return bytes(result)


def murmurhash3_128(data: bytes, seed: int = 0) -> tuple[int, int]:
    """MurmurHash3_x64_128 hash function.

    Args:
        data: Input bytes to hash.
        seed: Hash seed (32-bit unsigned).

    Returns:
        Tuple of (h1, h2), two 64-bit unsigned integers.
    """
    length = len(data)
    nblocks = length // 16

    h1 = seed & MASK64
    h2 = seed & MASK64

    for i in range(nblocks):
        off = i * 16
        k1, k2 = struct.unpack_from('<2Q', data, off)

        k1 = _mul64(k1, C1)
        k1 = _rotl64(k1, 31)
        k1 = _mul64(k1, C2)
        h1 ^= k1

        h1 = _rotl64(h1, 27)
        h1 = (h1 + h2) & MASK64
        h1 = (h1 * 5 + 0x52dce729) & MASK64

        k2 = _mul64(k2, C2)
        k2 = _rotl64(k2, 33)
        k2 = _mul64(k2, C1)
        h2 ^= k2

        h2 = _rotl64(h2, 31)
        h2 = (h2 + h1) & MASK64
        h2 = (h2 * 5 + 0x38495ab5) & MASK64

    tail = data[nblocks * 16:]
    k1 = 0
    k2 = 0

    tail_len = len(tail)
    if tail_len >= 15: k2 ^= tail[14] << 48
    if tail_len >= 14: k2 ^= tail[13] << 40
    if tail_len >= 13: k2 ^= tail[12] << 32
    if tail_len >= 12: k2 ^= tail[11] << 24
    if tail_len >= 11: k2 ^= tail[10] << 16
    if tail_len >= 10: k2 ^= tail[9] << 8
    if tail_len >= 9:
        k2 ^= tail[8]
        k2 = _mul64(k2, C2)
        k2 = _rotl64(k2, 33)
        k2 = _mul64(k2, C1)
        h2 ^= k2

    if tail_len >= 8: k1 ^= tail[7] << 56
    if tail_len >= 7: k1 ^= tail[6] << 48
    if tail_len >= 6: k1 ^= tail[5] << 40
    if tail_len >= 5: k1 ^= tail[4] << 32
    if tail_len >= 4: k1 ^= tail[3] << 24
    if tail_len >= 3: k1 ^= tail[2] << 16
    if tail_len >= 2: k1 ^= tail[1] << 8
    if tail_len >= 1:
        k1 ^= tail[0]
        k1 = _mul64(k1, C1)
        k1 = _rotl64(k1, 31)
        k1 = _mul64(k1, C2)
        h1 ^= k1

    h1 ^= length
    h2 ^= length
    h1 = (h1 + h2) & MASK64
    h2 = (h2 + h1) & MASK64

    def fmix64(k):
        k ^= k >> 33
        k = _mul64(k, 0xff51afd7ed558ccd)
        k ^= k >> 33
        k = _mul64(k, 0xc4ceb9fe1a85ec53)
        k ^= k >> 33
        return k

    h1 = fmix64(h1)
    h2 = fmix64(h2)
    h1 = (h1 + h2) & MASK64
    h2 = (h2 + h1) & MASK64

    return (h1, h2)


def generate_colliding_blocks(base_block: bytes,
                              count: int,
                              block_size: int = 4096,
                              chain: bool = True):
    """Generate blocks with the same MurmurHash3 hash as base_block.

    Mimics the behavior of the C murmur3collide tool, including the Gray-code
    counter mechanism for chunk selection. This ensures different chunks are
    modified across sequential blocks to preserve compressibility.

    Args:
        base_block: Initial block to transform (must be block_size bytes).
        count: Number of colliding blocks to generate.
        block_size: Size of each block in bytes (must be multiple of 32).
        chain: If True, each block transforms the previous output (like the C
            tool with same input/output file). If False, each block transforms
            the original base_block independently.

    Yields:
        Transformed blocks, one at a time. Each has the same hash as base_block
        but different data.

    Example (Collide02/03 pattern - chaining):
        base = b'\\x00' * 4096
        for block in generate_colliding_blocks(base, 999999, chain=True):
            write_block(block)

    Example (Collide01 pattern - independent):
        first_dataset = [read_block(i) for i in range(1000000)]
        for i, src in enumerate(first_dataset):
            block = next(generate_colliding_blocks(src, 1, chain=False))
            write_block(1000000 + i, block)
    """
    if len(base_block) != block_size:
        raise ValueError(f"base_block must be {block_size} bytes")
    if block_size % 32 != 0:
        raise ValueError(f"block_size must be a multiple of 32")

    n_chunks = block_size // 32
    current_block = base_block
    counter = 0

    for _ in range(count):
        counter += 1
        # Gray-code-like mechanism: use position of first set bit to select chunk.
        # This matches __builtin_ffsl(++counter) % (size / 32) from the C code.
        # Note: bin(counter).rfind('1') gives position of last set bit,
        # but we want first set bit from LSB, which is (counter & -counter).bit_length() - 1
        # Actually, __builtin_ffsl returns 1-indexed position of first set bit.
        # For counter=1 (0b1), ffsl=1. For counter=2 (0b10), ffsl=2.
        # For counter=3 (0b11), ffsl=1. For counter=4 (0b100), ffsl=3.
        first_set_bit_pos = (counter & -counter).bit_length()  # 1-indexed like ffsl
        chunk_index = (first_set_bit_pos - 1) % n_chunks

        # Transform the current block by modifying the selected chunk
        result = murmur3_collide(current_block, chunk_indices=[chunk_index])

        yield result

        if chain:
            # Next iteration transforms this output
            current_block = result
        # else: keep transforming the original base_block


if __name__ == "__main__":
    # Verify hash implementation against known test vectors from the C tests.
    text = b"The quick brown fox jumps over the lazy dog"
    h1, h2 = murmurhash3_128(text, seed=0)
    expected_h1 = 0xe34bbc7bbc071b6c
    expected_h2 = 0x7a433ca9c49a9347
    assert (h1, h2) == (expected_h1, expected_h2), \
        f"Hash mismatch: got ({h1:#x}, {h2:#x}), expected ({expected_h1:#x}, {expected_h2:#x})"
    print(f"Hash verification passed: ({h1:#018x}, {h2:#018x})")

    # Demonstrate collision generation on a 4096-byte block.
    block = os.urandom(4096)
    original_hash = murmurhash3_128(block)
    print(f"\nOriginal block hash: ({original_hash[0]:#018x}, {original_hash[1]:#018x})")

    # Single-chunk collision
    collided = murmur3_collide(block, chunk_indices=[0])
    collided_hash = murmurhash3_128(collided)
    assert collided_hash == original_hash
    assert collided != block
    print(f"Collided (chunk 0):  ({collided_hash[0]:#018x}, {collided_hash[1]:#018x})  data differs: {collided != block}")

    # Multi-chunk collision
    collided2 = murmur3_collide(block, chunk_indices=[0, 5, 42, 100, 127])
    collided2_hash = murmurhash3_128(collided2)
    assert collided2_hash == original_hash
    assert collided2 != block
    assert collided2 != collided
    print(f"Collided (5 chunks): ({collided2_hash[0]:#018x}, {collided2_hash[1]:#018x})  data differs: {collided2 != block}")

    # Verify the operation is an involution (applying twice restores original).
    double_collided = murmur3_collide(collided, chunk_indices=[0])
    assert double_collided == block, "Double collision should restore original"
    print(f"\nDouble collision restores original: True (involution)")

    # Demonstrate combinatorial explosion: modify different subsets of chunks.
    all_indices = list(range(128))
    collided_all = murmur3_collide(block, chunk_indices=all_indices)
    assert murmurhash3_128(collided_all) == original_hash
    print(f"All 128 chunks modified:  hash still matches: True")

    n_chunks = 4096 // 32
    print(f"\nFor a 4096-byte block:")
    print(f"  {n_chunks} independent 32-byte chunks, each with 2 states")
    print(f"  2^{n_chunks} = ~3.4e38 distinct blocks with the same hash")

    # Test the Gray-code generator with chaining
    print(f"\n--- Testing generate_colliding_blocks (chain=True) ---")
    zero_block = b'\x00' * 4096
    base_hash = murmurhash3_128(zero_block)
    print(f"Base block (zeros) hash: ({base_hash[0]:#018x}, {base_hash[1]:#018x})")

    gen = generate_colliding_blocks(zero_block, count=10, chain=True)
    for i, collided_block in enumerate(gen, 1):
        h = murmurhash3_128(collided_block)
        assert h == base_hash, f"Block {i} hash mismatch"
        assert collided_block != zero_block, f"Block {i} same as base"
        print(f"  Block {i}: hash matches, data differs")

    # Test independent transformation (chain=False)
    print(f"\n--- Testing generate_colliding_blocks (chain=False) ---")
    gen2 = generate_colliding_blocks(zero_block, count=5, chain=False)
    prev_blocks = []
    for i, collided_block in enumerate(gen2, 1):
        h = murmurhash3_128(collided_block)
        assert h == base_hash, f"Block {i} hash mismatch"
        # With chain=False, we might see repeats because we're always transforming
        # the same base block with the same Gray-code pattern
        prev_blocks.append(collided_block)
        print(f"  Block {i}: hash matches")

    print("\n✓ All tests passed")
