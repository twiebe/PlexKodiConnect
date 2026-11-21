"""
The passes over every item, each full sync, write only what changed.
"""
import sqlite3

import pytest

from resources.lib.library_sync import common
from tests import plex
from tests.test_sync_deletion import full_library_sync, movies

WATCHED = ('files', 'bookmark', 'movie')


def log_writes(dbs):
    con = sqlite3.connect(dbs['DB_VIDEO_PATH'])
    con.execute('CREATE TABLE writes(tbl TEXT)')
    for table in WATCHED:
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            con.execute("CREATE TRIGGER log_%s_%s AFTER %s ON %s BEGIN "
                        "INSERT INTO writes VALUES ('%s'); END"
                        % (table, event, event, table, table))
    con.commit()
    return con


def writes(con):
    return [r[0] for r in con.execute('SELECT tbl FROM writes')]


@pytest.fixture
def pms(pkc, monkeypatch):
    monkeypatch.setattr(common, 'PLAYLIST_SYNC_ENABLED', False)
    pms = plex.FakePMS([plex.movie(1), plex.movie(2, view_offset=600000),
                        plex.movie(3, view_count=1, user_rating=8)])
    pms.install(monkeypatch)
    assert full_library_sync(pms).successful
    return pms


def test_nothing_changed_nothing_written(pms, dbs):
    con = log_writes(dbs)
    assert full_library_sync(pms, last_sync=1).successful
    assert writes(con) == []


def test_a_new_rating_and_resume_point_are_written(pms, dbs):
    pms.movies[1] = plex.movie(1, user_rating=6, view_offset=1200000)
    assert full_library_sync(pms, last_sync=1).successful
    con = sqlite3.connect(dbs['DB_VIDEO_PATH'])
    assert con.execute("SELECT userrating FROM movie WHERE c00 = 'Movie 1'"
                       ).fetchone() == (6,)
    assert con.execute('''SELECT timeInSeconds FROM bookmark
                          JOIN movie ON movie.idFile = bookmark.idFile
                          WHERE c00 = 'Movie 1' AND type = 1''').fetchone() == (1200.0,)


def test_the_users_own_bookmarks_stay(pms, dbs):
    con = sqlite3.connect(dbs['DB_VIDEO_PATH'])
    file_id = con.execute("SELECT idFile FROM movie WHERE c00 = 'Movie 2'"
                          ).fetchone()[0]
    con.execute("INSERT INTO bookmark(idFile, timeInSeconds, totalTimeInSeconds,"
                " thumbNailImage, player, playerState, type) VALUES "
                "(?, 42, 5400, '', 'VideoPlayer', '', 0)", (file_id, ))
    con.commit()
    pms.movies[2] = plex.movie(2, view_offset=900000)
    assert full_library_sync(pms, last_sync=1).successful
    assert sorted(con.execute('SELECT type, timeInSeconds FROM bookmark '
                              'WHERE idFile = ?', (file_id, )).fetchall()) == \
        [(0, 42.0), (1, 900.0)]


def test_a_section_no_longer_synced_goes_without_asking_the_pms(pms, dbs, pkc):
    pkc.SYNC.section_ids = set()
    pms.existence_check_fails = True
    pkc.SYNC.sections = []
    from resources.lib.library_sync import full_sync
    sync = full_sync.FullSync(repair=False, callback=None, show_dialog=False)
    for plex_type, context in (('movie', full_sync.itemtypes.Movie), ):
        assert sync.delete_missing_items(plex_type, context)
    assert movies(dbs) == []
