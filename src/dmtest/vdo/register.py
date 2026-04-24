import dmtest.vdo.basic_01_tests as vdo_basic_01
import dmtest.vdo.basic_fs_dedupe_tests as vdo_basic_fs_dedupe
import dmtest.vdo.collide_01_tests as vdo_collide_01
import dmtest.vdo.collide_02_tests as vdo_collide_02
import dmtest.vdo.collide_03_tests as vdo_collide_03
import dmtest.vdo.compress_01_tests as vdo_compress_01
import dmtest.vdo.compress_tests as vdo_compress
import dmtest.vdo.create_03_tests as vdo_create_03
import dmtest.vdo.creation_tests as vdo_creation
import dmtest.vdo.dedupe_tests as vdo_dedupe
import dmtest.vdo.device_swap_tests as vdo_device_swap
import dmtest.vdo.dual_01_tests as vdo_dual_01
import dmtest.vdo.direct_01_tests as vdo_direct_01
import dmtest.vdo.direct_02_tests as vdo_direct_02
import dmtest.vdo.direct_03_tests as vdo_direct_03
import dmtest.vdo.direct_04_tests as vdo_direct_04
import dmtest.vdo.direct_05_tests as vdo_direct_05
import dmtest.vdo.direct_06_tests as vdo_direct_06
import dmtest.vdo.discard_512_tests as vdo_discard_512
import dmtest.vdo.discard_512_compressed_tests as vdo_discard_512_compressed
import dmtest.vdo.dmsetup_tests as vdo_dmsetup
import dmtest.vdo.full_tests as vdo_full
import dmtest.vdo.full_01_tests as vdo_full_01
import dmtest.vdo.full_02_tests as vdo_full_02
import dmtest.vdo.full_03_tests as vdo_full_03
import dmtest.vdo.full_04_tests as vdo_full_04
import dmtest.vdo.full_warn_tests as vdo_full_warn
import dmtest.vdo.gen_data_01_tests as vdo_gen_data_01
import dmtest.vdo.gen_data_02_tests as vdo_gen_data_02
import dmtest.vdo.gen_data_03_tests as vdo_gen_data_03
import dmtest.vdo.gen_data_04_tests as vdo_gen_data_04
import dmtest.vdo.grow_logical_03_tests as vdo_grow_logical_03
import dmtest.vdo.in_flight_dedupe_and_compress_tests as vdo_in_flight_dedupe_and_compress
import dmtest.vdo.instance_tests as vdo_instance
import dmtest.vdo.load_failure_tests as vdo_load_failure
import dmtest.vdo.major_minor_tests as vdo_major_minor
import dmtest.vdo.slab_count_01_tests as vdo_slab_count_01
import dmtest.vdo.sysfs_tests as vdo_sysfs
import dmtest.vdo.vdo_rename_tests as vdo_rename
import dmtest.vdo.zero_01_tests as vdo_zero_01

def register(tests):
    vdo_basic_01.register(tests)
    vdo_basic_fs_dedupe.register(tests)
    vdo_collide_01.register(tests)
    vdo_collide_02.register(tests)
    vdo_collide_03.register(tests)
    vdo_create_03.register(tests)
    vdo_creation.register(tests)
    vdo_dedupe.register(tests)
    vdo_device_swap.register(tests)
    vdo_dual_01.register(tests)
    vdo_direct_01.register(tests)
    vdo_direct_02.register(tests)
    vdo_direct_03.register(tests)
    vdo_direct_04.register(tests)
    vdo_direct_05.register(tests)
    vdo_direct_06.register(tests)
    vdo_discard_512.register(tests)
    vdo_discard_512_compressed.register(tests)
    vdo_dmsetup.register(tests)
    vdo_compress.register(tests)
    vdo_compress_01.register(tests)
    vdo_full.register(tests)
    vdo_full_01.register(tests)
    vdo_full_02.register(tests)
    vdo_full_03.register(tests)
    vdo_full_04.register(tests)
    vdo_full_warn.register(tests)
    vdo_gen_data_01.register(tests)
    vdo_gen_data_02.register(tests)
    vdo_gen_data_03.register(tests)
    vdo_gen_data_04.register(tests)
    vdo_grow_logical_03.register(tests)
    vdo_in_flight_dedupe_and_compress.register(tests)
    vdo_instance.register(tests)
    vdo_load_failure.register(tests)
    vdo_major_minor.register(tests)
    vdo_rename.register(tests)
    vdo_slab_count_01.register(tests)
    vdo_sysfs.register(tests)
    vdo_zero_01.register(tests)
