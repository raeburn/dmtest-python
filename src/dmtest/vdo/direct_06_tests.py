"""
VDO Direct06 test - Comprehensive block-level I/O, deduplication, and trim
"""
import logging as log
import math
import os
import time

from dmtest.assertions import assert_equal
from dmtest.gendatablocks import make_block_range
from dmtest.vdo.utils import BLOCK_SIZE, standard_vdo
import dmtest.process as process
import dmtest.vdo.stats as stats


def _wait_for_vdo_idle(vdo, timeout_seconds=30):
    """
    Wait for VDO to finish all in-progress I/Os.

    Args:
        vdo: VDO device object
        timeout_seconds: Maximum time to wait
    """
    start_time = time.time()
    while True:
        current_stats = stats.vdo_stats(vdo)
        vios_in_progress = current_stats.get('currentVIOsInProgress', 0)

        if vios_in_progress == 0:
            log.debug("VDO is idle (no VIOs in progress)")
            return

        if time.time() - start_time > timeout_seconds:
            log.warning(f"Timeout waiting for VDO to become idle, {vios_in_progress} VIOs still in progress")
            return

        log.debug(f"Waiting for VDO to become idle ({vios_in_progress} VIOs in progress)...")
        time.sleep(0.1)


def _get_max_discard_blocks(vdo_path):
    """
    Get the maximum number of 4KB blocks that can be discarded in one operation.

    Returns:
        int: Maximum discard blocks
    """
    # Extract device name from path (e.g., /dev/mapper/vdo-test -> vdo-test)
    dev_name = os.path.basename(vdo_path)

    # For device-mapper devices, we need to look under /sys/block/dm-*/
    # Find the correct dm device number
    try:
        # Read the discard_max_bytes from sysfs
        sysfs_paths = [
            f"/sys/block/{dev_name}/queue/discard_max_bytes",
            f"/sys/class/block/{dev_name}/queue/discard_max_bytes"
        ]

        for sysfs_path in sysfs_paths:
            if os.path.exists(sysfs_path):
                with open(sysfs_path, 'r') as f:
                    discard_max_bytes = int(f.read().strip())
                    # Convert bytes to 4KB blocks
                    max_discard_blocks = discard_max_bytes // BLOCK_SIZE
                    log.debug(f"VDO max discard: {discard_max_bytes} bytes = {max_discard_blocks} blocks")
                    return max_discard_blocks

        # If we can't find the sysfs entry, use a safe default
        # Most systems support at least 2GB discard
        log.warning(f"Could not read discard_max_bytes from sysfs, using default")
        return (2 * 1024 * 1024 * 1024) // BLOCK_SIZE  # 2GB in 4KB blocks

    except Exception as e:
        log.warning(f"Error reading max discard: {e}, using default")
        return (2 * 1024 * 1024 * 1024) // BLOCK_SIZE


