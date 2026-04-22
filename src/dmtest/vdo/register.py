import dmtest.vdo.basic_01_tests as vdo_basic_01
import dmtest.vdo.collide_01_tests as vdo_collide_01
import dmtest.vdo.collide_02_tests as vdo_collide_02
import dmtest.vdo.compress_01_tests as vdo_compress_01
import dmtest.vdo.compress_tests as vdo_compress
import dmtest.vdo.creation_tests as vdo_creation
import dmtest.vdo.dedupe_tests as vdo_dedupe
import dmtest.vdo.full_tests as vdo_full
import dmtest.vdo.full_01_tests as vdo_full_01
import dmtest.vdo.full_02_tests as vdo_full_02
import dmtest.vdo.load_failure_tests as vdo_load_failure

def register(tests):
    vdo_basic_01.register(tests)
    vdo_collide_01.register(tests)
    vdo_collide_02.register(tests)
    vdo_creation.register(tests)
    vdo_dedupe.register(tests)
    vdo_compress.register(tests)
    vdo_compress_01.register(tests)
    vdo_full.register(tests)
    vdo_full_01.register(tests)
    vdo_full_02.register(tests)
    vdo_load_failure.register(tests)
