import sqlite3


def test_the_test_dbs_are_set_up_as_pkc_does_it(dbs):
    con = sqlite3.connect(dbs['DB_PLEX_PATH'])
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'movie', 'show', 'season', 'episode', 'sections'} <= tables
    con = sqlite3.connect(dbs['DB_VIDEO_PATH'])
    assert con.execute('SELECT COUNT(*) FROM path').fetchone()[0] >= 2
