""" Write test data to a device or file"""
import dmtest.process as process

import logging
import mmap
import os
import struct

from pathlib import Path
from typing import List

# According to pylint numpy should be last
import numpy

# Each data stream is identified by an 8 character tag
MAX_TAG_SIZE = 8

def shrink_for_dedupe(number: int, dedupe: float) -> int:
    """Calculate the block number for the Header.

    In general, if we end up with the same tag, stream number, and block number we will
    have the same Header and thus the same 'random' data. This is how we generate
    deduplicated data.

    To accomplish this, the algorithm below maintains the appropriate fraction of unique
    block numbers. The rest are halved, perhaps multiple times, to map unevenly onto
    lower (previously used) block numbers

    Parameters
    ----------
    number : int
        The original block number
    dedupe : float
        The dedupe rate

    Returns
    -------
    int
        The calculated block number

    """
    while number > 0 and (number * dedupe) % 1 < dedupe:
        number = number >> 1
    return number

class CompareError(Exception):
    """ Exception raised for full data compare errors """
    def __init__(self,
                 block_number: int,
                 actual: bytes,
                 expected: bytes,
                 byte_number: int):
        self.block_number = block_number
        self.actual = actual
        self.expected = expected
        self.byte_number = byte_number

        def __str__(self) -> str:
            """ __str__ is to print() the error """
            return ("verification of block " + repr(self.block_number)
                    + " failed. actual: " + repr(self.actual)
                    + " expected: " + repr(self.expected) + " at byte "
                    + repr(self.byte_number))

class ClaimError(Exception):
    """ Exception raised when no streams claim a block """
    def __init__(self,
                 block_number: int,
                 actual: bytes):
        self.block_number = block_number
        self.actual = actual

        def __str__(self) -> str:
            """ __str__ is to print() the error """
            return ("block " + repr(self.block_number)
                    + " not claimed. actual: " + repr(self.actual))

HEADER_FORMAT = "!8sIL"

class Header:
    """Header of what is written to and read from disk

    The header is used to describe the block of data written to disk.

    """
    def __init__(self, tag: str, stream_number: int, block_number: int):
        self.tag = tag
        self.stream_number = stream_number
        self.block_number = block_number

    def get_seed(self) -> int:
        """generate a seed based off the Header

        Returns
        -------
        int
            the seed calculated from the Header

        """
        return int.from_bytes(self.to_bytes(), byteorder='big')

    def to_bytes(self) -> bytes:
        """get a bytes array representation of the Header

        Returns
        -------
        bytes
            the bytes array representation

        """
        return struct.pack(HEADER_FORMAT, self.tag.encode('ascii'),
                           self.stream_number, self.block_number)

    @classmethod
    def len_as_bytes(cls) -> int:
        """get the length of the bytes representation of the Header

        Returns
        -------
        int
            the length of the bytes representation

        """
        return struct.calcsize(HEADER_FORMAT)

    @classmethod
    def from_bytes(cls, buffer):
        """Generate a Header from a bytes array

        Parameters
        ----------
        buffer : bytes
            bytes read from disk

        Returns
        -------
        Header
            Header representing bytes read from disk

        """
        tag, stream_number, block_number = struct.unpack(HEADER_FORMAT, buffer)
        return Header(tag.decode('ascii').rstrip('\0'), stream_number, block_number)

class BlockBuffer:
    """A variable length buffer of data that is written to and read from disk

    It contains a fixed length header and a variable length data portion.

    """
    def __init__(self, header: 'Header', data: bytes = bytes()):
        self.header = header
        self.data = data

    def fill_data(self, compress_size: int, block_size: int):
        """Fill the variable length part of the buffer

        This function fills the variable length part of the buffer with compressible and
        random data. The first part is all 1s and is based on how much compression is
        requested. The remaining data is random data generated using a seed based off of
        the Header. The code will use similar Headers as a way of generating requested
        dedupe rates.

        Parameters
        ----------
        compress_size : int
            the requested compression rate
        block_size : int
            the block size to write

        """
        rand_size = block_size - compress_size
        header_len = Header.len_as_bytes()
        if compress_size > 0:
            compress_size = compress_size - header_len
        else:
            rand_size = rand_size - header_len

        seed = self.header.get_seed()
        rng = numpy.random.default_rng(seed)
        self.data = bytes([255] * compress_size) + rng.bytes(rand_size)

    def to_bytes(self) -> bytes:
        """get a bytes array representation of the BlockBuffer

        Returns
        -------
        bytes
            the bytes array representation

        """
        return self.header.to_bytes() + self.data

