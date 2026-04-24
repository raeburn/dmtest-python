"""
VDO GenData tests - Generate and verify data on filesystems
"""
import logging as log
import os
import tempfile

from dmtest.assertions import assert_equal
from dmtest.fs import Ext4
from dmtest.gendatablocks import make_block_range
from dmtest.vdo.utils import standard_vdo
import dmtest.process as process
import dmtest.vdo.stats as stats


def _write_file_dataset(mount_point, tag, num_files, blocks_per_file, dedupe):
    """
    Write a dataset of files with specified deduplication.

    Args:
        mount_point: Filesystem mount point
        tag: Tag for the data stream
        num_files: Number of files to create
        blocks_per_file: Number of 4KB blocks per file
        dedupe: Deduplication rate (0.0 to 1.0)

    Returns:
        List of BlockRange objects for verification
    """
    dataset_dir = os.path.join(mount_point, f"dataset_{tag}")
    os.makedirs(dataset_dir, exist_ok=True)

    total_bytes = num_files * blocks_per_file * 4096
    log.info(f"Writing dataset {tag}: {num_files} files, {blocks_per_file} blocks each, "
             f"{total_bytes} bytes total, dedupe={dedupe}")

    # Temporarily reduce logging level to avoid spamming logs
    old_level = log.getLogger().level
    log.getLogger().setLevel(log.WARNING)

    try:
        ranges = []
        for i in range(num_files):
            file_path = os.path.join(dataset_dir, f"file_{i:08d}")

            # Create the file
            with open(file_path, 'w') as f:
                pass

            # Write data to the file
            block_range = make_block_range(file_path, blocks_per_file)
            block_range.write(tag, dedupe=dedupe, fsync=False)
            ranges.append(block_range)

        return ranges
    finally:
        log.getLogger().setLevel(old_level)
        log.info(f"Completed writing dataset {tag}: {num_files} files")


def _verify_file_dataset(ranges, tag):
    """
    Verify a dataset of files.

    Args:
        ranges: List of BlockRange objects to verify
        tag: Tag identifying the dataset
    """
    log.info(f"Verifying dataset {tag}: {len(ranges)} files")

    # Temporarily reduce logging level to avoid spamming logs
    old_level = log.getLogger().level
    log.getLogger().setLevel(log.WARNING)

    try:
        for block_range in ranges:
            block_range.verify()
    finally:
        log.getLogger().setLevel(old_level)
        log.info(f"Completed verifying dataset {tag}: {len(ranges)} files")


def t_gen_data_01(fix) -> None:
    """
    Generate and verify data serially on a filesystem with four datasets of
    varying file counts (1, 32, 1024, 32768) and 25% deduplication.
    """
    MB = 1024 * 1024
    KB = 1024
    data_size = 800 * MB
    block_size = 4 * KB
    dedupe_rate = 0.25

    with standard_vdo(fix) as vdo:
        # Record initial statistics
        before_stats = stats.vdo_stats(vdo)

        fs = Ext4(vdo.path)
        fs.format()

        with tempfile.TemporaryDirectory() as mount_point:
            fs.mount(mount_point)

            try:
                all_datasets = []

                # Four datasets with varying file counts
                for num_files in [1, 32, 1024, 32768]:
                    blocks_per_file = data_size // (num_files * block_size)
                    tag = f"D{num_files}"

                    ranges = _write_file_dataset(
                        mount_point, tag, num_files, blocks_per_file, dedupe_rate
                    )
                    all_datasets.append((tag, ranges))

                # Sync data to disk
                process.run("sync")

                # Verify all datasets
                for tag, ranges in all_datasets:
                    _verify_file_dataset(ranges, tag)

            finally:
                fs.umount()

        # Record final statistics
        after_stats = stats.vdo_stats(vdo)

        # Check that dedupe advice timeouts didn't increase
        # (skip this check for VMs as mentioned in the Perl test)
        before_timeouts = before_stats.get('dedupeAdviceTimeouts', 0)
        after_timeouts = after_stats.get('dedupeAdviceTimeouts', 0)

        log.info(f"Dedupe advice timeouts: before={before_timeouts}, after={after_timeouts}")
        assert_equal(before_timeouts, after_timeouts,
                    "Dedupe advice timeouts should not increase")


def register(tests):
    tests.register("/vdo/gen-data/gen-data-01", t_gen_data_01)
