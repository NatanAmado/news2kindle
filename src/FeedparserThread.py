import pytz
import time
from datetime import datetime, timedelta
import collections
import threading
import feedparser
import logging
import os
from urllib.parse import quote
import re
try:
    from bs4 import BeautifulSoup
except ImportError:  # Optional dependency; fall back to raw HTML.
    BeautifulSoup = None


Post = collections.namedtuple('Post', [
    'time',
    'blog',
    'title',
    'author',
    'link',
    'body'
])

_INVALID_XML_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def strip_invalid_xml_chars(text):
    if not text:
        return ''
    return _INVALID_XML_RE.sub('', text)


def sanitize_body(body):
    body = strip_invalid_xml_chars(body)
    if not body:
        return ''
    sanitize_html = os.getenv("SANITIZE_HTML", "1").strip().lower() in ("1", "true", "yes", "y")
    if not sanitize_html or BeautifulSoup is None:
        return body
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "iframe", "svg", "video", "audio", "form"]):
        tag.decompose()
    strip_images = os.getenv("STRIP_IMAGES", "1").strip().lower() in ("1", "true", "yes", "y")
    if strip_images:
        for tag in soup.find_all("img"):
            tag.decompose()
    text_only = os.getenv("BODY_TEXT_ONLY", "").strip().lower() in ("1", "true", "yes", "y")
    if text_only:
        text = soup.get_text("\n")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "<p>" + "</p>\n<p>".join(lines) + "</p>" if lines else ''
    max_chars_raw = os.getenv("MAX_BODY_CHARS", "").strip()
    if soup.body:
        cleaned = soup.body.decode_contents()
    else:
        cleaned = soup.decode_contents()
    if max_chars_raw:
        try:
            max_chars = int(max_chars_raw)
        except ValueError:
            logging.warning("Invalid MAX_BODY_CHARS; ignoring")
            return cleaned
        if max_chars > 0:
            return cleaned[:max_chars]
    return cleaned


class FeedparserThread(threading.Thread):
    """
    Each one of these threads will get the task of opening one feed
    and process its entries.

    Given an url, starting time and a global list, this thread will
    add new posts to the global list, after processing.

    """

    def __init__(self, url, START, posts):
        threading.Thread.__init__(self)
        self.url = url
        self.START = START
        self.posts = posts
        self.myposts = []

    def run(self):
        feed_url = self.url
        if os.getenv("FULLTEXT_MORSS", "").strip().lower() in ("1", "true", "yes", "y"):
            morss_url = os.getenv("MORSS_URL", "https://morss.it").rstrip("/")
            morss_mode = os.getenv("MORSS_MODE", "clip").strip() or "clip"
            feed_url = f"{morss_url}/:{morss_mode}/{quote(self.url, safe=':/')}"
        try:
            min_items = int(os.getenv("MIN_ITEMS_PER_FEED", "0") or 0)
        except ValueError:
            logging.warning("Invalid MIN_ITEMS_PER_FEED; using 0")
            min_items = 0
        try:
            max_age_hours = float(os.getenv("MAX_POST_AGE_HOURS", "24") or 24)
        except ValueError:
            logging.warning("Invalid MAX_POST_AGE_HOURS; defaulting to 24")
            max_age_hours = 24
        if max_age_hours <= 0 or max_age_hours > 24:
            max_age_hours = 24
        max_age_cutoff = None
        if max_age_hours > 0:
            max_age_cutoff = pytz.utc.localize(datetime.utcnow()) - timedelta(hours=max_age_hours)
        skip_bozo = os.getenv("SKIP_BOZO", "1").strip().lower() in ("1", "true", "yes", "y")
        try:
            feed = feedparser.parse(
                feed_url,
                agent="news2kindle/1.0 (+https://github.com/)",
            )
        except Exception as exc:
            logging.warning("Feed fetch failed: %s (%s)", self.url, exc)
            return

        if getattr(feed, "bozo", False):
            exc = getattr(feed, "bozo_exception", None)
            if exc:
                logging.warning("Feed parse warning: %s (%s)", self.url, exc)
            if skip_bozo:
                return
        try:
            blog = feed['feed']['title']
        except KeyError:
            blog = "---"
        blog = strip_invalid_xml_chars(blog)
        all_posts = []
        for entry in feed['entries']:
            post = process_entry(entry, blog, None)
            if post:
                all_posts.append(post)

        all_posts.sort(key=lambda post: post.time, reverse=True)
        if max_age_cutoff:
            all_posts = [post for post in all_posts if post.time >= max_age_cutoff]
        recent_posts = [post for post in all_posts if post.time >= self.START]
        if min_items > 0 and len(recent_posts) < min_items:
            self.myposts = all_posts[:min_items]
        else:
            self.myposts = recent_posts
        self.myposts.sort()
        self.posts += self.myposts


def process_entry(entry, blog, START):
    """
    Coerces an entry from feedparser into a Post tuple.
    Returns None if the entry should be excluded.

    If it was published before START date, drop the entry.
    """
    try:
        when = entry['updated_parsed']
    except KeyError:
        try:
            when = entry['published_parsed']
        except KeyError:
            return  # Ignore undateable posts

    if when:
        when = pytz.utc.localize(datetime.fromtimestamp(time.mktime(when)))
    else:
        # print blog, entry
        return

    if START and when < START:
        return

    title = strip_invalid_xml_chars(entry.get('title', "Null"))

    try:
        author = entry['author']
    except KeyError:
        try:
            author = ', '.join(a['name'] for a in entry.get('authors', []))
        except KeyError:
            author = 'Anonymous'
    author = strip_invalid_xml_chars(author)

    link = strip_invalid_xml_chars(entry['link'])

    try:
        body = entry['content'][0]['value']
    except KeyError:
        body = entry.get('summary', '')

    body = sanitize_body(body)
    if not body:
        return

    return Post(when, blog, title, author, link, body)
