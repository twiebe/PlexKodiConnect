import sqlite3
import threading
import time

import pytest

from resources.lib import db, exceptions
from resources.lib.library_sync import full_sync
from tests import plex
from tests.test_sync_hangs import run_sync


def test_the_plex_db_copy_has_what_is_still_in_the_wal(dbs):
    keep_open = sqlite3.connect(dbs['DB_PLEX_PATH'])
    keep_open.execute('SELECT 1 FROM movie').fetchall()
    writer = db.connect('plex', write=True)
    writer.execute('INSERT INTO movie(plex_id, checksum) VALUES (99, 1)')
    writer.commit()
    writer.close()
    # Left by a sync that was killed
    with open(dbs['DB_PLEX_COPY_PATH'] + '-wal', 'wb') as f:
        f.write(b'\0' * 4096)
    full_sync.FullSync.copy_plex_db()
    keep_open.close()
    copy = sqlite3.connect(dbs['DB_PLEX_COPY_PATH'])
    assert copy.execute('SELECT plex_id FROM movie').fetchall() == [(99,)]


class Writer(object):
    """Something with Kodi DB connections, as KodiDBBase"""
    def __init__(self, kodiconn):
        self.kodiconn = kodiconn
        self.artconn = None
        self.calls = 0

    @db.catch_operationalerrors
    def locked_once(self):
        self.calls += 1
        if self.calls == 1:
            raise sqlite3.OperationalError('database is locked')
        return 'done'


def video_rows(dbs):
    con = sqlite3.connect(dbs['DB_VIDEO_PATH'])
    return con.execute('SELECT strSet FROM sets').fetchall()


def test_a_reader_retries_when_locked(pkc, dbs):
    writer = Writer(db.connect('video'))
    assert writer.locked_once() == 'done'


def test_a_writer_holding_the_lock_does_not_commit_half_its_work(pkc, dbs):
    conn = db.connect('video', write=True)
    conn.execute("INSERT INTO sets(strSet) VALUES ('half')")
    with pytest.raises(exceptions.LockedDatabase):
        Writer(conn).locked_once()
    assert video_rows(dbs) == []


def test_kodi_shutting_down_while_locked_raises(pkc, dbs, monkeypatch):
    monkeypatch.setattr(pkc.APP.monitor, 'waitForAbort', lambda timeout: True,
                        raising=False)
    with pytest.raises(exceptions.LockedDatabase):
        Writer(db.connect('video')).locked_once()


def test_kodi_can_write_while_the_sync_waits_for_downloads(pkc, dbs, monkeypatch,
                                                         settings):
    pms = plex.FakePMS([plex.movie(i) for i in range(1, 6)]).install(monkeypatch)
    # Item by item, so that movie 4 can be slow
    pms.batch_fails = True
    release = threading.Event()
    get_metadata = pms.get_metadata

    def slow(key, reraise=False):
        if int(key) == 4:
            release.wait(10)
        return get_metadata(key)

    monkeypatch.setattr(full_sync.PF, 'GetPlexMetadata', slow)
    monkeypatch.setattr(full_sync, 'XML_QUEUE_SIZE', 500)
    # One download thread, so movie 5 waits for movie 4
    settings['syncThreadNumber'] = '1'
    result = {}
    runner = threading.Thread(
        target=lambda: result.update(sync=run_sync(pms, plex.movie_section())))
    runner.start()
    reader = sqlite3.connect(dbs['DB_VIDEO_PATH'])
    deadline = time.time() + 10
    while time.time() < deadline:
        if reader.execute('SELECT COUNT(*) FROM movie').fetchone()[0] == 3:
            break
        time.sleep(0.05)
    # What came before is committed, and Kodi gets the write lock at once
    assert reader.execute('SELECT COUNT(*) FROM movie').fetchone()[0] == 3
    kodi = sqlite3.connect(dbs['DB_VIDEO_PATH'], timeout=0.5,
                           isolation_level=None)
    kodi.execute('BEGIN IMMEDIATE')
    kodi.execute("INSERT INTO sets(strSet) VALUES ('kodi')")
    kodi.execute('COMMIT')
    release.set()
    runner.join(20)
    assert result['sync'].successful
    assert reader.execute('SELECT COUNT(*) FROM movie').fetchone()[0] == 5


def test_a_new_kodi_id_needs_the_write_lock(pkc, dbs):
    from resources.lib.kodi_db import KodiVideoDB
    reader = db.connect('video')
    with pytest.raises(RuntimeError):
        KodiVideoDB(kodiconn=reader, lock=False).new_movie_id()
    writer = db.connect('video', write=True)
    assert KodiVideoDB(kodiconn=writer, lock=False).new_movie_id() == 1
    writer.commit()
    with pytest.raises(RuntimeError):
        KodiVideoDB(kodiconn=writer, lock=False).new_movie_id()
