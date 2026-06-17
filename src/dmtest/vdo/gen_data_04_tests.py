"""
VDO GenData04 tests - Generate and verify data in parallel streams
"""
import logging as log
from concurrent.futures import ThreadPoolExecutor, as_completed

from dmtest.assertions import assert_equal
from dmtest.vdo.utils import standard_vdo, mounted_fs
from dmtest.vdo.dataset_helpers import write_file_dataset, verify_file_dataset
import dmtest.process as process
import dmtest.vdo.stats as stats


def _write_dataset_for_parallel(mount_point, tag, num_files, blocks_per_file, dedupe):
    """Wrapper for parallel execution that returns (tag, ranges) instead of (dataset_dir, ranges)."""
    dataset_dir, ranges = write_file_dataset(
        mount_point, tag, num_files,
        blocks_per_file=blocks_per_file,
        dedupe=dedupe,
        suppress_logging=True
    )
    return (tag, ranges)


def t_parallel_data(fix) -> None:
    """
    Generate and verify data in parallel streams on a filesystem.

    Writes four datasets concurrently with varying file counts
    (1, 32, 1024, 32768) and 25% deduplication, then verifies all data.
    """
    GB = 1024 * 1024 * 1024
    KB = 1024
    data_size = 1 * GB
    block_size = 4 * KB
    dedupe_rate = 0.25

    with standard_vdo(fix) as vdo:
        # Record initial statistics
        before_stats = stats.vdo_stats(vdo)

        with mounted_fs(vdo.path, format=True) as mount_point:
            all_datasets = []

            # Write datasets in parallel using ThreadPoolExecutor
            log.info("Starting parallel write of four datasets")
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = []
                for num_files in [1, 32, 1024, 32768]:
                    blocks_per_file = data_size // (num_files * block_size)
                    tag = f"N{num_files}"

                    future = executor.submit(
                        _write_dataset_for_parallel,
                        mount_point, tag, num_files, blocks_per_file, dedupe_rate
                    )
                    futures.append(future)

                # Wait for all writes to complete
                for future in as_completed(futures):
                    tag, ranges = future.result()
                    all_datasets.append((tag, ranges))

            log.info("All parallel writes completed")

            # Sync data to disk
            process.run("sync")

            # Verify all datasets
            for tag, ranges in all_datasets:
                verify_file_dataset(ranges, tag, suppress_logging=True)

        # Record final statistics
        after_stats = stats.vdo_stats(vdo)

        # Check that dedupe advice timeouts didn't increase
        # (skip for VMs and low memory tests as in the Perl version)
        before_timeouts = before_stats.get('dedupeAdviceTimeouts', 0)
        after_timeouts = after_stats.get('dedupeAdviceTimeouts', 0)

        log.info(f"Dedupe advice timeouts: before={before_timeouts}, after={after_timeouts}")
        assert_equal(before_timeouts, after_timeouts,
                    "Dedupe advice timeouts should not increase")


def register(tests):
    tests.register("/vdo/gen-data/parallel-data", t_parallel_data)
