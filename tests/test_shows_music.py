"""
Shows and music go through the same transactions as movies: the full sync and
the websocket add, change and delete them with Kodi's ids taken under the
write lock, and nobody waits for the PMS while Kodi's DB is locked.
"""
import sqlite3
import threading

import pytest

from resources.lib import itemtypes
from resources.lib.kodi_db import common as kodi_db_common
from resources.lib.library_sync import common, full_sync, websocket
from tests import plex

TIMEOUT = 30
SYNCS = []


def shows():
    """Two shows of two seasons of two episodes: show 100 has seasons 101 and
    104, episodes 102, 103, 105, 106; show 200 likewise"""
    items = []
    for show_id in (100, 200):
        show = plex.show(show_id)
        items.append(show)
        for offset, index in ((1, 1), (4, 2)):
            season = plex.season(show_id + offset, show, index=index)
            items.append(season)
            for episode in (1, 2):
                items.append(plex.episode(show_id + offset + episode, season,
                                          index=episode))
    return items


def music():
    """Artist 300 with albums 301 (tracks 302, 303) and 304 (tracks 305,
    306)"""
    artist = plex.artist(300)
    items = [artist]
    for album_id in (301, 304):
        album = plex.album(album_id, artist)
        items.append(album)
        for index in (1, 2):
            items.append(plex.track(album_id + index, album, index=index))
    return items


@pytest.fixture
def pms(pkc, monkeypatch, settings):
    monkeypatch.setattr(common, 'PLAYLIST_SYNC_ENABLED', False)
    pkc.SYNC.enable_music = True
    pkc.SYNC.section_ids = {plex.SHOW_SECTION_ID, plex.MUSIC_SECTION_ID}
    pms = plex.FakePMS(shows() + music())
    return pms.install(monkeypatch)


def full_library_sync(pms, last_sync=0, repair=False):
    from resources.lib import app
    app.SYNC.sections = [
        plex.plex_section(last_sync, plex.SHOW_SECTION_ID, 'TV Shows', 'show'),
        plex.plex_section(last_sync, plex.MUSIC_SECTION_ID, 'Music', 'artist')]
    sync = full_sync.FullSync(repair=repair, callback=None, show_dialog=False)
    SYNCS.append(sync)
    sync.current_time += 60 * len(SYNCS)
    sync.copy_plex_db()
    runner = threading.Thread(target=sync.full_library_sync)
    runner.start()
    runner.join(TIMEOUT)
    assert not runner.is_alive(), 'the sync hangs'
    return sync


def video(dbs, sql, args=()):
    return sqlite3.connect(dbs['DB_VIDEO_PATH']).execute(sql, args).fetchall()


def audio(dbs, sql, args=()):
    return sqlite3.connect(dbs['DB_MUSIC_PATH']).execute(sql, args).fetchall()


def plexdb(dbs, sql):
    return sqlite3.connect(dbs['DB_PLEX_PATH']).execute(sql).fetchall()


def episodes(dbs):
    """(show, season, episode) of each episode in Kodi"""
    return video(dbs, '''
        SELECT tvshow.c00, seasons.season, episode.c00 FROM episode
        JOIN tvshow ON tvshow.idShow = episode.idShow
        JOIN seasons ON seasons.idSeason = episode.idSeason
        ORDER BY tvshow.c00, seasons.season, episode.c00''')


def songs(dbs):
    """(album, song) of each song in Kodi"""
    return audio(dbs, '''
        SELECT album.strAlbum, song.strTitle FROM song
        JOIN album ON album.idAlbum = song.idAlbum
        ORDER BY album.strAlbum, song.strTitle''')


ALL_EPISODES = [(show, season, 'Episode %s' % episode)
                for show in ('Show 100', 'Show 200')
                for season in (1, 2) for episode in (1, 2)]
