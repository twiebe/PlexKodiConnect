"""What a review found: Kodi must get to write, and nothing may hang"""
import sqlite3
import threading
import time

import pytest

from resources.lib import db, exceptions, itemtypes, plex_functions as PF
from resources.lib.library_sync import full_sync, process_metadata, websocket
from tests import plex
from tests.test_sync_hangs import pms, run_sync, synced_movies  # noqa: F401
from tests.test_websocket import messages  # noqa: F401 (fixture)


def can_write(path, timeout=0.5):
    kodi = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    try:
        kodi.execute('BEGIN IMMEDIATE')
        kodi.execute('COMMIT')
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        kodi.close()


@pytest.mark.parametrize('batch', [True, False])
def test_a_401_ends_the_sync(pkc, dbs, monkeypatch, settings, batch):
    settings['syncThreadNumber'] = '2'
    pms = plex.FakePMS([plex.movie(i) for i in range(1, 201)]).install(monkeypatch)
    get_metadata = pms.get_metadata
    monkeypatch.setattr(full_sync.PF, 'plex_metadata',
                        lambda ids: 401 if batch else {})
    monkeypatch.setattr(full_sync.PF, 'GetPlexMetadata',
                        lambda key, reraise=False:
                        401 if int(key) >= 5 else get_metadata(key))
    assert not run_sync(pms, plex.movie_section()).successful


def test_kodi_can_write_after_a_commit_while_the_sync_waits(pkc, dbs, monkeypatch,
                                                           settings):
    pms = plex.FakePMS([plex.movie(i) for i in range(1, 6)]).install(monkeypatch)
    pms.batch_fails = True
    release = threading.Event()
    get_metadata = pms.get_metadata

    def slow(key, reraise=False):
        if int(key) == 4:
            release.wait(10)
        return get_metadata(key)
    monkeypatch.setattr(full_sync.PF, 'GetPlexMetadata', slow)
    # Every item commits on the interval
    monkeypatch.setattr(process_metadata, 'COMMIT_INTERVAL', -1)
    monkeypatch.setattr(process_metadata, 'YIELD_TO_KODI', 0)
    settings['syncThreadNumber'] = '1'
    runner = threading.Thread(target=run_sync, args=(pms, plex.movie_section()))
    runner.start()
    reader = sqlite3.connect(dbs['DB_VIDEO_PATH'])
    deadline = time.time() + 10
    while (time.time() < deadline and
           reader.execute('SELECT COUNT(*) FROM movie').fetchone()[0] < 3):
        time.sleep(0.05)
    time.sleep(0.3)
    writable = can_write(dbs['DB_VIDEO_PATH'])
    release.set()
    runner.join(20)
    assert writable, 'write lock held while waiting for a download'


def test_kodi_gets_a_turn_while_the_sync_writes(pkc, dbs, monkeypatch, settings):
    pms = plex.FakePMS([plex.movie(i) for i in range(1, 301)]).install(monkeypatch)
    add_update = itemtypes.Movie.add_update

    def slow(self, *args, **kwargs):
        time.sleep(0.01)
        return add_update(self, *args, **kwargs)
    monkeypatch.setattr(itemtypes.Movie, 'add_update', slow)
    monkeypatch.setattr(process_metadata, 'COMMIT_INTERVAL', 0.3)
    runner = threading.Thread(target=run_sync, args=(pms, plex.movie_section()))
    runner.start()
    time.sleep(0.5)
    # Like Kodi's busy handler: try, sleep 100 ms, try again
    kodi = sqlite3.connect(dbs['DB_VIDEO_PATH'], timeout=0, isolation_level=None)
    start = time.time()
    while time.time() - start < 3:
        try:
            kodi.execute('BEGIN IMMEDIATE')
            kodi.execute('COMMIT')
            break
        except sqlite3.OperationalError:
            time.sleep(0.1)
    waited = time.time() - start
    still_syncing = runner.is_alive()
    runner.join(20)
    assert still_syncing
    assert waited < 1, 'Kodi waited %.1f s' % waited


