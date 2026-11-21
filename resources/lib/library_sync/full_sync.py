#!/usr/bin/env python
# -*- coding: utf-8 -*-
from collections import defaultdict
from logging import getLogger
import queue
import sqlite3
from time import monotonic

import xbmcgui

from .get_metadata import GetMetadataThread
from .fill_metadata_queue import FillMetadataQueue
from .process_metadata import ProcessMetadataThread, COMMIT_INTERVAL, \
    yield_to_kodi
from . import common, sections
from .. import utils, timing, backgroundthread as bg, variables as v, app
from .. import plex_functions as PF, itemtypes, path_ops, db
from ..plex_db import PlexDB

if common.PLAYLIST_SYNC_ENABLED:
    from .. import playlists


LOG = getLogger('PLEX.sync.full_sync')
DELETION_BATCH_SIZE = 250
PLAYSTATE_BATCH_SIZE = 5000

# Max. number of plex_ids held in memory for later processing
BACKLOG_QUEUE_SIZE = 10000
# Max number of xmls held in memory
XML_QUEUE_SIZE = 500
# Safety margin to filter PMS items - how many seconds to look into the past?
UPDATED_AT_SAFETY = 60 * 5
LAST_VIEWED_AT_SAFETY = 60 * 5


class FullSync(common.LibrarySyncMixin, bg.KillableThread):
    def __init__(self, repair, callback, show_dialog):
        """
        repair=True: force sync EVERY item
        """
        self.successful = True
        self.repair = repair
        self.callback = callback
        # For progress dialog
        self.show_dialog = show_dialog
        self.show_dialog_userdata = utils.settings('playstate_sync_indicator') == 'true'
        if self.show_dialog:
            self.dialog = xbmcgui.DialogProgressBG()
            self.dialog.create(utils.lang(39714))
        else:
            self.dialog = None
        self.current_time = timing.plex_now()
        # plex_type: the plex_ids the passes over all items came across
        self.seen = defaultdict(set)
        self.last_section = sections.Section()
        self.install_sync_done = utils.settings('SyncInstallRunDone') == 'true'
        super(FullSync, self).__init__()

    def update_progressbar(self, section, title, current):
        if not self.dialog:
            return
        current += 1
        try:
            progress = int(float(current) / float(section.number_of_items) * 100.0)
        except ZeroDivisionError:
            progress = 0
        self.dialog.update(progress,
                           '%s (%s)' % (section.name, section.section_type_text),
                           '%s %s/%s'
                           % (title, current, section.number_of_items))
        if app.APP.is_playing_video:
            self.dialog.close()
            self.dialog = None

    @staticmethod
    def copy_plex_db():
        """
        Takes the current plex.db file and copies it to plex-copy.db
        This will allow us to have "concurrent" connections during adding/
        updating items, increasing sync speed tremendously.
        Using the same DB with e.g. WAL mode did not really work out...

        Copied with SQLite's backup, not as a file: in WAL mode the latest
        changes may still be in plex.db-wal, and a copy made while SQLite
        writes back to plex.db can be broken. A plex-copy.db-wal left behind
        would be applied to the new copy. The old copy goes first: a backup
        into it would write every page twice, through its WAL
        """
        for suffix in ('', '-wal', '-shm'):
            if path_ops.exists(v.DB_PLEX_COPY_PATH + suffix):
                path_ops.remove(v.DB_PLEX_COPY_PATH + suffix)
        source = sqlite3.connect(v.DB_PLEX_PATH, timeout=db.DB_CONNECTION_TIMEOUT)
        target = sqlite3.connect(v.DB_PLEX_COPY_PATH)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    @utils.log_time
    def process_new_and_changed_items(self, section_queue, processing_queue):
        LOG.debug('Start working')
        get_metadata_queue = queue.Queue(maxsize=BACKLOG_QUEUE_SIZE)
        scanner_thread = FillMetadataQueue(self.repair,
                                           section_queue,
                                           get_metadata_queue,
                                           processing_queue)
        aborted = []

        def stop_feeding():
            scanner_thread.cancel()
            for t in metadata_threads:
                t.cancel()

        def abort():
            # E.g. the PMS answers 401: stop all of it, not only one thread
            aborted.append(True)
            stop_feeding()
            process_thread.cancel()

        metadata_threads = [
            GetMetadataThread(get_metadata_queue, processing_queue,
                              on_abort=abort)
            for _ in range(int(utils.settings('syncThreadNumber')))
        ]
        process_thread = ProcessMetadataThread(self.current_time,
                                               processing_queue,
                                               self.update_progressbar,
                                               on_failure=stop_feeding)
        scanner_thread.start()
        for t in metadata_threads:
            t.start()
        process_thread.start()
        LOG.debug('Waiting for scanner thread to finish up')
        scanner_thread.join()
        LOG.debug('Waiting for metadata download threads to finish up')
        for t in metadata_threads:
            t.join()
        LOG.debug('Download metadata threads finished')
        process_thread.join()
        self.successful = (process_thread.successful and
                           scanner_thread.successful and not aborted)
        LOG.debug('threads finished work. successful: %s', self.successful)

    @utils.log_time
    def processing_loop_playstates(self, section_queue):
        while not self.should_cancel():
            section = section_queue.get()
            section_queue.task_done()
            if section is None:
                break
            self.playstate_per_section(section)

    def playstate_per_section(self, section):
        LOG.debug('Processing %s playstates for library section %s',
                  section.number_of_items, section)
        try:
            with section.context(self.current_time) as context:
                last_commit = monotonic()
                for xml in section.iterator:
                    section.count += 1
                    context.begin()
                    if context.outdated(xml, section):
                        LOG.info('Updating item the PMS did not mark as '
                                 'updated: %s %s', xml.get('ratingKey'),
                                 xml.get('title'))
                        self.add_missing_item(context, xml, section)
                    elif not context.update_userdata(xml, section.plex_type):
                        # Somehow did not sync this item yet
                        self.add_missing_item(context, xml, section)
                    # Seen on the PMS: no need to delete it
                    self.seen[section.plex_type].add(int(xml.attrib['ratingKey']))
                    self.update_progressbar(section, '', section.count - 1)
                    if (section.count % PLAYSTATE_BATCH_SIZE == 0 or
                            monotonic() - last_commit > COMMIT_INTERVAL):
                        # While we write, Kodi cannot
                        context.commit(begin=False)
                        yield_to_kodi(self)
                        last_commit = monotonic()
        except RuntimeError:
            LOG.error('Could not entirely process section %s', section)
            self.successful = False

    @staticmethod
    def add_missing_item(context, xml, section):
        """
        Adds an item a playstate pass came across that is not synced yet, with
        all its metadata: the listing's xml lacks streams, cast and
        collections, and the checksum we save means we would not get them
        later.
        """
        plex_id = xml.get('ratingKey')
        LOG.info('Adding item missing in Kodi: %s %s', plex_id, xml.get('title'))
        # Don't keep Kodi from writing while we ask the PMS
        context.commit(begin=False)
        metadata = PF.GetPlexMetadata(plex_id)
        try:
            metadata[0].attrib
        except (TypeError, IndexError, AttributeError):
            LOG.error('Could not get metadata for %s, adding it next time',
                      plex_id)
            return
        children = None
        if section.get_children:
            children = PF.GetAllPlexChildren(plex_id)
            try:
                children[0].attrib
            except (TypeError, IndexError, AttributeError):
                LOG.error('Could not get the children of %s, adding it next '
                          'time', plex_id)
                return
        context.begin()
        context.add_update(metadata[0],
                           section_name=section.name,
                           section_id=section.section_id,
                           children=children)

    def delete_missing_items(self, plex_type, context):
        """
        Removes the items of plex_type that the last pass over all items did
        not come across - after asking the PMS: a listing can miss items, e.g.
        if items are deleted while we page through it. Items of sections we no
        longer sync go without asking.
        Returns False if we could not finish
        """
        with PlexDB(lock=False) as plexdb:
            missing = [(plex_id, section_id) for plex_id, section_id
                       in plexdb.plex_ids_and_sections(plex_type)
                       if plex_id not in self.seen[plex_type]]
        if not missing:
            return True
        to_check = [plex_id for plex_id, section_id in missing
                    if section_id in app.SYNC.section_ids]
        still_there = PF.plex_ids_on_pms(to_check) if to_check else set()
        if still_there is None:
            LOG.error('Could not check whether the PMS still has %s '
                      'items, not deleting them', plex_type)
            return False
        if still_there:
            LOG.warn('The PMS still has these %s items we did not see: %s',
                     plex_type, sorted(still_there))
        to_delete = [plex_id for plex_id, _ in missing
                     if plex_id not in still_there]
        for start in range(0, len(to_delete), DELETION_BATCH_SIZE):
            with context(self.current_time) as ctx:
                for plex_id in to_delete[start:start + DELETION_BATCH_SIZE]:
                    if self.should_cancel():
                        return False
                    ctx.remove(plex_id, plex_type)
        return True

    def threaded_get_generators(self, kinds, section_queue, items):
        """
        Getting iterators is costly, so let's do it in a dedicated thread
        """
        LOG.debug('Start threaded_get_generators')
        try:
            for kind in kinds:
                for section in (x for x in app.SYNC.sections
                                if x.section_type == kind[1]):
                    if self.should_cancel():
                        LOG.debug('Need to exit now')
                        return
                    if not section.sync_to_kodi:
                        LOG.info('User chose to not sync section %s', section)
                        continue
                    section = sections.get_sync_section(section,
                                                        plex_type=kind[0])
                    timestamp = section.last_sync - UPDATED_AT_SAFETY \
                        if section.last_sync else None
                    if items == 'all':
                        updated_at = None
                        last_viewed_at = None
                    elif items == 'watched':
                        if not timestamp:
                            # No need to sync playstate updates since section
                            # has not yet been synched
                            continue
                        else:
                            updated_at = None
                            last_viewed_at = timestamp
                    elif items == 'updated':
                        updated_at = timestamp
                        last_viewed_at = None
                    try:
                        section.iterator = PF.get_section_iterator(
                            section.section_id,
                            plex_type=section.plex_type,
                            updated_at=updated_at,
                            last_viewed_at=last_viewed_at)
                    except RuntimeError:
                        LOG.error('Sync at least partially unsuccessful!')
                        LOG.error('Error getting section iterator %s', section)
                        # Without the section's items we must not delete
                        # what we did not see
                        self.successful = False
                    else:
                        if (items == 'all' and section.plex_type in
                                (v.PLEX_TYPE_MOVIE, v.PLEX_TYPE_SHOW)):
                            # Changes to these do not mark items as updated
                            section.tags = PF.tag_members(section.section_id,
                                                          section.plex_type)
                        section.number_of_items = section.iterator.total
                        if section.number_of_items > 0:
                            section_queue.put(section)
                            LOG.debug('Put section in queue with %s items: %s',
                                      section.number_of_items, section)
        except Exception:
            self.successful = False
            utils.ERROR(notify=True)
        finally:
            # Sentinel for the section queue
            section_queue.put(None)
            LOG.debug('Exiting threaded_get_generators')

    def full_library_sync(self):
        section_queue = queue.Queue()
        processing_queue = bg.ProcessingQueue(maxsize=XML_QUEUE_SIZE)
        kinds = [
            (v.PLEX_TYPE_MOVIE, v.PLEX_TYPE_MOVIE),
            (v.PLEX_TYPE_SHOW, v.PLEX_TYPE_SHOW),
            (v.PLEX_TYPE_SEASON, v.PLEX_TYPE_SHOW),
            (v.PLEX_TYPE_EPISODE, v.PLEX_TYPE_SHOW)
        ]
        if app.SYNC.enable_music:
            kinds.extend([
                (v.PLEX_TYPE_ARTIST, v.PLEX_TYPE_ARTIST),
                (v.PLEX_TYPE_ALBUM, v.PLEX_TYPE_ARTIST),
            ])

        # ADD NEW ITEMS
        # We need to enforce syncing e.g. show before season before episode
        bg.FunctionAsTask(self.threaded_get_generators,
                          None,
                          kinds,
                          section_queue,
                          items='all' if self.repair else 'updated').start()
        # Do the heavy lifting
        self.process_new_and_changed_items(section_queue, processing_queue)
        common.update_kodi_library(video=True, music=True)
        if self.should_cancel() or not self.successful:
            return

        # In order to not delete all your songs again for playstate synch
        if app.SYNC.enable_music:
            kinds.extend([
                (v.PLEX_TYPE_SONG, v.PLEX_TYPE_ARTIST),
            ])

        # Update playstate progress since last sync - especially useful for
        # users of very large libraries since this step is very fast
        # These playstates will be synched twice
        LOG.debug('Start synching playstate for last watched items')
        bg.FunctionAsTask(self.threaded_get_generators,
                          None,
                          kinds,
                          section_queue,
                          items='watched').start()
        self.processing_loop_playstates(section_queue)
        if self.should_cancel() or not self.successful:
            return

        # Sync Plex playlists to Kodi and vice-versa
        if common.PLAYLIST_SYNC_ENABLED:
            LOG.debug('Start playlist sync')
            if self.show_dialog:
                if self.dialog:
                    self.dialog.close()
                self.dialog = xbmcgui.DialogProgressBG()
                # "Synching playlists"
                self.dialog.create(utils.lang(39715))
            if not playlists.full_sync() or self.should_cancel():
                return

        # SYNC PLAYSTATE of ALL items (otherwise we won't pick up on items that
        # were set to unwatched or changed user ratings). Also mark all items on
        # the PMS to be able to delete the ones still in Kodi
        LOG.debug('Start synching playstate and userdata for every item')
        # Make sure we're not showing an item's title in the sync dialog
        if not self.show_dialog_userdata and self.dialog:
            # Close the progress indicator dialog
            self.dialog.close()
            self.dialog = None
        bg.FunctionAsTask(self.threaded_get_generators,
                          None,
                          kinds,
                          section_queue,
                          items='all').start()
        self.processing_loop_playstates(section_queue)
        if self.should_cancel() or not self.successful:
            return

        # Delete movies that are not on Plex anymore
        LOG.debug('Looking for items to delete')
        kinds = [
            (v.PLEX_TYPE_MOVIE, itemtypes.Movie),
            (v.PLEX_TYPE_SHOW, itemtypes.Show),
            (v.PLEX_TYPE_SEASON, itemtypes.Season),
            (v.PLEX_TYPE_EPISODE, itemtypes.Episode)
        ]
        if app.SYNC.enable_music:
            kinds.extend([
                (v.PLEX_TYPE_ARTIST, itemtypes.Artist),
                (v.PLEX_TYPE_ALBUM, itemtypes.Album),
                (v.PLEX_TYPE_SONG, itemtypes.Song)
            ])
        for plex_type, context in kinds:
            # Delete movies that are not on Plex anymore
            if not self.delete_missing_items(plex_type, context):
                self.successful = False
                return
        LOG.debug('Done looking for items to delete')

    @utils.log_time
    def _run(self):
        try:
            # Get latest Plex libraries and build playlist and video node files
            if self.should_cancel() or not sections.sync_from_pms(self):
                return
            self.copy_plex_db()
            self.full_library_sync()
        finally:
            common.update_kodi_library(video=True, music=True)
            if self.dialog:
                self.dialog.close()
            if not self.successful and not self.should_cancel():
                # "ERROR in library sync"
                utils.dialog('notification',
                             heading='{plex}',
                             message=utils.lang(39410),
                             icon='{error}')
            self.callback(self.successful)


def start(show_dialog, repair=False, callback=None):
    # Call run() and NOT start in order to not spawn another thread
    FullSync(repair, callback, show_dialog).run()
