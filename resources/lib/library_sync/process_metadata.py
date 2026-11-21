# -*- coding: utf-8 -*-
from logging import getLogger
import queue
from time import monotonic

from . import common, sections
from ..plex_db import PlexDB
from .. import backgroundthread, app

LOG = getLogger('PLEX.sync.process_metadata')

COMMIT_TO_DB_EVERY_X_ITEMS = 500
# While we write, Kodi cannot: commit at least this often [seconds]
COMMIT_INTERVAL = 1.0
# How long we leave the DBs to Kodi after a commit. Kodi's busy handler
# retries every 100 ms: if we begin again at once, it never gets a turn
YIELD_TO_KODI = 0.15



def yield_to_kodi(thread):
    """Wait so Kodi gets the write lock if it waits for it"""
    app.APP.monitor.waitForAbort(YIELD_TO_KODI)


class ProcessMetadataThread(common.LibrarySyncMixin,
                            backgroundthread.KillableThread):
    """
    Invoke once in order to process the received PMS metadata xmls
    """
    def __init__(self, current_time, processing_queue, update_progressbar,
                 on_failure=None):
        self.current_time = current_time
        # Called if this thread fails, to stop the threads that feed it
        self.on_failure = on_failure
        self.processing_queue = processing_queue
        self.update_progressbar = update_progressbar
        self.last_section = sections.Section()
        self.successful = True
        super(ProcessMetadataThread, self).__init__()

    def start_section(self, section):
        if section != self.last_section:
            if self.last_section:
                self.finish_last_section()
            LOG.debug('Start or continue processing section %s', section)
            self.last_section = section
            # Warn the user for this new section if we cannot access a file
            app.SYNC.path_verified = False
        else:
            LOG.debug('Resume processing section %s', section)

    def finish_last_section(self):
        if (not self.should_cancel() and self.last_section and
                self.last_section.sync_successful):
            # Check for should_cancel() because we cannot be sure that we
            # processed every item of the section
            with PlexDB() as plexdb:
                # Set the new time mark for the next delta sync
                plexdb.update_section_last_sync(self.last_section.section_id,
                                                self.current_time)
            LOG.info('Finished processing section successfully: %s',
                     self.last_section)
        elif self.last_section and not self.last_section.sync_successful:
            LOG.warn('Sync not successful for section %s', self.last_section)
            self.successful = False

    def _get(self, before_waiting=None):
        """
        The next item with an xml, or None for the sentinel or if we need to
        stop. Calls before_waiting() before it waits for one.
        """
        while True:
            try:
                item = self.processing_queue.get(block=False)
            except queue.Empty:
                if before_waiting:
                    before_waiting()
                item = common.queue_get(self, self.processing_queue)
            if item is None:
                return None
            self.processing_queue.task_done()
            if item['xml'] is not None:
                return item
            # A skipped item: the section is not synced completely
            item['section'].sync_successful = False

    def _run(self):
        try:
            self._process()
        except Exception:
            LOG.exception('Could not process the metadata, stopping the sync')
            self.successful = False
            if self.last_section:
                self.last_section.sync_successful = False
            if self.on_failure:
                self.on_failure()
        else:
            self.finish_last_section()

    def _process(self):
        # There are 2 sentinels: None for aborting/ending this thread, the dict
        # {'section': section, 'xml': None} for skipped/invalid items
        item = self._get()
        if item:
            section = item['section']
            processed = 0
            self.start_section(section)
        while not self.should_cancel():
            if item is None:
                break
            elif item['section'] != section:
                # We received an entirely new section
                self.start_section(item['section'])
                section = item['section']
            with section.context(self.current_time) as context:
                last_commit = monotonic()

                def commit():
                    # Let Kodi write, the next item begins anew
                    nonlocal processed, last_commit
                    context.commit(begin=False)
                    processed = 0
                    yield_to_kodi(self)
                    last_commit = monotonic()

                def release():
                    # Don't keep the DBs locked while we wait for downloads
                    nonlocal processed
                    context.commit(begin=False)
                    processed = 0

                while not self.should_cancel():
                    if item is None or item['section'] != section:
                        break
                    self.update_progressbar(section,
                                            item['xml'][0].get('title'),
                                            section.count)
                    context.begin()
                    context.add_update(item['xml'][0],
                                       section_name=section.name,
                                       section_id=section.section_id,
                                       children=item['children'])
                    processed += 1
                    section.count += 1
                    if (processed == COMMIT_TO_DB_EVERY_X_ITEMS or
                            monotonic() - last_commit > COMMIT_INTERVAL):
                        commit()
                    item = self._get(before_waiting=release)