class DataStream:
    """A generic data stream that operates on a BlockRange"""
    def __init__(self):
        self.counter = 0

    def claim(self, buffer: bytes) -> bool:
        raise NotImplementedError("method claim must be implemented")

    def generate(self, block_number: int, block_size: int) -> bytes:
        raise NotImplementedError("method generate must be implemented")

    def report(self) -> str:
        raise NotImplementedError("method report must be implemented")

class BlockStream(DataStream):
    """A data stream that may contain deduplicate and/or compressible data"""
    def __init__(self,
                 tag: str,
                 dedupe: float = 0.0,
                 compress: float = 0.0):
        self.tag = tag
        self.dedupe = dedupe
        self.compress = compress
        self.number = 0
        super().__init__()

    def claim(self, buffer):
        """Claim initial ownership of the data buffer

        Parameters
        ----------
        buffer : bytes
            a bytes representation of a BlockBuffer

        Returns
        -------
        bool
            True if the buffer has the same tag as this stream
        """
        try:
            header = Header.from_bytes(buffer[:Header.len_as_bytes()])
        except UnicodeDecodeError:
            # If decoding failed, e.g., the content isn't ASCII, then
            # we don't claim it.
            return False
        return header.tag == self.tag

    def generate(self, block_number, block_size):
        """Generate a buffer for the BlockStream at a given location

        Parameters
        ----------
        block_number : int
            location in the BlockStream to generate the buffer
        block_size : int
            size of the buffer to generate

        Returns
        -------
        bytes
            the bytes array
        """
        number = shrink_for_dedupe(block_number, self.dedupe)
        header = Header(self.tag, self.number, number)
        block = BlockBuffer(header)
        # Fill the block with ones for compress and rest with random data
        compress = int(self.compress * block_size)
        block.fill_data(compress, block_size)
        return block.to_bytes()

    def report(self):
        """Return information about the last write or verify"""
        return (str(self.tag) + ":" + str(self.counter))

class ZeroStream(DataStream):
    """A data stream representing a new or trimmed device or file"""
    def claim(self, buffer):
        """Claim initial ownership of the data buffer

        Parameters
        ----------
        buffer : bytes
            a bytes representation of a BlockBuffer

        Returns
        -------
        bool
            True if the ZeroStream owns this buffer
        """
        return (buffer[0] == 0) and (buffer[1] == 0)

    def generate(self, block_number, block_size):
        """Generate a buffer for the ZeroStream at a given location

        Parameters
        ----------
        block_number : int
            location in the ZeroStream to generate the buffer
        block_size : int
            size of the buffer to generate

        Returns
        -------
        bytes
            the bytes array
        """
        return b"\0" * block_size

    def report(self):
        """Return information about the last write or verify"""
        return "ZERO: " + str(self.counter)

