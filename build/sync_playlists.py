"""
Find A Course: daily playlist tag sync (public RSS feeds only).

No API key, no login, no access to any account. It reads YouTube's public
playlist RSS feeds (https://www.youtube.com/feeds/videos.xml?playlist_id=...),
the same feed any podcast or RSS reader can open.

A feed only lists the first 15 videos of a playlist, so this script keeps a
running snapshot of every video ever seen in each playlist:
  - data/playlist-snapshot.json starts with each playlist's full public list
    (taken 2026-11-12) and grows as new videos appear at the top of a feed.
  - Playlists must be set to "Add new videos to top of playlist" in YouTube
    Studio, or new additions won't show up in the feed.
  - Removing a video from a playlist is NOT detected. To drop a tag, remove
    the video ID from the snapshot file (or ask for a full refresh).

Inputs:  data/courses-base.json, data/playlists.json, data/playlist-snapshot.json
Output:  data/courses.json (what the website loads), updated snapshot

Run:  python3 build/sync_playlists.py
"""
import json, os, re, sys, urllib.request, urllib.error
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), 'data')
FEED = 'https://www.youtube.com/feeds/videos.xml?playlist_id='
NS = {'a': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}


def load(name):
    with open(os.path.join(DATA, name)) as f:
        return json.load(f)


def save(name, obj, pretty=False):
    path = os.path.join(DATA, name)
    with open(path + '.tmp', 'w') as f:
        json.dump(obj, f, ensure_ascii=False, **({'indent': 1} if pretty else {'separators': (',', ':')}))
    os.replace(path + '.tmp', path)


def playlist_id(url):
    m = re.search(r'[?&]list=([\w-]+)', url)
    return m.group(1) if m else url.strip()


def feed_video_ids(pid):
    req = urllib.request.Request(FEED + pid, headers={'User-Agent': 'FindACourseSync/1.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        root = ET.fromstring(r.read())
    return [e.findtext('yt:videoId', namespaces=NS) for e in root.findall('a:entry', NS)]


def main():
    courses = load('courses-base.json')
    playlists = load('playlists.json')['playlists']
    snap = load('playlist-snapshot.json')

    video_to_courses = {}
    for c in courses:
        for g in c['groups']:
            for v in g['videos']:
                video_to_courses.setdefault(v['id'], []).append(c['slug'])

    ptags, problems = {}, []
    print(f"{'Tag':28} {'Known':>6} {'New today':>10} {'Courses':>8} {'Not on map':>11}")
    for p in playlists:
        pid = playlist_id(p['url'])
        known = snap.setdefault(pid, [])
        try:
            new = [v for v in feed_video_ids(pid) if v and v not in known]
        except (urllib.error.URLError, ET.ParseError) as e:
            problems.append(f"{p['tag']}: could not read feed ({e})")
            new = []
        known.extend(new)
        hit, missing = set(), 0
        for vid in known:
            slugs = video_to_courses.get(vid)
            if not slugs:
                missing += 1
                continue
            for s in slugs:
                hit.add(s)
                tags = ptags.setdefault(s, [])
                if p['tag'] not in tags:
                    tags.append(p['tag'])
        print(f"{p['tag']:28} {len(known):6} {len(new):10} {len(hit):8} {missing:11}")

    for c in courses:
        c['ptags'] = ptags.get(c['slug'], [])
        c['tags'] = list(dict.fromkeys(c.get('tags', []) + c['ptags']))

    save('playlist-snapshot.json', snap, pretty=True)
    save('courses.json', courses)
    print(f"Updated courses.json: {sum(1 for c in courses if c['ptags'])} courses have playlist tags")
    for msg in problems:
        print('WARNING:', msg)
    if problems:
        sys.exit(1)


if __name__ == '__main__':
    main()
