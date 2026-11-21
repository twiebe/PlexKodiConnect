import queue

import pytest

from tests import plex
from tests.test_sync_hangs import pms, run_sync  # noqa: F401 (fixture)


@pytest.fixture
def fetch(pms, pkc):
    """Run one metadata thread over ITEMS (count, plex_id, section) up to the
    sentinel; returns what it passed on, by count"""
    from resources.lib.library_sync.get_metadata import GetMetadataThread

    def run(items):
        source = queue.Queue()
        for item in items:
            source.put(item)
        source.put(None)
        target = queue.Queue()
        GetMetadataThread(source, target)._run()
        result = {}
        while not target.empty():
            count, item = target.get()
            result[count] = item
        return result
    return run


def items(pms, section, plex_ids, start=0):
    for plex_id in plex_ids:
        pms.movies[plex_id] = plex.movie(plex_id)
    return [(start + i, plex_id, section) for i, plex_id in enumerate(plex_ids)]


def test_one_request_for_up_to_20_items(pms, fetch):
    section = plex.movie_section()
    result = fetch(items(pms, section, range(1, 46)))
    assert [len(x) for x in pms.batch_requests] == [20, 20, 5]
    assert pms.metadata_requests == []
    assert sorted(result) == list(range(45))
    assert [result[i]['xml'][0].get('ratingKey') for i in range(45)] == \
        [str(x) for x in range(1, 46)]


def test_an_item_the_answer_lacks_is_asked_alone(pms, fetch):
    section = plex.movie_section()
    pms.missing_in_batches = {3}
    pms.metadata_errors = {4: RuntimeError('boom')}
    result = fetch(items(pms, section, range(1, 6)))
    assert pms.metadata_requests == [3, 4]
    assert result[2]['xml'][0].get('ratingKey') == '3'
    assert result[3]['xml'] is None
    assert not section.sync_successful


def test_a_failed_batch_is_asked_item_by_item(pms, fetch):
    section = plex.movie_section()
    pms.batch_fails = True
    result = fetch(items(pms, section, range(1, 6)))
    assert pms.metadata_requests == [1, 2, 3, 4, 5]
    assert all(result[i]['xml'] is not None for i in range(5))


def test_a_batch_holds_one_section(pms, fetch):
    first, second = plex.movie_section(), plex.movie_section()
    work = items(pms, first, [1, 2, 3]) + items(pms, second, [4, 5], start=3)
    result = fetch(work)
    assert pms.batch_requests == [[1, 2, 3], [4, 5]]
    assert result[0]['section'] is first


def test_a_full_sync_with_batches(pms, dbs):
    import sqlite3
    for plex_id in range(1, 101):
        pms.movies[plex_id] = plex.movie(plex_id)
    assert run_sync(pms, plex.movie_section()) is not None
    con = sqlite3.connect(dbs['DB_VIDEO_PATH'])
    assert con.execute('SELECT COUNT(*) FROM movie').fetchone() == (100, )


def test_plex_metadata_splits_the_answer(pkc, monkeypatch):
    from resources.lib import plex_functions as PF, downloadutils
    answer = plex.container(plex.movie(7), plex.movie(9), librarySectionID='1')
    urls = []

    def download(self, url, **kwargs):
        urls.append(url)
        return answer
    monkeypatch.setattr(downloadutils.DownloadUtils, 'downloadUrl', download)
    result = PF.plex_metadata([7, 8, 9])
    assert '/library/metadata/7,8,9?' in urls[0]
    assert sorted(result) == [7, 9]
    assert len(result[9]) == 1 and result[9][0].get('ratingKey') == '9'
    assert result[9].get('librarySectionID') == '1'
    assert result[9].get('size') == '1'
    monkeypatch.setattr(downloadutils.DownloadUtils, 'downloadUrl',
                        lambda self, url, **kw: 401)
    assert PF.plex_metadata([1, 2]) == 401
    monkeypatch.setattr(downloadutils.DownloadUtils, 'downloadUrl',
                        lambda self, url, **kw: None)
    assert PF.plex_metadata([1, 2]) == {}