class BlockRange():
    """A range of blocks in a file or device

    Raises
    ------
    OsError
    ValueError
    CompareError
    FileNotFoundError

    """
    def __init__(self,
                 path: str,
                 block_count: int = 1,
                 block_size: int = 4096,
                 offset: int = 0):
        self.path = self.validate_path(path)
        self.block_count = block_count
        self.block_size = block_size
        self.offset = offset
        self.create = False
        self.streams: List[DataStream] = []

    def update_path(self, new_path: str):
        self.path = self.validate_path(new_path)

    def report(self):
        """Report on all streams associated with this block range"""
        list(map(lambda x: x.report(self), self.streams))

    def _seek(self, fd):
        """Seek to the start of the block range

        Parameters
        ----------
        fd : the file descriptor associated with the block range
        """
        fd.seek(self.block_size * self.offset)

    def trim(self, fsync=False):
        """Trim the block range, if supported."""
        byte_offset = self.block_size * self.offset
        byte_size = self.block_size * self.block_count
        process.run(f"blkdiscard --force -o {byte_offset} -l {byte_size} {self.path}")
        if fsync:
            with open(self.path, "w+") as f:
                os.fsync(f.fileno())
        stream = ZeroStream()
        self.streams.clear()
        self.streams.append(stream)

    def validate_path(self, value: str):
        """Validate the file or device parameter

        Parameters
        ----------
        value : str
            the file or path parameter

        Raises
        ------
        FileNotFoundError

        """
        path = Path(value)
        if (path.is_file() or path.is_block_device()):
            return path
        raise FileNotFoundError(value)

    def verify(self):
        """Verify the data previously written to a block range.

        Raises
        ------
        ValueError

        """
        if self.path is None:
            raise ValueError("the file/device path is invalid")

        logging.info(f"verifying {self.block_count*self.block_size} bytes in {self.path} at {self.block_size*self.offset}")
        flags = os.O_RDONLY
        with os.fdopen(os.open(self.path, flags), "rb") as fd:
            self._seek(fd)
            for n in range(0, self.block_count):
                data = fd.read(self.block_size)
                self.verify_streams(n, data)

    def verify_streams(self, block_number: int, actual: bytes):
        """Verify all streams related to a specific block in a block range.

        Parameters
        ----------
        block_number : int
            block number to verify all streams against
        actual : bytes
            the bytes to compare against

        Raises
        ------
        CompareError

        """
        if self.streams == []:
            logging.warning("no streams available to claim data")
        for stream in self.streams:
            if stream.claim(actual):
                expected = stream.generate(block_number, self.block_size)
                if expected == actual:
                    stream.counter += 1
                    return
                for i in range(0, self.block_size):
                    if actual[i] != expected[i]:
                        raise CompareError(block_number, actual, expected, i)
        raise ClaimError(block_number, actual)

    def write(self,
              tag: str,
              dedupe: float = 0.0,
              compress: float = 0.0,
              direct: bool = False,
              sync: bool = False,
              fsync: bool = False):
        """Write to a block range

        Parameters
        ----------
        tag : str
            tag the data for future reference
        dedupe : float
            how much deduplication to write
        compress : float
            how much compressible data to write
        direct : bool
            open the device with O_DIRECT (not yet implemented)
        sync : bool
            open the device with O_SYNC
        fsync : bool
            write the device with fsync

        Raises
        ------
        ValueError
        NotImplementedError

        """
        if tag is None:
            raise ValueError("tag is not defined")
        if len(tag) >= MAX_TAG_SIZE:
            raise ValueError("tag must be 8 characters or less")
        if (dedupe < 0) or (dedupe > 1.00):
            raise ValueError("the dedupe fraction " + str(dedupe)
                             + " is invalid")
        # This attempts to handle the space used by the header in all blocks
        if (compress < 0.0) or (compress > 0.96):
            raise ValueError("the compression fraction " + str(compress)
                             + " is invalid")
        stream = BlockStream(tag, dedupe, compress)

        flags = os.O_WRONLY
        if direct:
            flags |= os.O_DIRECT
        if sync:
            flags |= os.O_SYNC
        if self.create:
            flags |= os.O_CREAT

        logging.info(f"writing {self.block_count*self.block_size} bytes tagged \"{tag}\""
                     f" to {self.path} at {self.block_size*self.offset} open flags {flags}")
        self.streams.append(stream)
        if direct:
            fd = os.open(self.path, flags)
            try:
                os.lseek(fd, self.block_size * self.offset, os.SEEK_SET)
                buf = mmap.mmap(-1, self.block_size)
                try:
                    for n in range(0, self.block_count):
                        data = stream.generate(n, self.block_size)
                        buf[:] = data
                        os.write(fd, buf)
                        stream.counter += 1
                finally:
                    buf.close()
                if fsync:
                    os.fsync(fd)
            finally:
                os.close(fd)
        else:
            with os.fdopen(os.open(self.path, flags), "r+b") as fd:
                self._seek(fd)
                for n in range(0, self.block_count):
                    data = stream.generate(n, self.block_size)
                    fd.write(data)
                    stream.counter += 1
                fd.flush()
                if fsync:
                    os.fsync(fd)

def make_block_range(path: str,
                     block_count: int = 1,
                     block_size: int = 4096,
                     offset: int = 0) -> BlockRange:
    """Used to write data for testing

    Creates a range of blocks within a device or file. The range can produce both
    dedupe and compressed data. It can provide the following
    operations:

    Parameters
    ----------
    path : str
        path to a file or device
    block_count : int
        number of blocks in the block range
    block_size : int
        size of each block in the range (in bytes)
    offset : int
        offset in the device or file the block range begins at

    Returns
    -------
    BlockRange
         the block range

    """
    return BlockRange(path, block_count, block_size, offset)
