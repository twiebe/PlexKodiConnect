import sqlite3

import pytest

from resources.lib.library_sync import websocket
from resources.lib import itemtypes
from tests import plex
from tests.test_sync_hangs import pms, synced_movies  # noqa: F401 (fixture)


@pytest.fixture
def messages(pms, dbs, pkc, monkeypatch):
    """Process websocket messages about the movies PLEX_IDS (state 5, new or
    changed, or 9, deleted) as the Sync thread does"""
    monkeypatch.setattr(websocket, 'cache_artwork', lambda *args: None)
    monkeypatch.setattr(websocket, 'update_kodi_library', lambda **kwargs: None)
    monkeypatch.setattr(websocket.backgroundthread.BGThreader, 'addTask',
                        lambda task: None)
    pkc.SYNC.backgroundsync_saftymargin = 0

    def process(plex_ids, state=5):
        websocket.WEBSOCKET_MESSAGES = [
            {'plex_id': i, 'plex_type': 'movie', 'state': state,
             'timestamp': 0, 'attempt': 0} for i in plex_ids]
        websocket.process_websocket_messages()
        return [m['plex_id'] for m in websocket.WEBSOCKET_MESSAGES]
    yield process
    websocket.WEBSOCKET_MESSAGES = []


def test_new_items_are_asked_for_20_at_a_time(pms, messages, dbs):
    assert messages(range(1, 26)) == []
    assert [len(x) for x in pms.batch_requests] == [20, 5]
    assert pms.metadata_requests == []
    assert len(synced_movies(dbs)) == 25


def test_one_db_context_for_all_messages_of_a_kind(pms, messages, monkeypatch):
    entered = []
    enter = itemtypes.Movie.__enter__

    def counting(self):
        entered.append(1)
        return enter(self)
    monkeypatch.setattr(itemtypes.Movie, '__enter__', counting)
    messages(range(1, 11))
    assert len(entered) == 1


def test_a_failing_item_is_rolled_back_alone(pms, messages, dbs, monkeypatch):
    add_update = itemtypes.Movie.add_update

    def half(self, xml, *args, **kwargs):
        add_update(self, xml, *args, **kwargs)
        if xml.get('ratingKey') == '3':
            raise ValueError('after the writes')
    monkeypatch.setattr(itemtypes.Movie, 'add_update', half)
    assert messages([1, 2, 3, 4]) == [3]
    assert websocket.WEBSOCKET_MESSAGES[0]['attempt'] == 1
    assert synced_movies(dbs) == ['Movie 1', 'Movie 2', 'Movie 4']
    con = sqlite3.connect(dbs['DB_PLEX_PATH'])
    assert con.execute('SELECT plex_id FROM movie ORDER BY plex_id').fetchall() \
        == [(1, ), (2, ), (4, )]


def test_an_item_missing_from_the_answer_is_asked_alone(pms, messages, dbs):
    pms.missing_in_batches = {2}
    assert messages([1, 2, 3]) == []
    assert pms.metadata_requests == [2]
    assert len(synced_movies(dbs)) == 3


def test_deletions_share_one_context(pms, messages, dbs):
    messages(range(1, 6))
    assert messages([2, 4], state=9) == []
    assert synced_movies(dbs) == ['Movie 1', 'Movie 3', 'Movie 5']
