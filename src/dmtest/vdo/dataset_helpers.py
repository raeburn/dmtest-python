"""Shared helper functions for VDO filesystem dataset operations.

Provides utilities for writing and verifying file-based datasets with
controlled deduplication and compression characteristics.
"""

import logging as log
import os

from dmtest.gendatablocks import make_block_range


def write_file_dataset(mount_point, tag, num_files, blocks_per_file=None,
                       num_bytes=None, dedupe=0, compress=0,
                       suppress_logging=False):
    """Write a dataset of files with specified characteristics.

    Args:
        mount_point: Filesystem mount point
        tag: Tag for the data stream
        num_files: Number of files to create
        blocks_per_file: Number of 4KB blocks per file (mutually exclusive with num_bytes)
        num_bytes: Total bytes to write across all files (mutually exclusive with blocks_per_file)
        dedupe: Deduplication rate (0.0 to 1.0), default 0
        compress: Compression rate (0.0 to 0.96), default 0
        suppress_logging: If True, reduce logging verbosity during write

    Returns:
        Tuple of (dataset_dir, list of BlockRange objects)
    """
    if blocks_per_file is None and num_bytes is None:
        raise ValueError("Must specify either blocks_per_file or num_bytes")
    if blocks_per_file is not None and num_bytes is not None:
        raise ValueError("Cannot specify both blocks_per_file and num_bytes")

    dataset_dir = os.path.join(mount_point, f"dataset_{tag}")
    os.makedirs(dataset_dir, exist_ok=True)

    # Calculate blocks_per_file from num_bytes if needed
    if num_bytes is not None:
        blocks_per_file = int(num_bytes // (num_files * 4096))
        if blocks_per_file < 1:
            blocks_per_file = 1

    total_bytes = num_files * blocks_per_file * 4096
    log.info(f"Writing dataset {tag}: {num_files} files, {blocks_per_file} blocks each, "
             f"{total_bytes} bytes total, dedupe={dedupe}, compress={compress}")

    # Temporarily reduce logging level if requested
    old_level = None
    if suppress_logging:
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
            block_range.write(tag, dedupe=dedupe, compress=compress, fsync=False)
            ranges.append(block_range)

        return (dataset_dir, ranges)
    finally:
        if old_level is not None:
            log.getLogger().setLevel(old_level)
        log.info(f"Completed writing dataset {tag}: {num_files} files")


def verify_file_dataset(ranges, tag, suppress_logging=False):
    """Verify a dataset of files.

    Args:
        ranges: List of BlockRange objects to verify
        tag: Tag identifying the dataset
        suppress_logging: If True, reduce logging verbosity during verification
    """
    log.info(f"Verifying dataset {tag}: {len(ranges)} files")

    # Temporarily reduce logging level if requested
    old_level = None
    if suppress_logging:
        old_level = log.getLogger().level
        log.getLogger().setLevel(log.WARNING)

    try:
        for block_range in ranges:
            block_range.verify()
    finally:
        if old_level is not None:
            log.getLogger().setLevel(old_level)
        log.info(f"Completed verifying dataset {tag}: {len(ranges)} files")
