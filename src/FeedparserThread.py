import pytz
import time
from datetime import datetime, timedelta
import collections
import threading
import feedparser
import logging
import os
from urllib.parse import quote


Post = collections.namedtuple('Post', [
    'time',
    'blog',
    'title',
    'author',
    'link',
    'body'
])


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
            max_age_hours = float(os.getenv("MAX_POST_AGE_HOURS", "") or 0)
        except ValueError:
            logging.warning("Invalid MAX_POST_AGE_HOURS; ignoring")
            max_age_hours = 0
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

    title = entry.get('title', "Null")

    try:
        author = entry['author']
    except KeyError:
        try:
            author = ', '.join(a['name'] for a in entry.get('authors', []))
        except KeyError:
            author = 'Anonymous'

    link = entry['link']

    try:
        body = entry['content'][0]['value']
    except KeyError:
        body = entry.get('summary', '')

    if not body:
        return

    return Post(when, blog, title, author, link, body)
