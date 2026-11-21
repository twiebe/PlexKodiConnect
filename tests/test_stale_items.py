"""
Changes the PMS does not mark as an update - a renamed file, a new collection
or label - and items Kodi lost reach Kodi with the next full sync.
"""
import sqlite3

import pytest

from resources.lib.itemtypes import common as itemtypes_common
from resources.lib.library_sync import common
from tests import plex
from tests.test_sync_deletion import full_library_sync


@pytest.fixture
def pms(pkc, monkeypatch):
    monkeypatch.setattr(common, 'PLAYLIST_SYNC_ENABLED', False)
    pms = plex.FakePMS([
        plex.movie(1),
        plex.movie(2, collections=[(1, 'Saga')], labels=['4K']),
        plex.movie(3)])
    pms.install(monkeypatch)
    assert full_library_sync(pms).successful
    pms.metadata_requests.clear()
    pms.batch_requests.clear()
    pms.tag_requests.clear()
    return pms


@pytest.fixture
def direct_paths(pkc, monkeypatch):
    monkeypatch.setattr(pkc.SYNC, 'direct_paths', True)


def video(dbs, sql, args=()):
    return sqlite3.connect(dbs['DB_VIDEO_PATH']).execute(sql, args).fetchall()


def tags(dbs, title):
    return sorted(r[0] for r in video(dbs, '''
        SELECT tag.name FROM tag_link JOIN tag ON tag.tag_id = tag_link.tag_id
        JOIN movie ON movie.idMovie = tag_link.media_id
        WHERE tag_link.media_type = 'movie' AND movie.c00 = ?''', (title, )))


def set_of(dbs, title):
    return video(dbs, '''SELECT sets.strSet FROM movie
                         LEFT JOIN sets ON sets.idSet = movie.idSet
                         WHERE c00 = ?''', (title, ))


def refetched(pms):
    return sorted(pms.metadata_requests +
                  [x for batch in pms.batch_requests for x in batch])


def test_nothing_changed_nothing_asked_for(pms):
    assert full_library_sync(pms, last_sync=1).successful
    assert refetched(pms) == []
    assert pms.tag_requests == [plex.SECTION_ID]


def test_a_new_collection_and_label(pms, dbs):
    # The PMS keeps updatedAt as it is
    pms.movies[1] = plex.movie(1, collections=[(1, 'Saga')], labels=['Kids'])
    assert full_library_sync(pms, last_sync=1).successful
    assert refetched(pms) == [1]
    assert tags(dbs, 'Movie 1') == ['Kids', 'Movies', 'Saga']
    assert set_of(dbs, 'Movie 1') == [('Saga', )]


def test_a_collection_and_label_removed(pms, dbs):
    pms.movies[2] = plex.movie(2)
    assert full_library_sync(pms, last_sync=1).successful
    assert refetched(pms) == [2]
    assert tags(dbs, 'Movie 2') == ['Movies']
    assert set_of(dbs, 'Movie 2') == [(None, )]


def test_unknown_tags_change_nothing(pms):
    pms.tags_fail = True
    pms.movies[1] = plex.movie(1, labels=['Kids'])
    assert full_library_sync(pms, last_sync=1).successful
    assert refetched(pms) == []


def test_a_renamed_file_with_direct_paths(direct_paths, pms, dbs):
    pms.movies[3] = plex.movie(3, filename='Movie 3 (2001) 1080p.mkv')
    assert full_library_sync(pms, last_sync=1).successful
    assert refetched(pms) == [3]
    assert video(dbs, '''SELECT files.strFilename FROM movie
                         JOIN files ON files.idFile = movie.idFile
                         WHERE c00 = 'Movie 3' ''') == [('Movie 3 (2001) 1080p.mkv', )]


def test_add_on_paths_need_no_new_file_name(pms, dbs):
    # Kodi plays the item through the PMS, which knows the new file
    pms.movies[3] = plex.movie(3, filename='Movie 3 (2001) 1080p.mkv')
    assert full_library_sync(pms, last_sync=1).successful
    assert refetched(pms) == []


