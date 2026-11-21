import sqlite3

from tests import plex
from tests.test_sync_hangs import pms, run_sync  # noqa: F401 (fixture)


def test_the_collections_of_a_section_are_downloaded_once(pms, dbs, monkeypatch):
    from resources.lib import plex_functions as PF
    asked = []
    collections = plex.container(plex.ET.Element(
        'Directory', {'index': '9', 'ratingKey': '900', 'title': 'Saga'}))
    monkeypatch.setattr(PF, 'collections',
                        lambda section_id: asked.append(section_id) or collections)
    for plex_id in range(1, 11):
        pms.movies[plex_id] = plex.movie(plex_id, collections=[(9, 'Saga')])
    assert run_sync(pms, plex.movie_section()) is not None
    assert len(asked) == 1


def test_an_episode_index_on_the_season_table_is_moved(dbs):
    from resources.lib import plex_db
    con = sqlite3.connect(dbs['DB_PLEX_PATH'])
    con.execute('DROP INDEX ix_episode_3')
    con.execute('CREATE INDEX ix_episode_3 ON season (plex_guid)')
    con.commit()
    plex_db.initialize()
    assert con.execute("SELECT tbl_name FROM sqlite_master WHERE name = "
                       "'ix_episode_3'").fetchone() == ('episode', )
