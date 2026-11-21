#!/usr/bin/env python
# -*- coding: utf-8 -*-
from logging import getLogger
import queue

import xbmc

from .. import utils, app, variables as v

LOG = getLogger('PLEX.sync')

PLAYLIST_SYNC_ENABLED = (v.DEVICE != 'Microsoft UWP' and
                         utils.settings('enablePlaylistSync') == 'true')


class LibrarySyncMixin(object):
    def suspend(self, block=False, timeout=None):
        """
        Let's NOT suspend sync threads but immediately terminate them
        """
        self.cancel()

    def wait_while_suspended(self):
        """
        Return immediately
        """
        return self.should_cancel()

    def run(self):
        app.APP.register_thread(self)
        LOG.debug('##===--- Starting %s ---===##', self.__class__.__name__)
        try:
            self._run()
        except Exception as err:
            LOG.error('Exception encountered: %s', err)
            utils.ERROR(notify=True)
        finally:
            app.APP.deregister_thread(self)
            LOG.debug('##===--- %s Stopped ---===##', self.__class__.__name__)


# How long a sync thread waits on a queue before it checks whether to stop
QUEUE_POLL = 0.5


def queue_get(thread, source):
    """
    The next item of SOURCE, or None once THREAD should stop: a sync thread
    must not wait for good on a thread that has died or stopped.
    """
    while not thread.should_cancel():
        try:
            return source.get(timeout=QUEUE_POLL)
        except queue.Empty:
            pass
    return None


def queue_put(thread, target, item):
    """
    Put ITEM in TARGET. Returns False if THREAD should stop before there is
    room for it.
    """
    while not thread.should_cancel():
        try:
            target.put(item, timeout=QUEUE_POLL)
            return True
        except queue.Full:
            pass
    return False


def update_kodi_library(video=True, music=True):
    """
    Updates the Kodi library and thus refreshes the Kodi views and widgets
    """
    if video:
        if not xbmc.getCondVisibility('Window.IsMedia'):
            xbmc.executebuiltin('UpdateLibrary(video)')
        else:
            # Prevent cursor from moving - refresh later
            xbmc.executebuiltin('Container.Refresh')
            app.APP.update_widgets = True
    if music:
        xbmc.executebuiltin('UpdateLibrary(music)')


def tag_last(iterable):
    """
    Given some iterable, returns (last, item), where last is only True if you
    are on the final iteration.
    """
    iterator = iter(iterable)
    gotone = False
    try:
        lookback = next(iterator)
        gotone = True
        while True:
            cur = next(iterator)
            yield False, lookback
            lookback = cur
    except StopIteration:
        if gotone:
            yield True, lookback
        raise StopIteration()
