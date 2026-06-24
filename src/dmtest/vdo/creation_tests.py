from dmtest.vdo.utils import standard_vdo

def test_create(fix):
    with standard_vdo(fix) as vdo:
        pass