def test_a_websocket_item_is_downloaded_before_the_dbs_are_locked(
        pms, messages, dbs, monkeypatch):
    seen = []
    get_metadata = pms.get_metadata

    def probing(key, reraise=False):
        seen.append(can_write(dbs['DB_VIDEO_PATH'], timeout=0.2))
        return get_metadata(key)
    monkeypatch.setattr(websocket.PF, 'GetPlexMetadata', probing)
    pms.missing_in_batches = {3}
    messages([7])
    messages([1, 2, 3])
    assert seen == [True, True]


def test_a_message_of_a_kind_pkc_does_not_sync_is_dropped_alone(pms, messages,
                                                                dbs):
    websocket.WEBSOCKET_MESSAGES = [
        {'plex_id': 999, 'plex_type': 'playlist', 'state': 9, 'timestamp': 0,
         'attempt': 0},
        {'plex_id': 7, 'plex_type': 'movie', 'state': 5, 'timestamp': 0,
         'attempt': 0}]
    websocket.process_websocket_messages()
    assert websocket.WEBSOCKET_MESSAGES == []
    assert synced_movies(dbs) == ['Movie 7']


def test_items_of_several_sections_keep_their_own(pkc, monkeypatch):
    from resources.lib import downloadutils
    episode = plex.ET.Element('Video', {'ratingKey': '5', 'type': 'episode',
                                        'librarySectionID': '2',
                                        'librarySectionTitle': 'TV Shows'})
    movie = plex.movie(9)
    movie.set('librarySectionID', '1')
    movie.set('librarySectionTitle', 'Movies')
    bare = plex.movie(11)
    del bare.attrib['librarySectionID']
    # The PMS names the section of the first item only
    answer = plex.container(episode, movie, bare, librarySectionID='2',
                            librarySectionTitle='TV Shows')
    monkeypatch.setattr(downloadutils.DownloadUtils, 'downloadUrl',
                        lambda self, url, **kwargs: answer)
    result = PF.plex_metadata([5, 9, 11])
    assert result[5].get('librarySectionID') == '2'
    assert result[9].get('librarySectionID') == '1'
    assert result[9].get('librarySectionTitle') == 'Movies'
    # Without a section of its own it is asked for alone
    assert 11 not in result


def test_beginning_waits_for_kodi_longer_than_the_busy_timeout(pkc, dbs,
                                                               monkeypatch):
    monkeypatch.setattr(db, 'DB_CONNECTION_TIMEOUT', 0.2)
    kodi = sqlite3.connect(dbs['DB_VIDEO_PATH'], isolation_level=None,
                           check_same_thread=False)
    with itemtypes.Movie(0) as context:
        context.commit(begin=False)
        kodi.execute('BEGIN IMMEDIATE')
        threading.Timer(1.0, lambda: kodi.execute('COMMIT')).start()
        context.begin()
        assert context.kodiconn.in_transaction


def test_a_context_that_cannot_connect_lets_go_of_the_locks(pkc, dbs,
                                                            monkeypatch):
    from resources.lib.plex_db import PLEXDB_LOCK
    from resources.lib.kodi_db import KODIDB_LOCK
    connect = db.connect

    def failing(media_type=None, write=False):
        if media_type == 'video':
            raise exceptions.LockedDatabase('Database is locked')
        return connect(media_type, write)
    monkeypatch.setattr(db, 'connect', failing)
    with pytest.raises(exceptions.LockedDatabase):
        with itemtypes.Movie(0):
            pass
    assert PLEXDB_LOCK.acquire(timeout=1)
    PLEXDB_LOCK.release()
    assert KODIDB_LOCK.acquire(timeout=1)
    KODIDB_LOCK.release()


def test_settings_that_do_not_load_are_tried_again(pkc, settings):
    from resources.lib import sync
    pkc.SYNC.settings_changed = True
    settings['syncThreadNumber'] = ''
    sync.load_changed_settings()
    assert pkc.SYNC.settings_changed
    settings['syncThreadNumber'] = '4'
    sync.load_changed_settings()
    assert not pkc.SYNC.settings_changed
    assert pkc.SYNC.sync_thread_number == 4
