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


SHOW_SECTION_ID = 2
MUSIC_SECTION_ID = 3


def _item(tag, section_id, section_title, **attrib):
    attrib.setdefault('guid', 'tv.plex.agents.none://%s' % attrib['ratingKey'])
    attrib.update({'librarySectionID': str(section_id),
                   'librarySectionTitle': section_title,
                   'librarySectionKey': '/library/sections/%s' % section_id,
                   'summary': '',
                   'addedAt': str(UPDATED_AT - 1000)})
    attrib.setdefault('updatedAt', str(UPDATED_AT))
    return ET.Element(tag, {k: str(v) for k, v in attrib.items()})


def _media(element, plex_id, path, duration, video=True):
    media = ET.SubElement(element, 'Media', {
        'id': str(plex_id), 'duration': str(duration),
        'container': path.rsplit('.', 1)[1], 'audioChannels': '1',
        'audioCodec': 'aac' if video else 'mp3'})
    if video:
        media.attrib.update({'videoCodec': 'h264', 'width': '128',
                             'height': '72'})
    part = ET.SubElement(media, 'Part', {
        'id': str(plex_id), 'duration': str(duration), 'file': path,
        'key': '/library/parts/%s/file.%s' % (plex_id, path.rsplit('.', 1)[1]),
        'container': media.get('container')})
    if video:
        ET.SubElement(part, 'Stream', {'streamType': '1', 'codec': 'h264',
                                       'width': '128', 'height': '72'})
    ET.SubElement(part, 'Stream', {'streamType': '2', 'codec': 'aac',
                                   'channels': '1', 'language': 'English',
                                   'languageCode': 'eng'})


def show(plex_id, title=None, updated_at=UPDATED_AT):
    """A show as the kodi-dev Plex describes it"""
    title = title or 'Show %s' % plex_id
    xml = _item('Directory', SHOW_SECTION_ID, 'TV Shows',
                ratingKey=plex_id, key='/library/metadata/%s/children' % plex_id,
                type='show', title=title, index=1, updatedAt=updated_at,
                thumb='/library/metadata/%s/thumb/%s' % (plex_id, updated_at))
    ET.SubElement(xml, 'Location', {'path': '/media/shows/%s' % title})
    return xml


def season(plex_id, show_xml, index=1, updated_at=UPDATED_AT):
    return _item('Directory', SHOW_SECTION_ID, 'TV Shows',
                 ratingKey=plex_id, key='/library/metadata/%s/children' % plex_id,
                 parentRatingKey=show_xml.get('ratingKey'), type='season',
                 title='Season %s' % index, parentTitle=show_xml.get('title'),
                 parentKey='/library/metadata/%s' % show_xml.get('ratingKey'),
                 index=index, parentIndex=1, updatedAt=updated_at)


def episode(plex_id, season_xml, index=1, updated_at=UPDATED_AT,
            view_count=0):
    show_title = season_xml.get('parentTitle')
    attrib = dict(
        ratingKey=plex_id, key='/library/metadata/%s' % plex_id,
        parentRatingKey=season_xml.get('ratingKey'),
        grandparentRatingKey=season_xml.get('parentRatingKey'),
        parentKey='/library/metadata/%s' % season_xml.get('ratingKey'),
        grandparentKey='/library/metadata/%s' % season_xml.get('parentRatingKey'),
        type='episode', title='Episode %s' % index,
        grandparentTitle=show_title, parentTitle=season_xml.get('title'),
        index=index, parentIndex=season_xml.get('index'), duration=2128,
        updatedAt=updated_at,
        thumb='/library/metadata/%s/thumb/%s' % (plex_id, updated_at))
    if view_count:
        attrib.update(viewCount=view_count, lastViewedAt=UPDATED_AT + 10)
    xml = _item('Video', SHOW_SECTION_ID, 'TV Shows', **attrib)
    _media(xml, plex_id, '/media/shows/%s/Season %02d/%s - S%02dE%02d.mkv'
           % (show_title, int(season_xml.get('index')), show_title,
              int(season_xml.get('index')), index), 2128)
    return xml


def artist(plex_id, title=None, updated_at=UPDATED_AT):
    title = title or 'Artist %s' % plex_id
    xml = _item('Directory', MUSIC_SECTION_ID, 'Music',
                ratingKey=plex_id, key='/library/metadata/%s/children' % plex_id,
                type='artist', title=title, index=1, updatedAt=updated_at)
    ET.SubElement(xml, 'Genre', {'tag': 'Rock'})
    return xml


def album(plex_id, artist_xml, title=None, updated_at=UPDATED_AT):
    xml = _item('Directory', MUSIC_SECTION_ID, 'Music',
                ratingKey=plex_id, key='/library/metadata/%s/children' % plex_id,
                parentRatingKey=artist_xml.get('ratingKey'), type='album',
                title=title or 'Album %s' % plex_id,
                parentKey='/library/metadata/%s' % artist_xml.get('ratingKey'),
                parentTitle=artist_xml.get('title'), index=1, year=2021,
                originallyAvailableAt='2021-01-01', updatedAt=updated_at)
    ET.SubElement(xml, 'Genre', {'tag': 'Rock'})
    return xml


