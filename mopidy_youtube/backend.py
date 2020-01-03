# -*- coding: utf-8 -*-

import re
import string
import unicodedata
from multiprocessing.pool import ThreadPool
from urllib.parse import parse_qs, urlparse

from mopidy import backend
from mopidy.models import Album, SearchResult, Track

import pafy

import pykka

import requests

from mopidy_youtube import logger

yt_api_endpoint = 'https://www.googleapis.com/youtube/v3/'
# yt_key = 'AIzaSyAl1Xq9DwdE_KD4AtPaE4EJl3WZe2zCqg4'
# TODO : Allow to put the key in the config
yt_key = 'AIzaSyADcD6Gvoj76B7X4HlVtIf__VomNWfunoM'
session = requests.Session()

video_uri_prefix = 'youtube:video'
search_uri = 'youtube:search'


def resolve_track(track, stream=False):
    logger.debug("Resolving YouTube for track '%s'", track)
    if hasattr(track, 'uri'):
        return resolve_url(track.comment, stream)
    else:
        return resolve_url(track.split('.')[-1], stream)


def safe_url(uri):
    valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    safe_uri = unicodedata.normalize('NFKD', uri)
    return re.sub(r'\s+', ' ', ''.join(c for c in safe_uri if c in valid_chars))


def resolve_url(url, stream=False):
    try:
        video = pafy.new(url)
        if not stream:
            uri = '%s/%s.%s' % (
                video_uri_prefix, safe_url(video.title), video.videoid)
        else:
            uri = video.getbestaudio()
            if not uri:  # get video url
                uri = video.getbest()
            logger.debug('%s - %s %s %s' % (
                video.title, uri.bitrate, uri.mediatype, uri.extension))
            uri = uri.url
        if not uri:
            logger.info('No uri found returning')
            return
    except Exception as e:
        # Video is private or doesn't exist
        logger.info(str(e))
        return

    try:
        track = Track(
            name=video.title,
            comment=video.videoid,
            length=video.length * 1000,
            album=Album(
                name='YouTube',
            ),
            uri=uri
        )
        return track
    except Exception as e:
        logger.info(e)


def search_youtube(q):
    query = {
        'part': 'id',
        'maxResults': 15,
        'type': 'video',
        'q': q,
        'key': yt_key
    }
    result = session.get(yt_api_endpoint + 'search', params=query)
    data = result.json()

    resolve_pool = ThreadPool(processes=16)
    playlist = [item['id']['videoId'] for item in data['items']]

    playlist = resolve_pool.map(resolve_url, playlist)
    resolve_pool.close()
    logger.info(str(playlist))
    return [item for item in playlist if item]


def resolve_playlist(url):
    resolve_pool = ThreadPool(processes=16)
    logger.info("Resolving YouTube-Playlist '%s'", url)
    playlist = []

    page = 'first'
    while page:
        params = {
            'playlistId': url,
            'maxResults': 50,
            'key': yt_key,
            'part': 'contentDetails'
        }
        if page and page != "first":
            logger.debug("Get YouTube-Playlist '%s' page %s", url, page)
            params['pageToken'] = page

        result = session.get(yt_api_endpoint + 'playlistItems', params=params)
        data = result.json()
        page = data.get('nextPageToken')

        for item in data["items"]:
            video_id = item['contentDetails']['videoId']
            playlist.append(video_id)

    playlist = resolve_pool.map(resolve_url, playlist)
    resolve_pool.close()
    return [item for item in playlist if item]


class YouTubeBackend(pykka.ThreadingActor, backend.Backend):
    def __init__(self, config, audio):
        super(YouTubeBackend, self).__init__()
        self.config = config
        self.library = YouTubeLibraryProvider(backend=self)
        self.playback = YouTubePlaybackProvider(audio=audio, backend=self)

        self.uri_schemes = ['youtube', 'yt']


class YouTubeLibraryProvider(backend.LibraryProvider):
    def lookup(self, track):
        if 'yt:' in track:
            track = track.replace('yt:', '')

        if 'youtube.com' in track:
            url = urlparse(track)
            req = parse_qs(url.query)
            if 'list' in req:
                return resolve_playlist(req.get('list')[0])
            else:
                return [item for item in [resolve_url(track)] if item]
        else:
            return [item for item in [resolve_track(track)] if item]

    def search(self, query=None, uris=None, exact=False):
        # TODO Support exact search

        if not query:
            return

        if 'uri' in query:
            search_query = ''.join(query['uri'])
            url = urlparse(search_query)
            if 'youtube.com' in url.netloc:
                req = parse_qs(url.query)
                if 'list' in req:
                    return SearchResult(
                        uri=search_uri,
                        tracks=resolve_playlist(req.get('list')[0])
                    )
                else:
                    logger.info(
                        "Resolving YouTube for track '%s'", search_query)
                    return SearchResult(
                        uri=search_uri,
                        tracks=[t for t in [resolve_url(search_query)] if t]
                    )
        else:
            search_query = ' '.join(list(query.values())[0])
            logger.info("Searching YouTube for query '%s'", search_query)
            return SearchResult(
                uri=search_uri,
                tracks=search_youtube(search_query)
            )


class YouTubePlaybackProvider(backend.PlaybackProvider):

    def translate_uri(self, uri):
        track = resolve_track(uri, True)
        if track is not None:
            return track.uri
        else:
            return None
