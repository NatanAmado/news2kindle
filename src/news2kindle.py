#!/usr/bin/env python
# encoding: utf-8

# idea and original code from from from https://gist.github.com/alexwlchan/01cec115a6f51d35ab26

# PYTHON boilerplate
from email.utils import COMMASPACE, formatdate
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import smtplib
import sys
import pypandoc
import pytz
import time
import logging
import threading
from datetime import datetime, timedelta
import os
from FeedparserThread import FeedparserThread

logging.basicConfig(level=logging.INFO)

EMAIL_SMTP = os.getenv("EMAIL_SMTP")
EMAIL_SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASSWD = os.getenv("EMAIL_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM")
KINDLE_EMAIL = os.getenv("KINDLE_EMAIL")
PANDOC = os.getenv("PANDOC_PATH", "/usr/bin/pandoc")
PERIOD = int(os.getenv("UPDATE_PERIOD", 12))  # hours between RSS pulls

CONFIG_PATH = os.path.expanduser(os.getenv("CONFIG_PATH", "/config"))
FEED_FILE = os.path.join(CONFIG_PATH, 'feeds.txt')
COVER_FILE = os.path.join(CONFIG_PATH, 'cover.png')


feed_file = os.path.expanduser(FEED_FILE)


def load_feeds():
    """Return a list of the feeds for download.
        At the moment, it reads it from `feed_file`.
    """
    with open(feed_file, 'r') as f:
        # Strip whitespace and drop blank/comment lines to avoid bad URLs.
        return [line.strip() for line in f if line.strip() and not line.lstrip().startswith('#')]


def update_start(now):
    """
    Update the timestamp of the feed file. The time stamp is used
    as the starting point to download articles.
    """
    new_now = time.mktime(now.timetuple())
    with open(feed_file, 'a'):
        os.utime(feed_file, (new_now, new_now))


def get_start(fname):
    """
    Get the starting time to read posts since. This is currently saved as 
    the timestamp of the feeds file.
    """
    return pytz.utc.localize(datetime.fromtimestamp(os.path.getmtime(fname)))


def get_posts_list(feed_list, START):
    """
    Spawn a worker thread for each feed.
    """
    posts = []
    ths = []
    for url in feed_list:
        th = FeedparserThread(url, START, posts)
        ths.append(th)
        th.start()

    for th in ths:
        th.join()

    # When all is said and done,
    return posts


def nicedate(dt):
    return dt.strftime('%d %B %Y').strip('0')


def nicehour(dt):
    return dt.strftime('%I:%M&thinsp;%p').strip('0').lower()


def nicepost(post):
    thispost = post._asdict()
    thispost['nicedate'] = nicedate(thispost['time'])
    thispost['nicetime'] = nicehour(thispost['time'])
    return thispost


# <link rel="stylesheet" type="text/css" href="style.css">
html_head = u"""<html>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width" />
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">

  <meta name="apple-mobile-web-app-capable" content="yes" />
<style>
</style>
<title>THE DAILY NEWS</title>
</head>
<body>

"""

html_tail = u"""
</body>
</html>
"""

html_perpost = u"""
    <article>
        <h1><a href="{link}">{title}</a></h1>
        <p><small>By {author} for <i>{blog}</i>, on {nicedate} at {nicetime}.</small></p>
         {body}
    </article>
"""


def send_mail(send_from, send_to, subject, text, files):
    # assert isinstance(send_to, list)

    msg = MIMEMultipart()
    msg['From'] = send_from
    msg['To'] = COMMASPACE.join(send_to)
    msg['Date'] = formatdate(localtime=True)
    msg['Subject'] = subject
    msg.attach(MIMEText(text, 'text', 'utf-8'))

    for f in files or []:
        with open(f, "rb") as fil:
            msg.attach(MIMEApplication(
                fil.read(),
                Content_Disposition=f'attachment; filename="{os.path.basename(f)}"',
                Name=os.path.basename(f)
            ))
    if EMAIL_SMTP_PORT == 465:
        smtp = smtplib.SMTP_SSL(EMAIL_SMTP, EMAIL_SMTP_PORT)
    else:
        smtp = smtplib.SMTP(EMAIL_SMTP, EMAIL_SMTP_PORT)
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
    smtp.login(EMAIL_USER, EMAIL_PASSWD)
    smtp.sendmail(send_from, send_to, msg.as_string())
    smtp.quit()


def do_one_round():
    # get all posts from starting point to now
    now = pytz.utc.localize(datetime.now())
    lookback_hours = os.getenv("LOOKBACK_HOURS")
    if lookback_hours:
        try:
            start = now - timedelta(hours=int(lookback_hours))
        except ValueError:
            logging.warning("Invalid LOOKBACK_HOURS=%s; using feed timestamp", lookback_hours)
            start = get_start(feed_file)
    else:
        start = get_start(feed_file)

    logging.info(f"Collecting posts since {start}")

    posts = get_posts_list(load_feeds(), start)
    posts.sort()

    logging.info(f"Downloaded {len(posts)} posts")

    if posts:
        logging.info("Compiling newspaper")

        result = html_head + \
            u"\n".join([html_perpost.format(**nicepost(post))
                        for post in posts]) + html_tail

        logging.info("Creating epub")

        epub_title = os.getenv("EPUB_TITLE", "Daily News")
        epub_lang = os.getenv("EPUB_LANG", "en")
        epubFile = 'dailynews.epub'

        os.environ['PYPANDOC_PANDOC'] = PANDOC
        pypandoc.convert_text(result,
                              to='epub3',
                              format="html",
                              outputfile=epubFile,
                              extra_args=["--standalone",
                                          f"--epub-cover-image={COVER_FILE}",
                                          f"--metadata=title={epub_title}",
                                          f"--metadata=lang={epub_lang}",
                                          ])
        logging.info("Sending to kindle email")
        send_mail(send_from=EMAIL_FROM,
                  send_to=[KINDLE_EMAIL],
                  subject="Daily News",
                  text="This is your daily news.\n\n--\n\n",
                  files=[epubFile])
        keep_output = os.getenv("KEEP_OUTPUT", "").strip().lower() in ("1", "true", "yes", "y")
        if keep_output:
            logging.info("Keeping output file %s", epubFile)
        else:
            logging.info("Cleaning up...")
            os.remove(epubFile)

    logging.info("Finished.")
    try:
        update_start(now)
    except OSError as exc:
        logging.warning("Could not update feed timestamp: %s", exc)


if __name__ == '__main__':
    run_once = os.getenv("RUN_ONCE", "").strip().lower() in ("1", "true", "yes", "y")
    while True:
        do_one_round()
        if run_once:
            break
        time.sleep(PERIOD*60)
