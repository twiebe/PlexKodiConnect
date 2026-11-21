"""
A full sync deletes from Kodi what the PMS no longer has - and nothing else.
"""
import sqlite3
import threading

import pytest

from resources.lib.library_sync import common, full_sync
from tests import plex

TIMEOUT = 30
SYNCS = []


def full_library_sync(pms, last_sync=0):
    """Runs a full sync of the movie section; returns the FullSync."""
    from resources.lib import app
    app.SYNC.sections = [plex.plex_section(last_sync)]
    sync = full_sync.FullSync(repair=False, callback=None, show_dialog=False)
    # Each sync a minute after the one before, as the last_sync marks need
    SYNCS.append(sync)
    sync.current_time += 60 * len(SYNCS)
    sync.copy_plex_db()
    runner = threading.Thread(target=sync.full_library_sync)
    runner.start()
    runner.join(TIMEOUT)
    assert not runner.is_alive(), 'the sync hangs'
    return sync


def movies(dbs):
    con = sqlite3.connect(dbs['DB_VIDEO_PATH'])
    return sorted(int(r[0].rsplit(' ', 1)[1])
                  for r in con.execute('SELECT c00 FROM movie'))


def genres(dbs, plex_id):
    con = sqlite3.connect(dbs['DB_VIDEO_PATH'])
    return [r[0] for r in con.execute('''
        SELECT genre.name FROM movie
        JOIN genre_link ON genre_link.media_id = movie.idMovie
            AND genre_link.media_type = 'movie'
        JOIN genre ON genre.genre_id = genre_link.genre_id
        WHERE movie.c00 = ?''', ('Movie %s' % plex_id, ))]


@pytest.fixture
def pms(pkc, monkeypatch):
    monkeypatch.setattr(common, 'PLAYLIST_SYNC_ENABLED', False)
    pms = plex.FakePMS([plex.movie(i) for i in range(1, 11)]).install(monkeypatch)
    sync = full_library_sync(pms)
    assert sync.successful
    pms.listing_requests.clear()
    return pms


def test_what_the_pms_deleted_is_deleted(pms, dbs):
    del pms.movies[4]
    assert full_library_sync(pms).successful
    assert movies(dbs) == [1, 2, 3, 5, 6, 7, 8, 9, 10]


def test_a_failed_listing_deletes_nothing(pms, dbs):
    # The third request lists every item, to find the deleted ones
    pms.fail_listings = {3}
    del pms.movies[4]
    sync = full_library_sync(pms, last_sync=1)
    assert not sync.successful
    assert movies(dbs) == list(range(1, 11))


def test_an_item_the_listing_missed_stays(pms, dbs):
    # E.g. items deleted on the PMS while we paged through the listing
    pms.hide_in_listings = {3: {6}}
    assert full_library_sync(pms, last_sync=1).successful
    assert movies(dbs) == list(range(1, 11))


def test_nothing_is_deleted_if_the_pms_cannot_say_what_it_has(pms, dbs):
    del pms.movies[4]
    pms.existence_check_fails = True
    assert not full_library_sync(pms).successful
    assert movies(dbs) == list(range(1, 11))


def test_an_item_found_missing_later_gets_all_its_metadata(pkc, dbs, monkeypatch):
    monkeypatch.setattr(common, 'PLAYLIST_SYNC_ENABLED', False)
    pms = plex.FakePMS([plex.movie(i) for i in range(1, 6)]).install(monkeypatch)
    # The listing for new items misses movie 3, the one for all items has it
    pms.hide_in_listings = {1: {3}}
    assert full_library_sync(pms).successful
    assert movies(dbs) == [1, 2, 3, 4, 5]
    assert genres(dbs, 3) == ['Drama']
