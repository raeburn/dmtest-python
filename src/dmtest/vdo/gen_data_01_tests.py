"""
VDO GenData tests - Generate and verify data on filesystems
"""
import logging as log
import tempfile

from dmtest.assertions import assert_equal
from dmtest.fs import Ext4
from dmtest.vdo.utils import standard_vdo
from dmtest.vdo.dataset_helpers import write_file_dataset, verify_file_dataset
import dmtest.process as process
import dmtest.vdo.stats as stats


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

                    dataset_dir, ranges = write_file_dataset(
                        mount_point, tag, num_files,
                        blocks_per_file=blocks_per_file,
                        dedupe=dedupe_rate,
                        suppress_logging=True
                    )
                    all_datasets.append((tag, ranges))

                # Sync data to disk
                process.run("sync")

                # Verify all datasets
                for tag, ranges in all_datasets:
                    verify_file_dataset(ranges, tag, suppress_logging=True)

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
