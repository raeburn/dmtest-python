import logging as log
import os
import shutil
import tempfile

from dmtest.blk_archive.common import unit_test_data_context, Data, BlkArchive, rs


def test_src_dest_combinations(fix):
    test_dir = tempfile.mkdtemp(prefix='blk_archive_unit_test_', dir="/")

    try:
        with unit_test_data_context(test_dir, fix) as utd:
            src = utd.create([Data.Type.BASIC, Data.Type.DM_THIN, Data.Type.DM_THICK])

            archive = BlkArchive(os.path.join(test_dir, f"test_archive_{rs(8)}"))

            for s in src:
                s.create_fs().mount().fill().unmount()

                # Pack source
                stream_id = archive.pack(s.src_arg())

                # Dump stream, to ensure it simply doesn't panic
                archive.dump_stream(stream_id)

                # Unpack source to each of the different destination targets
                for d in utd.create([Data.Type.BASIC, Data.Type.FILE, Data.Type.DM_THIN, Data.Type.DM_THICK]):
                    log.info(f"destination is {d}")

                    archive.unpack(stream_id, d.dest_arg())

                    archive.verify(s.src_arg(), stream_id)

                    log.info(f"Comparing {s} to {d}")
                    # MAYBE blk-archive -j verify -a <archive> -s <stream> d.dest_arg()

                    if not s.compare(d):
                        raise ValueError(f"Data miss-compare, src {s} != dest {d}")

                    d.unmount()
                    d.destroy()

    finally:
        shutil.rmtree(test_dir)


