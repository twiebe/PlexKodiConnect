"""
A Plex Media Server in miniature: the XML PKC gets for library sections and
items, and patches that make the sync code ask it instead of a real PMS.
"""
import xml.etree.ElementTree as ET

SECTION_ID = 1
UPDATED_AT = 1700000000


def movie(plex_id, title=None, updated_at=UPDATED_AT, view_count=0,
          view_offset=0, user_rating=None, collections=()):
    """A movie as the PMS describes it in /library/metadata/<plex_id>."""
    attrib = {
        'ratingKey': str(plex_id),
        'key': '/library/metadata/%s' % plex_id,
        'guid': 'plex://movie/%024x' % plex_id,
        'type': 'movie',
        'title': title or 'Movie %s' % plex_id,
        'librarySectionID': str(SECTION_ID),
        'librarySectionTitle': 'Movies',
        'summary': 'Plot of movie %s' % plex_id,
        'year': '2001',
        'duration': '5400000',
        'addedAt': str(UPDATED_AT - 1000),
        'updatedAt': str(updated_at),
        'originallyAvailableAt': '2001-01-01',
        'thumb': '/library/metadata/%s/thumb/%s' % (plex_id, updated_at),
        'art': '/library/metadata/%s/art/%s' % (plex_id, updated_at),
    }
    if view_count:
        attrib['viewCount'] = str(view_count)
        attrib['lastViewedAt'] = str(UPDATED_AT + 10)
    if view_offset:
        attrib['viewOffset'] = str(view_offset)
    if user_rating is not None:
        attrib['userRating'] = str(user_rating)
    video = ET.Element('Video', attrib)
    media = ET.SubElement(video, 'Media', {
        'id': str(plex_id), 'duration': '5400000', 'videoCodec': 'h264',
        'audioCodec': 'aac', 'audioChannels': '2', 'width': '1920',
        'height': '1080', 'container': 'mkv'})
    part = ET.SubElement(media, 'Part', {
        'id': str(plex_id), 'key': '/library/parts/%s/file.mkv' % plex_id,
        'file': '/media/movies/Movie %s (2001)/Movie %s (2001).mkv'
                % (plex_id, plex_id),
        'duration': '5400000', 'container': 'mkv'})
    ET.SubElement(part, 'Stream', {'streamType': '1', 'codec': 'h264',
                                   'width': '1920', 'height': '1080'})
    ET.SubElement(part, 'Stream', {'streamType': '2', 'codec': 'aac',
                                   'channels': '2', 'language': 'English',
                                   'languageCode': 'eng'})
    ET.SubElement(video, 'Genre', {'tag': 'Drama'})
    ET.SubElement(video, 'Director', {'tag': 'Jane Doe'})
    ET.SubElement(video, 'Role', {'tag': 'John Roe', 'role': 'Hero',
                                  'thumb': ''})
    for index, name in collections:
        ET.SubElement(video, 'Collection', {'id': str(index), 'tag': name})
    return video


def container(*children, **attrib):
    xml = ET.Element('MediaContainer', {
        'size': str(len(children)),
        'librarySectionID': str(SECTION_ID),
        'librarySectionTitle': 'Movies'})
    xml.attrib.update({k: str(v) for k, v in attrib.items()})
    xml.extend(children)
    return xml


def listing(*movies):
    """The section listing: what /library/sections/<id>/all says of each."""
    items = []
    for item in movies:
        short = ET.Element(item.tag, item.attrib)
        items.append(short)
    return items


class Listing(object):
    """
    A section listing as PF.get_section_iterator returns it. FAIL_AT: raise
    RuntimeError there, as DownloadGen does once a chunk failed.
    """
    def __init__(self, items, fail_at=None):
        self.items = list(items)
        self.total = len(self.items)
        self.fail_at = fail_at

    def __iter__(self):
        for position, item in enumerate(self.items):
            if position == self.fail_at:
                raise RuntimeError('Could not download everything')
            yield item


class FakePMS(object):
    """
    The movies of one section. Patch it in with install(monkeypatch); set
    fail_listing_at, metadata_errors or broken to make it misbehave.
    """
    def __init__(self, movies):
        self.movies = {int(m.get('ratingKey')): m for m in movies}
        self.fail_listing_at = None
        self.fail_listing = False
        # plex_id: exception to raise when its metadata is asked for
        self.metadata_errors = {}
        self.metadata_requests = []
        self.listing_requests = []

    def install(self, monkeypatch):
        from resources.lib import plex_functions as PF
        monkeypatch.setattr(PF, 'GetPlexMetadata', self.get_metadata)
        monkeypatch.setattr(PF, 'get_section_iterator', self.section_iterator)
        monkeypatch.setattr(PF, 'collections', lambda section_id: container())
        return self

    def get_metadata(self, key, reraise=False):
        plex_id = int(str(key).rsplit('/', 1)[-1])
        self.metadata_requests.append(plex_id)
        if plex_id in self.metadata_errors:
            raise self.metadata_errors[plex_id]
        if plex_id not in self.movies:
            return None
        return container(self.movies[plex_id])

    def section_iterator(self, section_id, plex_type=None, last_viewed_at=None,
                         updated_at=None, args=None):
        self.listing_requests.append((section_id, plex_type))
        if self.fail_listing:
            raise RuntimeError('Error while downloading chunks')
        items = [self.movies[k] for k in sorted(self.movies)]
        return Listing(listing(*items), fail_at=self.fail_listing_at)


def movie_section():
    """The movie section as the library sync gets it."""
    from resources.lib.library_sync import sections
    section = sections.Section()
    section.section_id = SECTION_ID
    section.name = 'Movies'
    section.section_type = 'movie'
    section.sync_to_kodi = True
    section.last_sync = 0
    return sections.get_sync_section(section, 'movie')