ALL_SONGS = [('Album 301', 'Track 302'), ('Album 301', 'Track 303'),
             ('Album 304', 'Track 305'), ('Album 304', 'Track 306')]


def test_a_full_sync_adds_shows_seasons_and_episodes(pms, dbs):
    assert full_library_sync(pms).successful
    assert episodes(dbs) == ALL_EPISODES
    assert video(dbs, 'SELECT COUNT(*) FROM seasons') == [(4, )]
    assert plexdb(dbs, 'SELECT plex_id FROM episode ORDER BY plex_id') == \
        [(x, ) for x in (102, 103, 105, 106, 202, 203, 205, 206)]
    # Each episode has its own file, in its season's folder
    assert video(dbs, '''SELECT COUNT(DISTINCT idFile) FROM episode''') == [(8, )]


def test_a_full_sync_adds_artists_albums_and_songs(pms, dbs):
    assert full_library_sync(pms).successful
    assert songs(dbs) == ALL_SONGS
    assert audio(dbs, '''SELECT DISTINCT artist.strArtist FROM song_artist
                         JOIN artist ON artist.idArtist = song_artist.idArtist
                         ''') == [('Artist 300', )]
    assert audio(dbs, '''SELECT strAlbum FROM album_artist
                         JOIN album ON album.idAlbum = album_artist.idAlbum
                         ORDER BY strAlbum''') == [('Album 301', ), ('Album 304', )]
    assert plexdb(dbs, 'SELECT plex_id FROM track ORDER BY plex_id') == \
        [(x, ) for x in (302, 303, 305, 306)]


def test_a_second_sync_adds_what_is_new_and_deletes_what_is_gone(pms, dbs):
    assert full_library_sync(pms).successful
    season = pms.movies[201]
    del pms.movies[203]
    pms.movies[207] = plex.episode(207, season, index=3,
                                   updated_at=plex.UPDATED_AT + 100)
    for plex_id in (304, 305, 306):
        del pms.movies[plex_id]
    assert full_library_sync(pms, last_sync=1).successful
    assert [x for x in episodes(dbs) if x[0] == 'Show 200'] == [
        ('Show 200', 1, 'Episode 1'), ('Show 200', 1, 'Episode 3'),
        ('Show 200', 2, 'Episode 1'), ('Show 200', 2, 'Episode 2')]
    assert songs(dbs) == ALL_SONGS[:2]
    assert audio(dbs, "SELECT COUNT(*) FROM album WHERE strAlbum = 'Album 304'"
                 ) == [(0, )]
    assert plexdb(dbs, 'SELECT plex_id FROM album') == [(301, )]


def test_watched_episodes_and_songs_are_written(pms, dbs):
    assert full_library_sync(pms).successful
    season = pms.movies[101]
    pms.movies[102] = plex.episode(102, season, index=1, view_count=2)
    pms.movies[302] = plex.track(302, pms.movies[301], index=1, view_count=3)
    assert full_library_sync(pms, last_sync=1).successful
    assert video(dbs, '''SELECT files.playCount FROM episode
                         JOIN files ON files.idFile = episode.idFile
                         WHERE episode.c00 = 'Episode 1' AND
                               episode.idShow = (SELECT idShow FROM tvshow
                                                 WHERE c00 = 'Show 100') AND
                               episode.idSeason = (SELECT MIN(idSeason)
                                                   FROM seasons)''') == [(2, )]
    assert audio(dbs, "SELECT iTimesPlayed FROM song WHERE strTitle = 'Track 302'"
                 ) == [(3, )]


@pytest.fixture
def synced(pms, dbs):
    """The PMS's items after a full sync"""
    assert full_library_sync(pms).successful
    return pms


