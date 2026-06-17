"""
VDO GenData02 test - Parallel compression testing
"""
import logging as log
from concurrent.futures import ThreadPoolExecutor, as_completed

from dmtest.assertions import assert_equal
from dmtest.vdo.utils import standard_vdo, mounted_fs
from dmtest.vdo.dataset_helpers import write_file_dataset, verify_file_dataset
import dmtest.process as process
import dmtest.vdo.stats as stats


def _write_and_verify_dataset(mount_point, tag, num_files, blocks_per_file, dedupe, compress):
    """Write and verify a dataset with compression support.

    Args:
        mount_point: Filesystem mount point
        tag: Tag for the data stream
        num_files: Number of files to create
        blocks_per_file: Number of 4KB blocks per file
        dedupe: Deduplication rate (0.0 to 1.0)
        compress: Compression rate (0.0 to 0.96)

    Returns:
        Tuple of (tag, success)
    """
    try:
        dataset_dir, ranges = write_file_dataset(
            mount_point, tag, num_files,
            blocks_per_file=blocks_per_file,
            dedupe=dedupe,
            compress=compress
        )

        verify_file_dataset(ranges, tag)

        log.info(f"Completed dataset {tag}")
        return (tag, True)

    except Exception as e:
        log.error(f"Failed dataset {tag}: {e}")
        raise


def t_gen_data_02(fix) -> None:
    """
    Generate and verify compressible data in parallel streams at four
    compression levels (0%, 30%, 55%, 85%) on a filesystem. Tests VDO
    compression functionality with varying compression ratios while
    maintaining data integrity.
    """
    GB = 1024 * 1024 * 1024
    KB = 1024
    data_size = 1 * GB
    block_size = 4 * KB
    dedupe_rate = 0.25
    num_files = 1024

    blocks_per_file = data_size // (num_files * block_size)

    # Four compression levels to test
    compression_levels = [
        ("C0", 0.0),     # 0% compression (incompressible)
        ("C30", 0.30),   # 30% compression
        ("C55", 0.55),   # 55% compression
        ("C85", 0.85),   # 85% compression
    ]

    with standard_vdo(fix) as vdo:
        # Record initial statistics
        before_stats = stats.vdo_stats(vdo)

        with mounted_fs(vdo.path, format=True) as mount_point:
            # Execute all four datasets in parallel
            log.info(f"Starting parallel write and verify of {len(compression_levels)} datasets")

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = []
                for tag, compress_rate in compression_levels:
                    future = executor.submit(
                        _write_and_verify_dataset,
                        mount_point, tag, num_files, blocks_per_file,
                        dedupe_rate, compress_rate
                    )
                    futures.append(future)

                # Wait for all tasks to complete
                for future in as_completed(futures):
                    tag, success = future.result()
                    log.info(f"Dataset {tag} completed successfully")

            # Sync data to disk
            process.run("sync")
            log.info("All datasets written and verified successfully")

        # Record final statistics
        after_stats = stats.vdo_stats(vdo)

        # Check that dedupe advice timeouts didn't increase
        before_timeouts = before_stats.get('dedupeAdviceTimeouts', 0)
        after_timeouts = after_stats.get('dedupeAdviceTimeouts', 0)

        log.info(f"Dedupe advice timeouts: before={before_timeouts}, after={after_timeouts}")
        assert_equal(before_timeouts, after_timeouts,
                    "Dedupe advice timeouts should not increase")


def register(tests):
    tests.register("/vdo/gen-data/gen-data-02", t_gen_data_02)