def test_a_movie_kodi_lost_comes_back(pms, dbs):
    """E.g. deleted when Kodi could not find its file and asked"""
    con = sqlite3.connect(dbs['DB_VIDEO_PATH'])
    con.execute("DELETE FROM movie WHERE c00 = 'Movie 1'")
    con.commit()
    assert full_library_sync(pms, last_sync=1).successful
    assert refetched(pms) == [1]
    assert video(dbs, "SELECT COUNT(*) FROM movie WHERE c00 = 'Movie 1'") == [(1, )]
    # And stays: the next sync has nothing to do
    pms.metadata_requests.clear()
    assert full_library_sync(pms, last_sync=2).successful
    assert refetched(pms) == []


@pytest.mark.parametrize('file, filename, expected', [
    ('/media/a/Movie.mkv', 'Movie.mkv', False),
    ('/media/a/Movie 1080p.mkv', 'Movie.mkv', True),
    ('\\\\nas\\a\\Movie 1080p.mkv', 'Movie 1080p.mkv', False),
    ('/media/a/Movie.mkv', 'stack:///a/Movie cd1.mkv , /a/Movie cd2.mkv', False),
    ('/media/a/Movie.mkv',
     'plugin://plugin.video.plexkodiconnect.movies/?plex_id=1', False),
    (None, 'Movie.mkv', False),
])
def test_renamed(file, filename, expected):
    xml = plex.movie(1)
    part = xml.find('Media/Part')
    if file is None:
        xml.remove(xml.find('Media'))
    else:
        part.set('file', file)
    assert itemtypes_common.renamed(xml, filename) is expected


def test_tag_members_leave_out_smart_collections(monkeypatch):
    from resources.lib import plex_functions as PF
    collections = plex.container(
        plex.ET.Element('Directory', {'ratingKey': '10', 'title': 'Saga'}),
        plex.ET.Element('Directory', {'ratingKey': '11', 'title': 'Recent',
                                      'smart': '1'}))
    members = {'{server}/library/collections/10/children':
               plex.container(plex.movie(1), plex.movie(2))}
    labels = plex.container(
        plex.ET.Element('Directory', {'key': '7', 'title': 'Kids'}))

    def chunks(url):
        if url in members:
            return members[url]
        assert 'label=7' in url and 'type=1' in url
        return plex.container(plex.movie(2))

    class DU(object):
        def downloadUrl(self, url, parameters=None):
            assert url == '{server}/library/sections/1/label'
            return labels
    monkeypatch.setattr(PF, 'collections', lambda section_id: collections)
    monkeypatch.setattr(PF, 'DownloadChunks', chunks)
    monkeypatch.setattr(PF, 'DU', DU)
    assert PF.tag_members(1, 'movie') == {1: {'Saga'}, 2: {'Saga', 'Kids'}}


def test_tag_members_none_if_a_collection_fails(monkeypatch):
    from resources.lib import plex_functions as PF
    monkeypatch.setattr(PF, 'collections', lambda section_id: plex.container(
        plex.ET.Element('Directory', {'ratingKey': '10', 'title': 'Saga'})))
    monkeypatch.setattr(PF, 'DownloadChunks', lambda url: None)
    assert PF.tag_members(1, 'movie') is None


def test_a_new_label_on_a_show(pkc, dbs, monkeypatch):
    from tests import test_shows_music as shows
    monkeypatch.setattr(common, 'PLAYLIST_SYNC_ENABLED', False)
    pkc.SYNC.section_ids = {plex.SHOW_SECTION_ID}
    pms = plex.FakePMS(shows.shows()).install(monkeypatch)
    assert shows.full_library_sync(pms).successful
    pms.metadata_requests.clear()
    pms.batch_requests.clear()
    pms.movies[100] = plex.show(100, labels=['Kids'])
    assert shows.full_library_sync(pms, last_sync=1).successful
    assert refetched(pms) == [100]
    assert sorted(r[0] for r in video(dbs, '''
        SELECT tag.name FROM tag_link JOIN tag ON tag.tag_id = tag_link.tag_id
        WHERE tag_link.media_type = 'tvshow' ''')) == ['Kids', 'TV Shows', 'TV Shows']