@pytest.fixture
def messages(pms, dbs, pkc, monkeypatch):
    """Process websocket messages [(plex_id, plex_type)] as the Sync thread
    does. Records each PMS request made while Kodi's DB is locked"""
    monkeypatch.setattr(websocket, 'cache_artwork', lambda *args: None)
    monkeypatch.setattr(websocket, 'update_kodi_library', lambda **kwargs: None)
    monkeypatch.setattr(websocket.backgroundthread.BGThreader, 'addTask',
                        lambda task: None)
    pkc.SYNC.backgroundsync_saftymargin = 0
    pms.under_lock = []
    for name in ('get_metadata', 'plex_metadata', 'children'):
        def watched(*args, _call=getattr(pms, name), _name=name):
            if kodi_db_common.KODIDB_LOCK.locked():
                pms.under_lock.append((_name, args))
            return _call(*args)
        setattr(pms, name, watched)
    pms.install(monkeypatch)

    def process(items, state=5):
        websocket.WEBSOCKET_MESSAGES = [
            {'plex_id': plex_id, 'plex_type': plex_type, 'state': state,
             'timestamp': 0, 'attempt': 0} for plex_id, plex_type in items]
        websocket.process_websocket_messages()
        return [m['plex_id'] for m in websocket.WEBSOCKET_MESSAGES]
    yield process
    websocket.WEBSOCKET_MESSAGES = []


def test_a_new_show_whose_episodes_come_first(pms, messages, dbs):
    # The PMS does not promise to tell of the show before its episodes
    assert messages([(102, 'episode'), (103, 'episode'), (101, 'season'),
                     (100, 'show')]) == []
    assert episodes(dbs) == ALL_EPISODES[:2]
    assert pms.under_lock == []


def test_a_new_album_gets_its_tracks_before_kodi_is_locked(pms, messages, dbs):
    assert messages([(300, 'artist'), (301, 'album')]) == []
    assert songs(dbs) == ALL_SONGS[:2]
    assert pms.under_lock == []


def test_a_new_track_of_a_new_album(pms, messages, dbs):
    # The album comes with all its tracks
    assert messages([(302, 'track')]) == []
    assert songs(dbs) == ALL_SONGS[:2]
    assert pms.under_lock == []


def test_a_single_gets_an_album_of_its_own(pms, messages, dbs):
    """A track the PMS knows no album of"""
    single = plex.track(400, None)
    single.set('grandparentRatingKey', '300')
    pms.movies[400] = single
    assert messages([(300, 'artist'), (400, 'track')]) == []
    album_id, = audio(dbs, "SELECT idAlbum FROM song WHERE strTitle = 'Track 400'")[0]
    assert audio(dbs, 'SELECT strReleaseType FROM album WHERE idAlbum = ?',
                 (album_id, )) == [('single', )]


def test_a_deleted_show_takes_its_seasons_and_episodes(synced, messages, dbs):
    assert messages([(100, 'show')], state=9) == []
    assert [x[0] for x in episodes(dbs)] == ['Show 200'] * 4
    assert video(dbs, 'SELECT COUNT(*) FROM seasons') == [(2, )]
    assert plexdb(dbs, 'SELECT COUNT(*) FROM season') == [(2, )]


def test_a_deleted_album_takes_its_songs(synced, messages, dbs):
    assert messages([(304, 'album')], state=9) == []
    assert songs(dbs) == ALL_SONGS[:2]
    assert plexdb(dbs, 'SELECT plex_id FROM track ORDER BY plex_id') == \
        [(302, ), (303, )]


def test_one_context_per_kind_for_shows(pms, messages, monkeypatch):
    entered = []
    for kind in (itemtypes.Show, itemtypes.Season, itemtypes.Episode):
        def counting(self, _enter=kind.__enter__, _kind=kind.__name__):
            entered.append(_kind)
            return _enter(self)
        monkeypatch.setattr(kind, '__enter__', counting)
    messages([(100, 'show'), (101, 'season'), (102, 'episode'),
              (103, 'episode'), (104, 'season'), (105, 'episode')])
    assert sorted(entered) == ['Episode', 'Season', 'Show']
