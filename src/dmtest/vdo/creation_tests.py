from dmtest.vdo.utils import standard_vdo

def t_create(fix):
    with standard_vdo(fix) as vdo:
        pass

def register(tests):
    tests.register("/vdo/creation/create01", t_create)
