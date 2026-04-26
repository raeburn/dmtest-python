"""VDO basic functional test.

Verifies VDO persistence by writing data to a filesystem on VDO, stopping
the VDO device, restarting it, and verifying the data is still readable.
"""
from dmtest.assertions import assert_equal, assert_string_in
from dmtest.fs import Ext4
from dmtest.utils import get_dmesg_log
from dmtest.vdo.utils import standard_vdo, standard_stack
import dmtest.process as process
import tempfile
import time

def t_basic(fix):
    """Basic VDO functional test: write files, stop/start VDO, verify data persists."""
    # Create VDO with slab_bits=17 (SLAB_BITS_SMALL)
    with standard_vdo(fix, slab_bits=17) as vdo:
        fs = Ext4(vdo.path)
        fs.format()

        with tempfile.TemporaryDirectory() as mount_point:
            fs.mount(mount_point)

            try:
                # Create file foo1 with "Hello World"
                file1 = f"{mount_point}/foo1"
                process.run(f"bash -c 'echo Hello World > {file1}'")

                # Create subdirectory dir2
                dir2 = f"{mount_point}/dir2"
                process.run(f"mkdir {dir2}")

                # Copy foo1 to dir2/foo2
                file2 = f"{dir2}/foo2"
                process.run(f"cp {file1} {file2}")

                # Copy foo1 to foo3
                file3 = f"{mount_point}/foo3"
                process.run(f"cp {file1} {file3}")

                # Drop caches
                process.run("echo 1 > /proc/sys/vm/drop_caches")

                # Verify content of foo1 and foo2
                result1 = process.run(f"cat {file1}")
                assert_equal(result1[1].strip(), "Hello World")

                result2 = process.run(f"cat {file2}")
                assert_equal(result2[1].strip(), "Hello World")

            finally:
                # Unmount filesystem before stopping VDO
                fs.umount()

    # VDO device is now stopped (exited context manager)
    # Get kernel log timestamp before restarting
    start_time = time.time()

    # Restart VDO device without reformatting
    with standard_vdo(fix, format=False, slab_bits=17) as vdo:
        fs = Ext4(vdo.path)

        with tempfile.TemporaryDirectory() as mount_point:
            fs.mount(mount_point)

            try:
                # Verify content of foo3
                file3 = f"{mount_point}/foo3"
                result3 = process.run(f"cat {file3}")
                assert_equal(result3[1].strip(), "Hello World")

            finally:
                fs.umount()

    # Check kernel log for VDO startup message
    log_message = get_dmesg_log(start_time)
    assert_string_in(log_message, "VDO commencing normal operation")

def register(tests):
    tests.register("/vdo/basic/basic01", t_basic)
