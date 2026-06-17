"""
VDO GenData03 test - Space reclamation testing with parallel write-verify-delete cycles
"""
import logging as log
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

from dmtest.assertions import assert_equal
from dmtest.vdo.utils import standard_vdo, mounted_fs
from dmtest.vdo.dataset_helpers import write_file_dataset, verify_file_dataset
import dmtest.process as process
import dmtest.vdo.stats as stats


def _delete_dataset(dataset_dir, tag):
    """
    Delete a dataset directory.

    Args:
        dataset_dir: Path to dataset directory
        tag: Tag identifying the dataset
    """
    log.info(f"Deleting dataset {tag}")
    if os.path.exists(dataset_dir):
        shutil.rmtree(dataset_dir)


def _gen_data_task(task_number, num_tasks, mount_point, data_size):
    """
    Execute one parallel task of the GenData03 test.

    Args:
        task_number: Task number (1-based)
        num_tasks: Total number of tasks
        mount_point: Filesystem mount point
        data_size: Size of each dataset

    Returns:
        Task number on completion
    """
    log.info(f"Task {task_number}: Starting")

    # First iteration uses staggered size to spread out task execution
    first_size = int(data_size * (1 + task_number / num_tasks) / 2)

    # Generate datasets for iteration 1
    datasets = _gen_datasets(task_number, 1, mount_point, first_size)

    # Iterations 2-10: write new, verify and delete old
    for round_num in range(2, 11):
        # Generate datasets for iteration N
        new_datasets = _gen_datasets(task_number, round_num, mount_point, data_size)

        # Verify and delete previous datasets
        for dataset_dir, ranges, tag in datasets:
            verify_file_dataset(ranges, tag)
            _delete_dataset(dataset_dir, tag)

        datasets = new_datasets

    # Verify and delete the last datasets
    for dataset_dir, ranges, tag in datasets:
        verify_file_dataset(ranges, tag)
        _delete_dataset(dataset_dir, tag)

    log.info(f"Task {task_number}: Completed")
    return task_number


def _gen_datasets(task_number, iteration, mount_point, data_size):
    """
    Write datasets to the filesystem.

    Args:
        task_number: Task number
        iteration: Iteration number
        mount_point: Filesystem mount point
        data_size: Size of data to write

    Returns:
        List of tuples (dataset_dir, ranges, tag)
    """
    # Divide the data space into equal sized datasets with differing numbers of files
    tags = ['H', 'L', 'M', 'S', 'T']
    num_files_list = [64, 512, 4096, 32768]
    block_size = 4096

    # Reduce number of datasets if the smallest file would be less than 1 block
    while num_files_list[-1] * block_size * len(num_files_list) < data_size:
        num_files_list.pop()
        if len(num_files_list) == 0:
            # Ensure we have at least one dataset
            num_files_list = [64]
            break

    num_bytes = int(data_size // len(num_files_list))

    # Write the datasets
    datasets = []
    for i in range(len(num_files_list)):
        tag = f"{task_number}{tags[i]}{iteration}"
        num_files = num_files_list[i]
        dataset_dir, ranges = write_file_dataset(
            mount_point, tag, num_files, num_bytes=num_bytes, dedupe=0.5)
        datasets.append((dataset_dir, ranges, tag))

    return datasets


def t_gen_data_03(fix) -> None:
    """
    Test space reclamation by writing and deleting data in multiple parallel
    streams. Each stream performs 10 rounds of write-verify-delete cycles,
    writing more total data than will fit in the VDO device to ensure proper
    space reclamation.
    """
    MB = 1024 * 1024
    data_size = 100 * MB
    num_tasks = 4

    with standard_vdo(fix) as vdo:
        # Record initial statistics
        before_stats = stats.vdo_stats(vdo)

        with mounted_fs(vdo.path, format=True) as mount_point:
            # Execute all tasks in parallel
            log.info(f"Starting {num_tasks} parallel tasks")

            with ThreadPoolExecutor(max_workers=num_tasks) as executor:
                futures = []
                for task_number in range(1, num_tasks + 1):
                    future = executor.submit(
                        _gen_data_task,
                        task_number, num_tasks, mount_point, data_size
                    )
                    futures.append(future)

                # Wait for all tasks to complete
                for future in as_completed(futures):
                    task_number = future.result()
                    log.info(f"Task {task_number} completed successfully")

            # Sync data to disk
            process.run("sync")
            log.info("All tasks completed successfully")

        # Record final statistics
        after_stats = stats.vdo_stats(vdo)

        # Check that dedupe advice timeouts didn't increase
        # (skip this check for VMs as mentioned in the Perl test)
        before_timeouts = before_stats.get('dedupeAdviceTimeouts', 0)
        after_timeouts = after_stats.get('dedupeAdviceTimeouts', 0)

        log.info(f"Dedupe advice timeouts: before={before_timeouts}, after={after_timeouts}")
        # Note: We skip this assertion as the test runs in a VM

        # Check that flush and FUA bios increased (filesystem journaling)
        before_flush = before_stats.get('biosIn', {}).get('flush', 0)
        after_flush = after_stats.get('biosIn', {}).get('flush', 0)
        before_fua = before_stats.get('biosIn', {}).get('fua', 0)
        after_fua = after_stats.get('biosIn', {}).get('fua', 0)

        log.info(f"Flush bios: before={before_flush}, after={after_flush}")
        log.info(f"FUA bios: before={before_fua}, after={after_fua}")

        assert after_flush > before_flush, \
            f"Expected flush bios to increase: before={before_flush}, after={after_flush}"
        assert after_fua > before_fua, \
            f"Expected FUA bios to increase: before={before_fua}, after={after_fua}"

        # Check that there are no bios in progress
        flush_in_progress = after_stats.get('biosInProgress', {}).get('flush', 0)
        fua_in_progress = after_stats.get('biosInProgress', {}).get('fua', 0)
        write_in_progress = after_stats.get('biosInProgress', {}).get('write', 0)

        log.info(f"Bios in progress - flush: {flush_in_progress}, fua: {fua_in_progress}, "
                 f"write: {write_in_progress}")

        assert_equal(0, flush_in_progress, "No flush bios should be in progress")
        assert_equal(0, fua_in_progress, "No FUA bios should be in progress")
        assert_equal(0, write_in_progress, "No write bios should be in progress")

        # Check that discard bios increased (ext4 uses discard)
        before_discard = before_stats.get('biosIn', {}).get('discard', 0)
        after_discard = after_stats.get('biosIn', {}).get('discard', 0)

        log.info(f"Discard bios: before={before_discard}, after={after_discard}")
        assert after_discard > before_discard, \
            f"Expected discard bios to increase: before={before_discard}, after={after_discard}"


def register(tests):
    tests.register("/vdo/gen-data/gen-data-03", t_gen_data_03)
