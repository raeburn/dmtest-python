import json
import logging as log
import os
import random
import string
import uuid
from contextlib import contextmanager
from enum import Enum

import dmtest.device_mapper.dev as dmdev
import dmtest.pool_stack as ps
import dmtest.process as process
import dmtest.tvm as tvm
import dmtest.units as units
from dmtest.fs import Xfs
from dmtest.thin.utils import standard_pool

# Environment variable to override the blk-archive/blk-stash binary name
BLK_ARCHIVE_BIN = os.getenv("BLK_ARCHIVE_BIN", "blk-stash")


class BlkArchive:
    def __init__(self, directory, block_size=4096):
        self.dir = os.path.abspath(directory)
        process.run(f"{BLK_ARCHIVE_BIN} create -a {self.dir} --block-size {block_size}")

    def pack(self, device_or_file):
        stdout = process.run(f"{BLK_ARCHIVE_BIN} pack -j -a {self.dir} {device_or_file}")[1]
        result = json.loads(stdout)
        return result["stream_id"]

    def unpack(self, stream, dest):
        process.run(f"{BLK_ARCHIVE_BIN} unpack -j -a {self.dir} -s {stream} {dest}")

    def dump_stream(self, stream):
        return process.run(f"{BLK_ARCHIVE_BIN} dump-stream -j -a {self.dir} -s {stream}")

    def pack_delta(self, old, old_id, new):
        stdout = process.run(f"{BLK_ARCHIVE_BIN} pack -j -a {self.dir} {new} --delta-stream {old_id} --delta-device {old}")[1]
        result = json.loads(stdout)
        return result["stream_id"]

    def verify(self, dev_or_file, stream=None):

        if stream is None:
            stream = self.get_stream_id(dev_or_file)
        process.run(
            f"{BLK_ARCHIVE_BIN} verify -a {self.dir} --stream {stream} {dev_or_file}"
        )

    def get_stream_id(self, dev_or_file):
        if isinstance(dev_or_file, str):
            name = os.path.basename(dev_or_file)
        else:
            name = os.path.basename(dev_or_file.path)
        (code, stdout, stderr) = process.run(f"{BLK_ARCHIVE_BIN} list -j -a {self.dir}")
        result = json.loads(stdout)

        item = next((i for i in result if i["source"] == name), None)
        if item is None:
            raise ValueError(f"couldn't find stream for {name}")
        return item["stream_id"]


@contextmanager
def loopback_context(test_dir):
    lb = LoopBackDevices(test_dir)
    try:
        yield lb
    finally:
        lb.destroy_all()


class LoopBackDevices(object):

    def __init__(self, test_dir):
        self.test_dir = test_dir
        self.count = 0
        self.devices = {}

    def create_device(self, size_mib):
        """
        Create a new loop back device.
        :param size_mib:
        :return: opaque handle
        """
        backing_file = os.path.join(self.test_dir, f"block_device_{self.count}")
        self.count += 1

        with open(backing_file, 'ab') as bd:
            bd.truncate(size_mib * (1024 * 1024))

        device = process.run(f"losetup -f --show {backing_file}")[1]
        token = uuid.uuid4()
        self.devices[token] = (device, backing_file)
        return token

    def device_node(self, token):
        if token in self.devices:
            return self.devices[token][0]

    def destroy_all(self):
        # detach the devices and delete the file(s) and directory!
        for (device, backing_file) in self.devices.values():
            process.run(f"losetup -d {device}")
            os.remove(backing_file)

        self.devices = {}
        self.count = 0
        self.test_dir = None


POOL_SIZE_MB = 8000
BASIC_BLOCK_SIZE_MB = 400


