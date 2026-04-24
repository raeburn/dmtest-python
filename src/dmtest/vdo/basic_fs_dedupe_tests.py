"""
VDO BasicFSDedupe test - Filesystem-level deduplication verification
"""
import logging as log
import os
import shutil
import tempfile

from dmtest.assertions import assert_near
from dmtest.fs import Ext4
from dmtest.gendatablocks import make_block_range
from dmtest.vdo.stats import vdo_stats, make_delta_stats
from dmtest.vdo.utils import standard_vdo, fsync


def t_basic_fs_dedupe(fix) -> None:
    """
    Basic filesystem-level deduplication test that writes a dataset twice
    and verifies deduplication achieves expected space savings.
    """
    MB = 1024 * 1024
    num_files = 32
    file_size_mb = 8
    blocks_per_file = file_size_mb * MB // 4096  # 8MB / 4KB = 2048 blocks

    with standard_vdo(fix) as vdo:
        fs = Ext4(vdo.path)
        fs.format()

        with tempfile.TemporaryDirectory() as mount_point:
            fs.mount(mount_point)

            try:
                # Create subdirectories on VDO filesystem
                original_dir = os.path.join(mount_point, "original")
                copy1_dir = os.path.join(mount_point, "copy1")
                os.makedirs(original_dir)
                os.makedirs(copy1_dir)

                # Record initial stats after filesystem setup
                fsync(vdo.path)
                initial_stats = vdo_stats(vdo)

                # Generate dataset in a scratch directory
                with tempfile.TemporaryDirectory() as scratch_dir:
                    dataset_dir = os.path.join(scratch_dir, "dataset")
                    os.makedirs(dataset_dir)

                    log.info(f"Generating dataset: {num_files} files × {file_size_mb}MB each = 256MB total")
                    for i in range(num_files):
                        file_path = os.path.join(dataset_dir, f"file_{i:08d}")
                        # Create the file first
                        with open(file_path, 'w') as f:
                            pass
                        # Write data to the file
                        block_range = make_block_range(file_path, blocks_per_file)
                        block_range.write(f"BFD{i:04d}", dedupe=0.0, fsync=False)

                    # Copy dataset to "original" directory
                    log.info("Copying dataset to 'original' directory")
                    shutil.copytree(dataset_dir, os.path.join(original_dir, "data"))

                    # Sync and check stats after first write
                    fsync(vdo.path)
                    stats_after_first = vdo_stats(vdo)
                    delta_first = make_delta_stats(stats_after_first, initial_stats)

                    data_blocks = delta_first['dataBlocksUsed']
                    logical_blocks = delta_first['logicalBlocksUsed']
                    ratio_first = data_blocks / logical_blocks if logical_blocks > 0 else 0

                    log.info(f"After first write: data={data_blocks}, logical={logical_blocks}, ratio={ratio_first:.3f}")
                    # Verify minimal deduplication on first write (filesystem metadata may cause some variance)
                    assert_near(ratio_first, 1.0, 0.1, "Data-to-logical ratio after first write")

                    # Copy the same dataset to "copy1" directory (duplicate copy)
                    log.info("Copying dataset to 'copy1' directory (duplicate)")
                    shutil.copytree(dataset_dir, os.path.join(copy1_dir, "data"))

                    # Sync and check stats after second write
                    fsync(vdo.path)
                    stats_after_second = vdo_stats(vdo)
                    delta_second = make_delta_stats(stats_after_second, initial_stats)

                    data_blocks_2 = delta_second['dataBlocksUsed']
                    logical_blocks_2 = delta_second['logicalBlocksUsed']
                    ratio_second = data_blocks_2 / logical_blocks_2 if logical_blocks_2 > 0 else 0

                    log.info(f"After second write: data={data_blocks_2}, logical={logical_blocks_2}, ratio={ratio_second:.3f}")
                    # Verify significant deduplication on second write (~50% ratio expected)
                    assert_near(ratio_second, 0.5, 0.05, "Data-to-logical ratio after second write (with dedupe)")

            finally:
                fs.umount()


def register(tests):
    tests.register("/vdo/basic/fs-dedupe", t_basic_fs_dedupe)
