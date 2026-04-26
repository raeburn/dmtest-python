"""
VDO Dual01 test - Concurrent data writes and discard operations.

Tests VDO's handling of simultaneous write and discard operations by running
them on separate logical volumes backed by the same VDO device.
"""

import logging as log
import os
import tempfile
import threading

from dmtest.fs import Ext4
from dmtest.gendatablocks import make_block_range
import dmtest.process as process
from dmtest.vdo.utils import standard_vdo, GB, fsync, settle_devices


def t_dual(fix) -> None:
    """Run concurrent writes and discards on separate LVs backed by VDO.

    Creates a VDO device, puts an LVM volume group on top, then creates
    two logical volumes. Runs discard operations on one LV while writing
    deduplicated data to a filesystem on the other LV.
    """
    with standard_vdo(fix, logical_size=64 * GB) as vdo:
        vg_name = "dual_vg"
        discard_lv_name = "discard"
        generate_lv_name = "generate"

        try:
            # Create volume group on top of VDO
            log.info(f"Creating volume group {vg_name} on {vdo.path}")
            process.run(f"vgcreate {vg_name} {vdo.path}")

            # Get free space and divide it in half
            rc, stdout, stderr = process.run(f"vgs --noheadings --units b -o vg_free {vg_name}")
            vg_free_bytes = int(stdout.rstrip('B'))
            lv_size_bytes = vg_free_bytes // 2

            # Round down to GB for cleaner LVM allocation
            lv_size_gb = (lv_size_bytes // GB)
            log.info(f"VG free space: {vg_free_bytes} bytes, creating {lv_size_gb}G LVs")

            # Create discard logical volume
            log.info(f"Creating discard LV: {discard_lv_name}")
            process.run(f"lvcreate -L {lv_size_gb}G -n {discard_lv_name} {vg_name}")
            discard_path = f"/dev/{vg_name}/{discard_lv_name}"

            # Create generate logical volume
            log.info(f"Creating generate LV: {generate_lv_name}")
            process.run(f"lvcreate -l 100%FREE -n {generate_lv_name} {vg_name}")
            generate_path = f"/dev/{vg_name}/{generate_lv_name}"

            # Wait for device nodes to appear
            settle_devices()

            # Format filesystem on generate LV
            log.info(f"Creating ext4 filesystem on {generate_path}")
            fs = Ext4(generate_path)
            fs.format()

            # Prepare for concurrent operations
            discard_error = None
            generate_error = None

            def discard_worker():
                """Continuously run discard operations."""
                nonlocal discard_error
                try:
                    log.info(f"Starting discard operations on {discard_path}")
                    # Run blkdiscard repeatedly
                    # The Perl test uses SliceOperation trim which runs continuously
                    # We'll do it a reasonable number of times
                    for i in range(20):
                        log.info(f"Discard iteration {i+1}/20")
                        process.run(f"blkdiscard {discard_path}")
                        # Sync after discard (replaces --sync option)
                        process.run(f"sync -d {discard_path}")
                    log.info("Discard operations completed")
                except Exception as e:
                    log.error(f"Discard worker error: {e}")
                    discard_error = e

            def generate_worker():
                """Write and verify data with deduplication."""
                nonlocal generate_error
                try:
                    with tempfile.TemporaryDirectory() as mount_point:
                        log.info(f"Mounting {generate_path} at {mount_point}")
                        fs.mount(mount_point)

                        try:
                            # Get device size and use 1/4 of it for data
                            rc, stdout, stderr = process.run(f"blockdev --getsize64 {generate_path}")
                            dev_size = int(stdout)
                            total_bytes = dev_size // 4

                            log.info(f"Device size: {dev_size} bytes, using {total_bytes} bytes for data")
                            log.info("Writing data with 25% deduplication")

                            # Create 1024 files with 25% dedupe
                            file_count = 1024
                            bytes_per_file = total_bytes // file_count
                            blocks_per_file = bytes_per_file // 4096

                            log.info(f"Creating {file_count} files, {blocks_per_file} blocks per file")

                            # Create a data directory
                            data_dir = os.path.join(mount_point, "data")
                            os.makedirs(data_dir)

                            # Write all files with one consistent data stream (25% dedupe)
                            block_ranges = []
                            for i in range(file_count):
                                file_path = os.path.join(data_dir, f"file_{i:04d}")
                                # Create empty file
                                with open(file_path, 'w') as f:
                                    pass
                                # Create block range for this file
                                br = make_block_range(file_path, blocks_per_file)
                                br.write(tag="gen", dedupe=0.25, compress=0.0, fsync=False)
                                block_ranges.append(br)

                            # Sync all data
                            log.info("Syncing filesystem")
                            fsync(generate_path)

                            log.info("Verifying written data")
                            for i, br in enumerate(block_ranges):
                                if i % 100 == 0:
                                    log.info(f"Verified {i}/{file_count} files")
                                br.verify()
                            log.info("Data verification completed")

                        finally:
                            log.info(f"Unmounting {mount_point}")
                            fs.umount()

                except Exception as e:
                    log.error(f"Generate worker error: {e}")
                    generate_error = e

            # Start both workers
            log.info("Starting concurrent discard and generate operations")
            discard_thread = threading.Thread(target=discard_worker)
            generate_thread = threading.Thread(target=generate_worker)

            discard_thread.start()
            generate_thread.start()

            # Wait for both to complete
            log.info("Waiting for operations to complete")
            discard_thread.join()
            generate_thread.join()

            log.info("Both operations completed")

            # Check for errors
            if discard_error:
                raise discard_error
            if generate_error:
                raise generate_error

        finally:
            # Cleanup: remove LVs and VG
            log.info("Cleaning up LVM resources")
            try:
                # Remove logical volumes
                process.run(f"lvremove -f {vg_name}/{discard_lv_name}", raise_on_fail=False)
                process.run(f"lvremove -f {vg_name}/{generate_lv_name}", raise_on_fail=False)
                # Remove volume group
                process.run(f"vgremove -f {vg_name}", raise_on_fail=False)
            except Exception as e:
                log.warning(f"Cleanup error (non-fatal): {e}")


def register(tests):
    tests.register("/vdo/dual/dual-01", t_dual)
