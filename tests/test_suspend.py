import threading
import time

from resources.lib import backgroundthread
from resources.lib.library_sync import full_sync
from tests import plex


class SyncThread(backgroundthread.KillableThread):
    """Runs a full sync like the Sync thread, then suspends when asked"""
    def __init__(self, pms):
        self.pms = pms
        self.sync = None
        super(SyncThread, self).__init__()

    def run(self):
        from resources.lib import app
        app.APP.register_thread(self)
        try:
            self.sync = full_sync.FullSync(repair=False, callback=None,
                                           show_dialog=False)
            app.APP.register_thread(self.sync)
            try:
                self.sync.copy_plex_db()
                section = plex.movie_section()
                section.iterator = self.pms.section_iterator(section.section_id)
                section.number_of_items = section.iterator.total
                sections = full_sync.queue.Queue()
                sections.put(section)
                sections.put(None)
                self.sync.process_new_and_changed_items(
                    sections, full_sync.bg.ProcessingQueue(
                        maxsize=full_sync.XML_QUEUE_SIZE))
            finally:
                app.APP.deregister_thread(self.sync)
            while not self.should_cancel():
                if self.should_suspend() and self.wait_while_suspended():
                    break
                self.sleep(0.1)
        finally:
            app.APP.deregister_thread(self)


def test_suspending_stops_a_running_sync_quickly(pkc, dbs, monkeypatch, settings):
    pms = plex.FakePMS([plex.movie(i) for i in range(1, 201)]).install(monkeypatch)
    pms.batch_fails = True
    get_metadata = pms.get_metadata

    def slow(key, reraise=False):
        time.sleep(0.2)
        return get_metadata(key)
    monkeypatch.setattr(full_sync.PF, 'GetPlexMetadata', slow)
    settings['syncThreadNumber'] = '2'
    thread = SyncThread(pms)
    thread.start()
    time.sleep(1.5)
    started = time.time()
    done = threading.Thread(target=pkc.APP.suspend_threads)
    done.start()
    done.join(15)
    took = time.time() - started
    try:
        assert not done.is_alive(), 'suspend_threads blocks'
        assert took < 3, took
        assert not thread.sync.successful
    finally:
        pkc.APP.stop_pkc = True
        thread.resume()
        thread.join(5)
