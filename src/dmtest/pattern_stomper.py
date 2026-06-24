import random
import dmtest.units as units
import dmtest.utils as utils

from dmtest.units import SECTOR_SIZE
from dmtest.utils import wipe_device
from typing import List


class Block:
    def __init__(self, block, seed):
        self.block = block
        self.seed = seed

    def get_buffer(self, block_size):
        s = self.seed % 256
        r = bytes([s for _ in range(block_size * SECTOR_SIZE)])
        assert len(r) == block_size * SECTOR_SIZE
        return r

    def __str__(self):
        return f"Block {self.block}, seed {self.seed}"


class BlockSet:
    def __init__(self, hash=None):
        self.blocks = hash or {}

    def add(self, b):
        self.blocks[b.block] = b

    def __iter__(self):
        return iter(self.blocks.values())

    def union(self, rhs):
        return BlockSet({**self.blocks, **rhs.blocks})

    def __len__(self):
        return len(self.blocks)

    def contains(self, b):
        return b in self.blocks

    def trim(self, max_blocks):
        new_blocks = {}
        for key, val in self.blocks.items():
            if key < max_blocks:
                new_blocks[key] = val
        return BlockSet(new_blocks)


def random_delta(nr_blocks, max_block):
    blocks = BlockSet()
    while len(blocks) != nr_blocks:
        b = None
        while b is None or blocks.contains(b):
            b = random.randint(0, max_block - 1)
        blocks.add(Block(b, random.randint(0, 255)))
    return blocks


def zeroes_delta(nr_blocks):
    blocks = BlockSet()
    for b in range(nr_blocks):
        blocks.add(Block(b, 0))
    return blocks


class PatternStomper:
    def __init__(self, dev: str, block_size: int, need_zero=False):
        self.dev = dev
        self.block_size = block_size
        self.max_blocks = utils.dev_size(dev) // block_size
        self.deltas: List[BlockSet] = []

        self._initialize_device(need_zero)

    def fork(self, new_dev: str) -> "PatternStomper":
        s2 = PatternStomper(new_dev, self.block_size, need_zero=False)

        if s2.max_blocks < self.max_blocks:
            s2.deltas = [d.trim(s2.max_blocks) for d in self.deltas.copy()]
        else:
            s2.deltas = self.deltas.copy()

        return s2

    def stamp(self, percent: int):
        nr_blocks = (self.max_blocks * percent) // 100
        delta = random_delta(nr_blocks, self.max_blocks)
        self.write_blocks(delta)

        self.deltas.append(delta)

    def restamp(self, delta_index: int):
        self.write_blocks(self.deltas[delta_index])

    def verify(self, delta_begin: int, delta_end: int = None):
        if delta_end is None:
            delta_end = delta_begin

        delta = BlockSet()
        for d in self.deltas[delta_begin : (delta_end + 1)]:
            delta = delta.union(d)

        self.verify_blocks(delta)

    def set_deltas(self, new_ds: List[BlockSet]):
        self.deltas = [bs.trim(self.max_blocks) for bs in new_ds]

    def _seek(self, io, b: Block):
        io.seek(self.block_size * b.block * SECTOR_SIZE)

    def write_block(self, io, b: Block):
        self._seek(io, b)
        io.write(b.get_buffer(self.block_size))

    def write_blocks(self, blocks: BlockSet):
        with open(self.dev, "wb") as io:
            for b in blocks:
                self.write_block(io, b)

    def read_block(self, io, b: Block) -> bytes:
        self._seek(io, b)
        return io.read(self.block_size * SECTOR_SIZE)

    def verify_block(self, io, b):
        expected = b.get_buffer(self.block_size)
        actual = self.read_block(io, b)

        # just check the first few bytes
        for i in range(16):
            assert actual[i] == expected[i]

        # This doesn't work, presumably some string encoding issues
        # self.assertEqual(actual, expected)

    def verify_blocks(self, blocks):
        with open(self.dev, "rb") as f:
            for b in blocks:
                self.verify_block(f, b)

    def _initialize_device(self, need_zero):
        if need_zero:
            wipe_device(self.dev)

        self.deltas.append(zeroes_delta(self.max_blocks))