def t_direct_06(fix) -> None:
    """
    Comprehensive VDO functional test exercising direct block I/O, deduplication
    verification, device restart persistence, discard/trim operations, and complex
    deduplication edge cases including overwrites of deduplicated data and shifted
    duplicate writes.
    """
    block_count = 5000

    with standard_vdo(fix, slab_bits=17) as vdo:
        # Calculate trims per dataset for statistics checking
        max_discard_blocks = _get_max_discard_blocks(vdo.path)
        trims_per_dataset = math.ceil(block_count / max_discard_blocks)
        log.info(f"Max discard blocks: {max_discard_blocks}, "
                f"trims per dataset: {trims_per_dataset}")

        # Step 1: Verify initial VDO statistics are all zero
        log.info("Verifying initial VDO statistics are zero")
        initial_stats = stats.vdo_stats(vdo)

        # Extract nested stats for cleaner access
        hash_lock_stats = initial_stats.get('hashLock', {})
        index_stats = initial_stats.get('index', {})
        bio_stats = initial_stats.get('biosIn', {})
        bio_out_stats = initial_stats.get('biosOut', {})

        assert_equal(bio_stats.get('write', 0), 0, "initial bios in write")
        assert_equal(bio_out_stats.get('write', 0), 0, "initial bios out write")
        assert_equal(initial_stats.get('dataBlocksUsed', 0), 0, "initial data blocks used")
        assert_equal(hash_lock_stats.get('dedupeAdviceValid', 0), 0, "initial dedupe advice valid")
        assert_equal(hash_lock_stats.get('dedupeAdviceStale', 0), 0, "initial dedupe advice stale")
        assert_equal(initial_stats.get('dedupeAdviceTimeouts', 0), 0, "initial dedupe advice timeouts")
        assert_equal(index_stats.get('entriesIndexed', 0), 0, "initial entries indexed")

        # Step 2: Write 5000 blocks with direct I/O (slice1 at offset 0)
        log.info(f"Step 2: Writing first slice: {block_count} blocks at offset 0 with tag 'Direct1'")
        slice1 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                   block_count=block_count, offset=0)
        slice1.write(tag="Direct1", dedupe=0, compress=0, direct=True, fsync=True)
        slice1.verify()

        # Check statistics after first write
        after_first = stats.vdo_stats(vdo)
        assert_equal(after_first['dataBlocksUsed'], block_count,
                    "data blocks used after first write")
        assert_equal(after_first['biosIn']['write'], block_count,
                    "bios in write after first write")
        assert_equal(after_first['biosOut']['write'], block_count,
                    "bios out write after first write")
        assert_equal(after_first['index']['entriesIndexed'], block_count,
                    "entries indexed after first write")

        # Step 3: Write same 5000 blocks to different location (100% deduplication)
        log.info(f"Step 3: Writing second slice: {block_count} blocks at offset {block_count} "
                "with same tag (testing deduplication)")
        slice2 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                   block_count=block_count, offset=block_count)
        slice2.write(tag="Direct1", dedupe=0, compress=0, direct=True, fsync=True)
        slice2.verify()

        # Check statistics - should show full deduplication
        after_second = stats.vdo_stats(vdo)
        assert_equal(after_second['hashLock']['dedupeAdviceValid'], block_count,
                    "dedupe advice valid after second write")
        assert_equal(after_second['biosIn']['write'], block_count * 2,
                    "bios in write after second write (all writes counted)")
        assert_equal(after_second['biosOut']['write'], block_count,
                    "bios out write after second write (no new physical writes)")
        assert_equal(after_second['dataBlocksUsed'], block_count,
                    "data blocks used unchanged (dedup prevented new allocation)")

    # Step 4: Restart VDO device to verify data persistence
    log.info("Step 4: Restarting VDO device to verify data persistence")

    with standard_vdo(fix, format=False, slab_bits=17) as vdo:
        # Update paths for the block ranges after restart
        slice1.update_path(vdo.path)
        slice2.update_path(vdo.path)

        # Check that partial I/O statistics are zero (VDO-4248 bug fix verification)
        restart_stats = stats.vdo_stats(vdo)
        bio_in = restart_stats.get('biosIn', {})
        bio_ack = restart_stats.get('biosOutCompleted', {})

        assert_equal(bio_in.get('readPartial', 0), 0, "bios in partial read after restart")
        assert_equal(bio_in.get('writePartial', 0), 0, "bios in partial write after restart")
        assert_equal(bio_in.get('discardPartial', 0), 0, "bios in partial discard after restart")
        assert_equal(bio_in.get('flushPartial', 0), 0, "bios in partial flush after restart")
        assert_equal(bio_in.get('fuaPartial', 0), 0, "bios in partial fua after restart")
        assert_equal(bio_ack.get('readPartial', 0), 0, "bios ack partial read after restart")
        assert_equal(bio_ack.get('writePartial', 0), 0, "bios ack partial write after restart")
        assert_equal(bio_ack.get('discardPartial', 0), 0, "bios ack partial discard after restart")
        assert_equal(bio_ack.get('flushPartial', 0), 0, "bios ack partial flush after restart")
        assert_equal(bio_ack.get('fuaPartial', 0), 0, "bios ack partial fua after restart")

        # Step 5: Drop page cache and verify data persists
        log.info("Step 5: Dropping page cache and verifying data persists")
        process.run("sh -c 'echo 3 > /proc/sys/vm/drop_caches'")
        slice1.verify()
        slice2.verify()

        # Step 6: Trim slice1 (first reference to deduplicated data)
        log.info("Step 6: Trimming first slice (should not free blocks due to slice2 reference)")
        before_trim1 = stats.vdo_stats(vdo)
        slice1.trim(fsync=False)
        _wait_for_vdo_idle(vdo)
        process.run("sh -c 'echo 3 > /proc/sys/vm/drop_caches'")

        # Verify data after trim
        slice1.verify()  # Should read zeros
        slice2.verify()  # Should still have data

        after_trim1 = stats.vdo_stats(vdo)
        assert_equal(after_trim1['dataBlocksUsed'], block_count,
                    "data blocks used unchanged after trim1 (slice2 still references)")

        # Step 7: Trim slice2 (last reference - should free blocks)
        log.info("Step 7: Trimming second slice (should free all blocks)")
        slice2.trim(fsync=False)
        _wait_for_vdo_idle(vdo)
        process.run("sh -c 'echo 3 > /proc/sys/vm/drop_caches'")

        # Verify both read zeros after trim
        slice1.verify()
        slice2.verify()

        after_trim2 = stats.vdo_stats(vdo)
        assert_equal(after_trim2['dataBlocksUsed'], 0,
                    "data blocks used should be zero after trimming both slices")

        # Step 8: Write new data to slice1 with tag "Direct2"
        log.info("Step 8: Writing new data to slice1 with tag 'Direct2'")
        slice1.write(tag="Direct2", dedupe=0, compress=0, direct=True, fsync=True)
        slice1.verify()

        after_direct2 = stats.vdo_stats(vdo)
        assert_equal(after_direct2['dataBlocksUsed'], block_count,
                    "data blocks used after writing Direct2")

        # Step 9: Rewrite same "Direct2" data to same location (self-deduplication)
        log.info("Step 9: Rewriting same data to same location (self-deduplication test)")
        before_rewrite = stats.vdo_stats(vdo)
        slice1.write(tag="Direct2", dedupe=0, compress=0, direct=True, fsync=True)
        slice1.verify()

        after_rewrite = stats.vdo_stats(vdo)
        # Data blocks used should remain the same
        assert_equal(after_rewrite['dataBlocksUsed'], before_rewrite['dataBlocksUsed'],
                    "data blocks used unchanged after self-deduplication")

        # Step 10: Write new data to slice2 with tag "Direct5" (using fsync instead of direct)
        log.info("Step 10: Writing new data to slice2 with tag 'Direct5'")
        slice2.write(tag="Direct5", dedupe=0, compress=0, direct=True, fsync=True)
        slice2.verify()

        after_direct5 = stats.vdo_stats(vdo)
        assert_equal(after_direct5['dataBlocksUsed'], block_count * 2,
                    "data blocks used after writing Direct5 to slice2")

        # Step 11: Write same "Direct5" dataset shifted by one block
        # This creates 4999 blocks of overlap testing dedup against data being overwritten
        log.info("Step 11: Writing shifted dataset (offset +1) to test overlapping deduplication")
        slice3 = make_block_range(path=vdo.path, block_size=BLOCK_SIZE,
                                   block_count=block_count, offset=block_count + 1)
        slice3.write(tag="Direct5", dedupe=0, compress=0, direct=True, fsync=True)
        slice3.verify()

        after_shifted = stats.vdo_stats(vdo)
        # Should stay at 2*block_count due to full deduplication
        # Both slices use the same tag "Direct5" so they generate identical data
        assert_equal(after_shifted['dataBlocksUsed'], block_count * 2,
                    "data blocks used after shifted write (full deduplication)")

        # Step 12: Rewrite "Direct5" data to original slice2 location
        log.info("Step 12: Rewriting Direct5 to original location (overwriting shared deduplicated data)")
        slice2.write(tag="Direct5", dedupe=0, compress=0, direct=True, fsync=True)
        slice2.verify()

        # Data blocks should remain the same
        after_rewrite2 = stats.vdo_stats(vdo)
        assert_equal(after_rewrite2['dataBlocksUsed'], after_shifted['dataBlocksUsed'],
                    "data blocks used unchanged after rewriting shared data")

        # Step 13: Trim slice1 (should free 5000 blocks)
        log.info("Step 13: Trimming slice1 (should free 5000 blocks)")
        slice1.trim(fsync=False)
        _wait_for_vdo_idle(vdo)

        after_trim_slice1 = stats.vdo_stats(vdo)
        # Should go from 10000 to 5000 (removing all Direct2 blocks from slice1)
        assert_equal(after_trim_slice1['dataBlocksUsed'], block_count,
                    "data blocks used after trimming slice1")

        # Verify slice2 data remains intact
        process.run("sh -c 'echo 3 > /proc/sys/vm/drop_caches'")
        slice1.verify()
        slice2.verify()

        # Step 14: Trim slice2 (which also trims most of slice3's data)
        log.info("Step 14: Trimming slice2 (frees all but 1 block)")
        slice2.trim(fsync=False)
        _wait_for_vdo_idle(vdo)

        after_trim_slice2 = stats.vdo_stats(vdo)
        # Trimming slice2 (logical blocks 5000-9999) also discards most of slice3's data
        # Only logical block 10000 (from slice3) remains untrimmed
        # So only 1 data block should remain
        assert_equal(after_trim_slice2['dataBlocksUsed'], 1,
                    "data blocks used after trimming slice2 (only block 10000 remains)")

        # Verify slice2 reads zeros
        process.run("sh -c 'echo 3 > /proc/sys/vm/drop_caches'")
        slice2.verify()

        log.info("Direct06 test completed successfully")


def register(tests):
    tests.register("/vdo/direct/direct-06", t_direct_06)