def track(plex_id, album_xml, index=1, updated_at=UPDATED_AT, view_count=0):
    """A track of ALBUM_XML, or a single without an album if it is None"""
    attrib = dict(
        ratingKey=plex_id, key='/library/metadata/%s' % plex_id,
        type='track', title='Track %s' % plex_id, index=index, parentIndex=1,
        duration=40072, updatedAt=updated_at)
    if album_xml is not None:
        attrib.update(
            parentRatingKey=album_xml.get('ratingKey'),
            grandparentRatingKey=album_xml.get('parentRatingKey'),
            parentKey='/library/metadata/%s' % album_xml.get('ratingKey'),
            grandparentKey='/library/metadata/%s' % album_xml.get('parentRatingKey'),
            parentTitle=album_xml.get('title'),
            grandparentTitle=album_xml.get('parentTitle'))
    if view_count:
        attrib.update(viewCount=view_count, lastViewedAt=UPDATED_AT + 10)
    xml = _item('Track', MUSIC_SECTION_ID, 'Music', **attrib)
    _media(xml, plex_id, '/media/music/%s/%02d - Track %s.mp3'
           % (attrib.get('parentTitle', 'Singles'), index, plex_id), 40072,
           video=False)
    ET.SubElement(xml, 'Genre', {'tag': 'Rock'})
    return xml


def container(*children, **attrib):
    first = children[0] if children else None
    xml = ET.Element('MediaContainer', {
        'size': str(len(children)),
        'librarySectionID': first.get('librarySectionID', str(SECTION_ID))
        if first is not None else str(SECTION_ID),
        'librarySectionTitle': first.get('librarySectionTitle', 'Movies')
        if first is not None else 'Movies'})
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
    The items of the sections, movies in the first. Patch it in with install(monkeypatch); set
    fail_listing_at, metadata_errors or broken to make it misbehave.
    """
    def __init__(self, movies):
        self.movies = {int(m.get('ratingKey')): m for m in movies}
        self.fail_listing_at = None
        self.fail_listing = False
        # plex_id: exception to raise when its metadata is asked for
        self.metadata_errors = {}
        self.metadata_requests = []
        # plex_ids of each request for several items at once
        self.batch_requests = []
        self.batch_fails = False
        # plex_ids a batch answer leaves out
        self.missing_in_batches = set()
        self.listing_requests = []
        self.children_requests = []
        # number of the listing request (1, 2, ...): what to do with it
        self.fail_listings = set()
        self.hide_in_listings = {}
        self.existence_check_fails = False

    def install(self, monkeypatch):
        from resources.lib import plex_functions as PF
        monkeypatch.setattr(PF, 'GetPlexMetadata', self.get_metadata)
        monkeypatch.setattr(PF, 'plex_metadata', self.plex_metadata,
                            raising=False)
        monkeypatch.setattr(PF, 'get_section_iterator', self.section_iterator)
        monkeypatch.setattr(PF, 'GetAllPlexChildren', self.children)
        monkeypatch.setattr(PF, 'collections', lambda section_id: container())
        monkeypatch.setattr(PF, 'plex_ids_on_pms', self.plex_ids_on_pms,
                            raising=False)
        return self

    def plex_ids_on_pms(self, plex_ids):
        if self.existence_check_fails:
            return None
        return {x for x in plex_ids if x in self.movies}

    def get_metadata(self, key, reraise=False):
        plex_id = int(str(key).rsplit('/', 1)[-1])
        self.metadata_requests.append(plex_id)
        if plex_id in self.metadata_errors:
            raise self.metadata_errors[plex_id]
        if plex_id not in self.movies:
            return None
        return container(self.movies[plex_id])

    def children(self, key):
        self.children_requests.append(int(key))
        return container(*[self.movies[k] for k in sorted(self.movies)
                           if self.movies[k].get('parentRatingKey') == str(key)])

    def plex_metadata(self, plex_ids):
        self.batch_requests.append(list(plex_ids))
        if self.batch_fails:
            return {}
        return {x: container(self.movies[x]) for x in plex_ids
                if x in self.movies and x not in self.metadata_errors and
                x not in self.missing_in_batches}

    def section_iterator(self, section_id, plex_type=None, last_viewed_at=None,
                         updated_at=None, args=None):
        self.listing_requests.append((section_id, plex_type))
        number = len(self.listing_requests)
        if self.fail_listing or number in self.fail_listings:
            raise RuntimeError('Error while downloading chunks')
        hidden = self.hide_in_listings.get(number, ())
        items = [self.movies[k] for k in sorted(self.movies)
                 if k not in hidden and
                 self.movies[k].get('type') == (plex_type or 'movie') and
                 self.movies[k].get('librarySectionID') == str(section_id)]
        # The filters the PMS applies
        if updated_at:
            items = [x for x in items
                     if int(x.get('updatedAt', 0)) >= updated_at]
        if last_viewed_at:
            # Items never watched have no lastViewedAt
            items = [x for x in items if x.get('lastViewedAt') and
                     int(x.get('lastViewedAt')) >= last_viewed_at]
        return Listing(listing(*items), fail_at=self.fail_listing_at)


def plex_section(last_sync=0, section_id=SECTION_ID, name='Movies',
                 section_type='movie'):
    """A section (the movies by default) as PKC knows it from the PMS."""
    from resources.lib.library_sync import sections
    section = sections.Section()
    section.section_id = section_id
    section.name = name
    section.section_type = section_type
    section.sync_to_kodi = True
    section.last_sync = last_sync
    return section


def movie_section():
    """The movie section as the library sync gets it."""
    from resources.lib.library_sync import sections
    return sections.get_sync_section(plex_section(), 'movie')
