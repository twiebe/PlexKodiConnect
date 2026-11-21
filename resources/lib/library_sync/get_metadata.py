# -*- coding: utf-8 -*-
from logging import getLogger

from . import common
from ..plex_api import API
from .. import backgroundthread, plex_functions as PF, utils, variables as v, app

LOG = getLogger('PLEX.sync.get_metadata')
LOCK = backgroundthread.threading.Lock()


class GetMetadataThread(common.LibrarySyncMixin,
                        backgroundthread.KillableThread):
    """
    Threaded download of Plex XML metadata for a certain library item.
    Fills the queue with the downloaded etree XML objects
    """
    def __init__(self, get_metadata_queue, processing_queue):
        self.get_metadata_queue = get_metadata_queue
        self.processing_queue = processing_queue
        super(GetMetadataThread, self).__init__()

    def _collections(self, item):
        api = API(item['xml'][0])
        collection_match = item['section'].collection_match
        collection_xmls = item['section'].collection_xmls
        if collection_match is None:
            collection_match = PF.collections(api.library_section_id())
            if collection_match is None:
                LOG.error('Could not download collections')
                return
            # Extract what we need to know
            collection_match = \
                [(utils.cast(int, x.get('index')),
                  utils.cast(int, x.get('ratingKey'))) for x in collection_match]
            # Once per section, not for every movie in a collection
            item['section'].collection_match = collection_match
        item['children'] = {}
        for plex_set_id, set_name in api.collections():
            if self.should_cancel():
                return
            if plex_set_id not in collection_xmls:
                # Get Plex metadata for collections - a pain
                for index, collection_plex_id in collection_match:
                    if index == plex_set_id:
                        collection_xml = PF.GetPlexMetadata(collection_plex_id)
                        try:
                            collection_xml[0].attrib
                        except (TypeError, IndexError, AttributeError):
                            LOG.error('Could not get collection %s %s',
                                      collection_plex_id, set_name)
                            continue
                        collection_xmls[plex_set_id] = collection_xml
                        break
                else:
                    LOG.error('Did not find Plex collection %s %s',
                              plex_set_id, set_name)
                    continue
            item['children'][plex_set_id] = collection_xmls[plex_set_id]

    def _process_abort(self, count, section):
        # Make sure other threads will also receive sentinel
        common.queue_put(self, self.get_metadata_queue, None)
        if count is not None:
            self._process_skipped_item(count, section)

    def _process_skipped_item(self, count, section):
        section.sync_successful = False
        # Add a "dummy" item so we're not skipping a beat
        common.queue_put(self, self.processing_queue,
                         (count, {'section': section, 'xml': None}))

    def _run(self):
        while True:
            item = common.queue_get(self, self.get_metadata_queue)
            if item is None:
                # Sentinel or we need to stop
                if not self.should_cancel():
                    self._process_abort(None, None)
                break
            try:
                if self.should_cancel():
                    self._process_skipped_item(item[0], item[2])
                    continue
                if not self._get_item(*item):
                    break
            except Exception:
                LOG.exception('Could not get the metadata of %s', item[1])
                self._process_skipped_item(item[0], item[2])
            finally:
                self.get_metadata_queue.task_done()

    def _get_item(self, count, plex_id, section):
        """
        Downloads the metadata of plex_id and passes it on for processing.
        Returns False if this thread should stop
        """
        item = {
            'xml': PF.GetPlexMetadata(plex_id),  # This will block
            'children': None,
            'section': section
        }
        if item['xml'] is None:
            # Did not receive a valid XML - skip that item for now
            LOG.error("Could not get metadata for %s. Skipping item "
                      "for now", plex_id)
            self._process_skipped_item(count, section)
            return True
        elif item['xml'] == 401:
            LOG.error('HTTP 401 returned by PMS. Too much strain? '
                      'Cancelling sync for now')
            utils.window('plex_scancrashed', value='401')
            self._process_abort(count, section)
            return False
        if section.plex_type == v.PLEX_TYPE_MOVIE:
            # Check for collections/sets
            collections = False
            for child in item['xml'][0]:
                if child.tag == 'Collection':
                    collections = True
                    break
            # The collections' metadata is only used for their artwork
            if collections and app.SYNC.artwork:
                with LOCK:
                    self._collections(item)
        if section.get_children:
            if self.should_cancel():
                self._process_skipped_item(count, section)
                return True
            children_xml = PF.GetAllPlexChildren(plex_id)  # Will block
            try:
                children_xml[0].attrib
            except (TypeError, IndexError, AttributeError):
                LOG.error('Could not get children for Plex id %s',
                          plex_id)
                self._process_skipped_item(count, section)
                return True
            else:
                item['children'] = children_xml
        common.queue_put(self, self.processing_queue, (count, item))
        return True
