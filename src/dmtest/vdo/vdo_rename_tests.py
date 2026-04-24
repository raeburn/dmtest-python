import logging as log

import dmtest.device_mapper.dev as dmdev
import dmtest.device_mapper.interface as dm
import dmtest.vdo.stats as stats
import dmtest.vdo.vdo_stack as vs


def t_rename(fix) -> None:
    """Test VDO device renaming using dmsetup rename."""
    log.info("Creating VDO device with known name")
    original_name = "test_vdo_rename"

    # Create VDO stack and activate with a known name
    stack = vs.VDOStack(fix.cfg["data_dev"])
    dev = dmdev.Dev(original_name)
    dev.load(stack._vdo_table())
    dev.resume()

    try:
        # Verify original device works
        log.info(f"Getting stats for original device '{original_name}'")
        original_stats = stats.vdo_stats(dev)
        log.info(f"Original device stats: data blocks used = {original_stats['dataBlocksUsed']}")

        # Rename device
        new_name = original_name + "A"
        log.info(f"Renaming device from '{original_name}' to '{new_name}'")
        dm.rename(original_name, new_name)

        # Update device object to reflect new name
        new_dev = dmdev.Dev.__new__(dmdev.Dev)
        new_dev._name = new_name
        new_dev._path = f"/dev/mapper/{new_name}"
        new_dev._active_table = dev._active_table

        # Verify renamed device works
        log.info(f"Getting stats for renamed device '{new_name}'")
        renamed_stats = stats.vdo_stats(new_dev)
        log.info(f"Renamed device stats: data blocks used = {renamed_stats['dataBlocksUsed']}")

        # Rename back to original
        log.info(f"Renaming device back from '{new_name}' to '{original_name}'")
        dm.rename(new_name, original_name)

        # Verify original device name works again
        log.info(f"Getting stats for restored device '{original_name}'")
        restored_stats = stats.vdo_stats(dev)
        log.info(f"Restored device stats: data blocks used = {restored_stats['dataBlocksUsed']}")

    finally:
        # Cleanup - make sure we remove using the correct current name
        try:
            dm.remove(original_name)
        except:
            try:
                dm.remove(new_name)
            except:
                pass


def register(tests):
    tests.register("/vdo/dmsetup/rename", t_rename)
