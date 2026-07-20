"""VDO compression and deduplication toggle tests.

Tests that VDO compression and deduplication can be switched on and off
at runtime, via dmsetup messages and via table reloads.
Converted from CompressDedupeDefaults.pm.
"""
import logging as log

from dmtest.assertions import assert_equal
from dmtest.vdo.utils import standard_vdo, wait_for_index, MB, GB
import dmtest.vdo.status as vdo_status
import dmtest.device_mapper.table as table
import dmtest.device_mapper.targets as targets
import dmtest.utils as utils


def _get_states(vdo) -> tuple[str, str]:
    """Return (compress_state, index_state) from VDO status."""
    s = vdo_status.vdo_status(vdo)
    return s['compress-state'], s['index-state']


def _make_vdo_table(fix, **extra_opts) -> table.Table:
    """Build a VDO table matching standard_vdo defaults, with extra opts."""
    data_dev = fix.cfg['data_dev']
    physical_size = utils.dev_size(data_dev) * 512
    logical_size = 20 * GB
    return table.Table(
        targets.VDOTarget(
            logical_size // 512,
            data_dev,
            physical_size // 4096,
            4096,
            128 * MB // 4096,
            16380,
            extra_opts,
        )
    )


def t_toggle_via_message(fix) -> None:
    """Test toggling compression and deduplication on/off via dmsetup messages."""
    with standard_vdo(fix, compression='on') as vdo:
        wait_for_index(vdo)

        log.info("Verifying initial state: compression and deduplication both on")
        compress, index = _get_states(vdo)
        assert_equal(compress, 'online')
        assert_equal(index, 'online')

        log.info("Disabling compression via 'compression off' message")
        vdo.message(0, "compression", "off")
        compress, index = _get_states(vdo)
        assert compress != 'online', f"Expected compression off, got '{compress}'"
        assert_equal(index, 'online')

        log.info("Re-enabling compression via 'compression on' message")
        vdo.message(0, "compression", "on")
        compress, index = _get_states(vdo)
        assert_equal(compress, 'online')
        assert_equal(index, 'online')

        log.info("Disabling deduplication via 'index-close' message")
        vdo.message(0, "index-close")
        compress, index = _get_states(vdo)
        assert_equal(compress, 'online')
        assert index != 'online', f"Expected deduplication off, got '{index}'"

        log.info("Re-enabling deduplication via 'index-enable' message")
        vdo.message(0, "index-enable")
        wait_for_index(vdo)
        compress, index = _get_states(vdo)
        assert_equal(compress, 'online')
        assert_equal(index, 'online')


def t_toggle_via_table_reload(fix) -> None:
    """Test toggling compression and deduplication on/off via table reloads."""
    with standard_vdo(fix, compression='on') as vdo:
        wait_for_index(vdo)

        log.info("Verifying initial state: compression and deduplication both on")
        compress, index = _get_states(vdo)
        assert_equal(compress, 'online')
        assert_equal(index, 'online')

        log.info("Disabling compression via table reload")
        with vdo.pause():
            vdo.load(_make_vdo_table(fix, compression='off',
                                     deduplication='on'))
        compress, index = _get_states(vdo)
        assert compress != 'online', f"Expected compression off, got '{compress}'"
        assert_equal(index, 'online')

        log.info("Re-enabling compression via table reload")
        with vdo.pause():
            vdo.load(_make_vdo_table(fix, compression='on',
                                     deduplication='on'))
        compress, index = _get_states(vdo)
        assert_equal(compress, 'online')
        assert_equal(index, 'online')

        log.info("Disabling deduplication via table reload")
        with vdo.pause():
            vdo.load(_make_vdo_table(fix, compression='on',
                                     deduplication='off'))
        compress, index = _get_states(vdo)
        assert_equal(compress, 'online')
        assert index != 'online', f"Expected deduplication off, got '{index}'"

        log.info("Re-enabling deduplication via table reload")
        with vdo.pause():
            vdo.load(_make_vdo_table(fix, compression='on',
                                     deduplication='on'))
        wait_for_index(vdo)
        compress, index = _get_states(vdo)
        assert_equal(compress, 'online')
        assert_equal(index, 'online')


def register(tests):
    tests.register_batch("/vdo/compress-dedupe-flags/", [
        ("toggle-via-message", t_toggle_via_message),
        ("toggle-via-table-reload", t_toggle_via_table_reload),
    ])
