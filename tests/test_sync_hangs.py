"""
The threads of a full sync must end, whatever fails: a thread that waits
for good holds PKC's DB locks and an open Kodi transaction.
"""
import sqlite3
import threading

import pytest

from resources.lib.library_sync import full_sync, websocket
from resources.lib import itemtypes
from tests import plex

TIMEOUT = 15


def run_sync(pms, section):
    """Runs the part of a full sync that adds new and changed items; returns
    the FullSync, or fails if it does not end."""
    sync = full_sync.FullSync(repair=False, callback=None, show_dialog=False)
    sync.copy_plex_db()
    section.iterator = pms.section_iterator(section.section_id)
    section.number_of_items = section.iterator.total
    section_queue = full_sync.queue.Queue()
    section_queue.put(section)
    section_queue.put(None)
    processing_queue = full_sync.bg.ProcessingQueue(
        maxsize=full_sync.XML_QUEUE_SIZE)
    runner = threading.Thread(target=sync.process_new_and_changed_items,
                              args=(section_queue, processing_queue))
    runner.start()
    runner.join(TIMEOUT)
    assert not runner.is_alive(), 'the sync hangs'
    return sync


def synced_movies(dbs):
    con = sqlite3.connect(dbs['DB_VIDEO_PATH'])
    return sorted(r[0] for r in con.execute('SELECT c00 FROM movie'))


@pytest.fixture
def pms(pkc, monkeypatch):
    return plex.FakePMS([plex.movie(i) for i in range(1, 31)]).install(monkeypatch)


def test_all_items_are_synced(pms, dbs):
    sync = run_sync(pms, plex.movie_section())
    assert sync.successful
    assert len(synced_movies(dbs)) == 30


def test_a_listing_that_fails_halfway_ends_the_sync(pms, dbs):
    pms.fail_listing_at = 12
    section = plex.movie_section()
    sync = run_sync(pms, section)
    assert not sync.successful
    assert not section.sync_successful
    # What came before is kept
    assert len(synced_movies(dbs)) == 12


def test_an_error_getting_one_item_skips_it(pms, dbs):
    pms.metadata_errors[7] = KeyError('ratingKey')
    sync = run_sync(pms, plex.movie_section())
    assert not sync.successful
    assert 'Movie 7' not in synced_movies(dbs)
    assert len(synced_movies(dbs)) == 29


def test_an_error_writing_an_item_ends_the_sync(pms, dbs, monkeypatch):
    add_update = itemtypes.Movie.add_update

    def failing(self, xml, *args, **kwargs):
        if xml.get('ratingKey') == '5':
            raise ValueError('broken item')
        return add_update(self, xml, *args, **kwargs)

    monkeypatch.setattr(itemtypes.Movie, 'add_update', failing)
    sync = run_sync(pms, plex.movie_section())
    assert not sync.successful
    # The open batch is rolled back, nothing half written stays
    assert synced_movies(dbs) == []


def test_an_error_writing_more_items_than_the_queue_holds(pms, dbs, monkeypatch):
    # The download threads must not wait for good on a full queue
    monkeypatch.setattr(full_sync, 'XML_QUEUE_SIZE', 3)
    monkeypatch.setattr(itemtypes.Movie, 'add_update',
                        lambda *args, **kwargs: 1 / 0)
    sync = run_sync(pms, plex.movie_section())
    assert not sync.successful


def test_one_bad_websocket_message_does_not_stop_the_others(pkc, monkeypatch):
    done = []

    def process(message):
        if message['plex_id'] == 1:
            raise ValueError('broken')
        done.append(message['plex_id'])
        return True, True, False

    monkeypatch.setattr(websocket, 'process_new_item_message', process)
    monkeypatch.setattr(websocket, 'cache_artwork', lambda *args: None)
    monkeypatch.setattr(websocket, 'update_kodi_library', lambda **kwargs: None)
    monkeypatch.setattr(websocket.backgroundthread.BGThreader, 'addTask',
                        lambda task: None)
    pkc.SYNC.backgroundsync_saftymargin = 0
    websocket.WEBSOCKET_MESSAGES = [
        {'plex_id': i, 'plex_type': 'movie', 'state': 5, 'timestamp': 0,
         'attempt': 0} for i in (1, 2)]
    websocket.process_websocket_messages()
    assert done == [2]
    assert [m['plex_id'] for m in websocket.WEBSOCKET_MESSAGES] == [1]
    assert websocket.WEBSOCKET_MESSAGES[0]['attempt'] == 1