def inc_repeating_str(str_len):
    d = f"{'0' * 16}{'1' * 16}{'2' * 16}{'3' * 16}{'4' * 16}" \
        f"{'5' * 16}{'6' * 16}{'7' * 16}{'8' * 16}{'9' * 16}" \
        f"{'A' * 16}{'B' * 16}{'C' * 16}{'D' * 16}{'E' * 16}{'F' * 16}"
    repeated_str = ''.join([d for _ in range(str_len // len(d))])
    remaining_len = str_len % len(d)
    partial = d[:remaining_len]
    return f"{repeated_str}{partial}"


def rs(str_len):
    return ''.join(random.choice(string.ascii_letters) for _ in range(str_len))


BLOCK_SIZE = 512
MAX_FILE_SIZE = 1024*1024*8
DUPLICATE_DATA = inc_repeating_str(MAX_FILE_SIZE)


def _round_to_block_size(size):
    return size if size % BLOCK_SIZE == 0 else size + BLOCK_SIZE - size % BLOCK_SIZE


@contextmanager
def unit_test_data_context(test_dir, fixture):
    utd = UnitTestData(test_dir, fixture)
    try:
        yield utd
    finally:
        utd.teardown()


class UnitTestData:

    def __init__(self, test_dir, fixture):
        self.mounted = []
        self.test_dir = test_dir
        self.lb = LoopBackDevices(test_dir)
        self.fix = fixture

        # Keep track of things
        self.dm_tables_to_remove = []
        self.data = []
        self._setup()
        self.thin_id = 0

    def _destroy(self):
        # Data objects we have handed out get cleaned up first
        for d in self.data:
            d.destroy()

        # Then run clean-up commands in reverse
        self.dm_tables_to_remove.reverse()
        for i in self.dm_tables_to_remove:
            i.remove()

        self.dm_tables_to_remove = []

        # Remove all loop back devices
        self.lb.destroy_all()

    def _make_dm_pool(self):
        self.pool = standard_pool(self.fix)
        self.dm_tables_to_remove.append(self.pool)

    def make_dm_thin(self):
        thin = ps.new_thin(self.pool, units.meg(BASIC_BLOCK_SIZE_MB), self.thin_id)
        self.thin_id += 1
        self.dm_tables_to_remove.append(thin)
        return f"{thin}"

    def make_dm_thick(self):
        # Make a dm linear, return device node path
        # We'll auto provision a loop back device for this
        block_token = self.lb.create_device(BASIC_BLOCK_SIZE_MB)
        vm = tvm.VM()
        vm.add_allocation_volume(self.lb.device_node(block_token))
        vm.add_volume(tvm.LinearVolume("basic_linear", units.meg(BASIC_BLOCK_SIZE_MB)))

        linear = dmdev.dev(vm.table("basic_linear"))
        self.dm_tables_to_remove.append(linear)
        return f"{linear}"

    def _setup(self):
        self._make_dm_pool()

    def teardown(self):
        self._destroy()

    def create(self, d_types, count=1):
        rc = []
        for _ in range(count):
            for d in d_types:
                d = Data(d, self)
                self.data.append(d)
                rc.append(d)
        return rc


class Data:

    class Type(Enum):
        BASIC = 1
        DM_THICK = 2
        DM_THIN = 3
        FILE = 4
        UNKNOWN = 5

    def __str__(self):
        return f"{self.t} mount path={self.mount_path}, fs created={self.fs_created}, device node={self.device_node}"

    def __init__(self, data_type, pd):
        self.t = data_type
        self.filled = False
        self.mount_path = None
        self.device_node = None
        self.pd = pd
        self.fs_created = False
        if data_type == Data.Type.FILE:
            self.mount_path = os.path.join(pd.test_dir, f"file_{rs(5)}")
        else:
            if data_type == Data.Type.BASIC:
                token = self.pd.lb.create_device(BASIC_BLOCK_SIZE_MB)
                self.device_node = self.pd.lb.device_node(token)
            elif data_type == Data.Type.DM_THIN:
                self.device_node = self.pd.make_dm_thin()
            elif data_type == Data.Type.DM_THICK:
                self.device_node = self.pd.make_dm_thick()

    def mount(self):
        if self.t == Data.Type.FILE or self.mount_path is not None:
            return self

        self.mount_path = os.path.join(os.sep, "mnt", str(uuid.uuid4()))
        fs = Xfs(self.device_node)
        fs.mount(self.mount_path)
        return self

    def unmount(self):
        if self.t == Data.Type.FILE or self.mount_path is None:
            return self

        fs = Xfs(self.device_node, self.mount_path)
        fs.umount()
        self.mount_path = None
        return self

    def create_fs(self):
        if self.t == Data.Type.FILE or self.fs_created:
            return self

        fs = Xfs(self.device_node)
        fs.format()
        self.fs_created = True
        return self

    def destroy(self):
        self.unmount()
        if self.t == Data.Type.FILE:
            if os.path.isfile(self.mount_path):
                os.remove(self.mount_path)

        self.pd = None
        self.mount_path = None
        self.device_node = None
        self.t = Data.Type.UNKNOWN

    def compare(self, rvalue):
        l_f = self.mount_path if self.mount_path is not None else self.device_node
        r_f = rvalue.mount_path if rvalue.mount_path is not None else rvalue.device_node

        # python filecmp.cmp not work for block devices..., cmp can check 400MB in 0.3-0.4 seconds
        return process.run(f"cmp {l_f} {r_f}", False)[0] == 0

    @staticmethod
    def _fill_file(file):
        size = _round_to_block_size(random.randint(BLOCK_SIZE, MAX_FILE_SIZE))
        with open(file, 'w') as out:
            out.write(DUPLICATE_DATA[0:size])
            out.flush()
            os.fsync(out.fileno())

    def fill(self):
        log.info(f"filling {self}")
        if not self.filled:
            if self.mount_path is not None:
                if self.t == Data.Type.FILE:
                    Data._fill_file(self.mount_path)
                else:
                    # TODO: Create 1 file on the mount point for now, will expand later.
                    fn = os.path.join(os.sep, self.mount_path, rs(10))
                    Data._fill_file(fn)
            else:
                # TODO: Write directly to block device or consider this an error?
                pass

            self.filled = True
        return self

    def fs_path(self):
        return self.mount_path

    def dev_node(self):
        return self.device_node

    def src_arg(self):
        if self.t == Data.Type.FILE:
            return f"{self.mount_path}"
        return f"{self.device_node}"

    def dest_arg(self):
        if self.t == Data.Type.FILE:
            return f"--create {self.mount_path}"
        return f"{self.device_node}"
