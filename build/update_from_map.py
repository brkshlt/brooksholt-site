"""
Find A Course: daily update from the public "Find A Course" Google My Map.

Public data only: the map's public KML export, YouTube's public oEmbed endpoint
(video titles), and YouTube's public playlist RSS feeds. No keys, no logins.

Steps:
  1. Download the map's KML (anyone with the map link can do this).
  2. Turn each pin into a course: name, location, state, videos (date + link),
     grouped by sub-course ("Gold course:", "Diamond course:", ...).
  3. Look up titles for any video not already in data/video-titles.json.
  4. Auto-tag courses from their names and video titles, then apply
     data/tag-overrides.json (hand-made fixes).
  5. Write data/courses-base.json, then run the playlist sync, which writes
     data/courses.json (the file the website loads).
  6. Print a short report of anything in the map that looks like a typo.

Run:  python3 build/update_from_map.py
"""
import datetime, html, json, os, re, sys, urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
DATA = os.path.join(SITE, 'data')
MAP_ID = '1qYIuVoSd9Bs1Y5IK0-3KHZZGOGd6T93h'
KML_URL = f'https://www.google.com/maps/d/kml?forcekml=1&mid={MAP_ID}'
UA = {'User-Agent': 'FindACourseSync/1.0'}
NS = {'k': 'http://www.opengis.net/kml/2.2'}

# Two different courses share a name; give each a town so they stay distinct.
# Keyed by (name, latitude rounded to 2 places).
RENAME = {
    ('Lost Treasure Golf', 36.03): 'Lost Treasure Golf (Kill Devil Hills)',
    ('Lost Treasure Golf', 40.02): 'Lost Treasure Golf (Lancaster)',
    ('Congo Falls Adventure Golf', 39.27): 'Congo Falls Adventure Golf (Ocean City)',
    ('Congo Falls Adventure Golf', 39.32): 'Congo Falls Adventure Golf (Margate)',
    ('PopStroke', 29.22): 'PopStroke (Daytona Beach)',
    ('PopStroke', 33.54): 'PopStroke (Scottsdale)',
}

R = lambda p: re.compile(p, re.I)
# (tag, course-name pattern, video-title pattern, title hits needed)
TAG_RULES = [
    ('Blacklight', R(r'black ?light|glow|neon|monster mini golf|lumin'), R(r'black ?light|glow in the dark|neon'), 1),
    ('Indoor', R(r'indoor|black ?light|glow|monster mini golf|mall'), R(r'indoor|black ?light|glow in the dark'), 1),
    ('Old School', R(r'old school|vintage|retro|classic'), R(r'old school|vintage|retro|classic|oldest|from the (19)?[5-7]0s'), 1),
    ('Homemade', R(r'homemade|backyard'), R(r'homemade|backyard|home ?made|diy'), 1),
    ('High-Tech', R(r'popstroke|puttshack|topgolf|tech|interactive'), R(r'high[- ]tech|tech mini golf|interactive|futuristic'), 1),
    ('Hole-in-One Prize', None, R(r'free game|lifetime pass|wins? (a |an )?(free|prize|lifetime|\$)|\$\d+|prize|win (a |an )?free|for (a|an) free'), 1),
    ('Hardest', None, R(r'hardest|impossible|toughest|most difficult|hard(er)? than|challenging'), 2),
    ('Must Play', None, R(r'must[- ]play|favou?rite|best mini golf course(s)? (in|ever)'), 1),
    ('Pirate', R(r'pirate|smuggler|shipwreck|treasure|jolly roger|buccaneer|captain|blackbeard|privateer'), R(r'\bpirate'), 2),
    ('Dinosaur', R(r'dino|jurassic|raptor|t-?rex|prehistoric'), R(r'dinosaur|jurassic'), 1),
    ('Jungle & Safari', R(r'jungle|safari|congo|gorilla|amazon|tiger'), R(r'jungle|safari'), 2),
    ('Tropical & Tiki', R(r'tiki|hawaii|aloha|tropical|luau|polynesian|lagoon|maui|kahuna|malibu'), R(r'tiki|hawaiian|tropical'), 2),
    ('Volcano', R(r'volcano|lava|molten'), R(r'volcano|lava'), 1),
    ('Waterfalls', R(r'falls|waterfall|cascade'), R(r'waterfall'), 2),
    ('Spooky', R(r'haunt|monster|zombie|spook|ghost|skull|graveyard|cemetery|creepy'), R(r'haunted|spooky|creepy'), 2),
    ('Space', R(r'\b(space|spaceport|ufo|ufos|alien|galaxy|rocket|planet|astro|moon|cosmic)\b'), R(r'\bufo|alien|outer space'), 1),
    ('Disney', R(r'disney|fantasia|winter summerland'), R(r'disney'), 1),
    ('Mega Mini Golf', None, R(r'mega mini golf'), 1),
]

LINE = re.compile(r'(\d{1,2}/\d{1,2}/\d{2,4})?(.*?)(https?://\S+)')
GOOD_DATE = re.compile(r'\d{1,2}/\d{1,2}/20\d\d')
TODAY = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()


def fetch(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def load(name, default=None):
    path = os.path.join(DATA, name)
    if default is not None and not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save(name, obj, pretty=False):
    path = os.path.join(DATA, name)
    with open(path + '.tmp', 'w') as f:
        json.dump(obj, f, ensure_ascii=False, **({'indent': 0} if pretty else {'separators': (',', ':')}))
    os.replace(path + '.tmp', path)


def video_id(url):
    m = re.search(r'(?:yo?u?tu\.?u?be/|[?&]v=|shorts/|live/)([\w-]{11})', url)
    return m.group(1) if m else None


# ---------- state lookup (point in polygon, nearest edge for coastal pins) ----------
with open(os.path.join(HERE, 'us-states.json')) as f:
    STATES = json.load(f)['features']


def _polys(feature):
    g = feature['geometry']
    return [g['coordinates']] if g['type'] == 'Polygon' else g['coordinates']


def _inside(x, y, poly):
    inside = False
    for ring in poly:
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i][:2]
            xj, yj = ring[j][:2]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
                inside = not inside
            j = i
    return inside


def state_of(lng, lat):
    for f in STATES:
        if any(_inside(lng, lat, p) for p in _polys(f)):
            return f['properties']['name']
    best = (9e9, None)
    for f in STATES:
        for poly in _polys(f):
            for ring in poly:
                for q in ring:
                    d = (q[0] - lng) ** 2 + (q[1] - lat) ** 2
                    if d < best[0]:
                        best = (d, f['properties']['name'])
    return best[1] if best[0] < 0.25 else None


# ---------- 1-2. read the map ----------
def read_map(issues):
    root = ET.fromstring(fetch(KML_URL))
    courses, seen = [], set()
    for p in root.iter('{%s}Placemark' % NS['k']):
        name = re.sub(r'\s+', ' ', p.findtext('k:name', default='', namespaces=NS)).strip()
        coords = p.findtext('.//k:coordinates', default='', namespaces=NS).strip()
        if not name or not coords:
            issues.append(f'{name or "(unnamed pin)"}: pin has no name or location')
            continue
        lng, lat = [float(v) for v in coords.split(',')[:2]]
        name = RENAME.get((name, round(lat, 2)), name)

        desc = html.unescape(p.findtext('k:description', default='', namespaces=NS))
        groups, cur = [], {'label': None, 'videos': []}
        for raw in re.split(r'<br\s*/?>|\n', desc):
            line = re.sub(r'<[^>]+>', '', raw).strip()
            if not line:
                continue
            m = LINE.search(line)
            if m:
                date, note, url = m.group(1), m.group(2).strip(' :\t'), m.group(3)
                vid = video_id(url)
                if not vid:
                    issues.append(f'{name}: link is not a YouTube video: {url}')
                    continue
                iso = None
                if date and GOOD_DATE.fullmatch(date):
                    mo, da, yr = date.split('/')
                    iso = f'{yr}-{int(mo):02d}-{int(da):02d}'
                    if iso > TODAY or iso < '2015-01-01':
                        issues.append(f'{name}: date looks wrong ("{date}") for {url}')
                        iso = None
                else:
                    issues.append(f'{name}: date missing or mistyped ("{date or ""}") for {url}')
                cur['videos'].append({'d': iso, 'id': vid, 'note': note or None})
            else:
                if cur['videos'] or cur['label']:
                    groups.append(cur)
                cur = {'label': line.rstrip(': \t'), 'videos': []}
        if cur['videos'] or cur['label']:
            groups.append(cur)
        groups = [g for g in groups if g['videos']]
        for g in groups:  # newest first, whatever order they were typed in
            g['videos'].sort(key=lambda v: v['d'] or '', reverse=True)
        n = sum(len(g['videos']) for g in groups)
        if not n:
            issues.append(f'{name}: no videos listed')

        state = 'At Sea' if re.search(r'royal caribbean|cruise', name, re.I) else state_of(lng, lat)
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower().replace("'", '').replace('’', '')).strip('-')
        base, k = slug, 2
        while slug in seen:
            slug, k = f'{base}-{k}', k + 1
        seen.add(slug)
        dates = [v['d'] for g in groups for v in g['videos'] if v['d']]
        courses.append({'slug': slug, 'last': max(dates) if dates else None, 'n': n, 'name': name,
                        'lat': round(lat, 6), 'lng': round(lng, 6), 'state': state, 'groups': groups})
    return courses


# ---------- 3. titles ----------
def fill_titles(courses, issues):
    titles = load('video-titles.json', {})
    new = [v['id'] for c in courses for g in c['groups'] for v in g['videos'] if v['id'] not in titles]
    for vid in dict.fromkeys(new):
        q = urllib.parse.urlencode({'url': f'https://www.youtube.com/watch?v={vid}', 'format': 'json'})
        try:
            titles[vid] = html.unescape(json.loads(fetch('https://www.youtube.com/oembed?' + q, 20))['title'])
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                issues.append(f'Video {vid} is private, deleted or the link is wrong (https://youtu.be/{vid})')
            # leave it out of the cache so it is retried next time
        except Exception:
            pass
    for c in courses:
        for g in c['groups']:
            for v in g['videos']:
                v['t'] = titles.get(v['id'])
    save('video-titles.json', dict(sorted(titles.items())), pretty=True)
    return len(new)


# ---------- 4. tags ----------
def tag(courses):
    overrides = load('tag-overrides.json', {'courses': {}}).get('courses', {})
    for c in courses:
        titles = [v['t'] for g in c['groups'] for v in g['videos'] if v.get('t')]
        tags = []
        for name, name_pat, title_pat, need in TAG_RULES:
            hits = sum(1 for t in titles if title_pat.search(t)) if title_pat else 0
            if (name_pat and name_pat.search(c['name'])) or hits >= need:
                tags.append(name)
        if re.search(r'closed', c['name'], re.I):
            tags.append('Closed')
        if c['n'] >= 10:
            tags.append('Played 10+ Times')
        o = overrides.get(c['slug'], {})
        tags = [t for t in tags if t not in o.get('remove', [])] + [t for t in o.get('add', []) if t not in tags]
        c['tags'] = tags


def write_stats():
    """Small summary for the home page: totals, newest video, and pin locations for the dot map."""
    courses = [c for c in load('courses.json') if c['n'] > 0]
    newest = max(((v['d'] or '', v['id'], v.get('t'), c['name'], c['slug'])
                  for c in courses for g in c['groups'] for v in g['videos']), default=None)
    save('stats.json', {
        'courses': len(courses),
        'videos': sum(c['n'] for c in courses),
        'states': len({c['state'] for c in courses if c['state'] and c['state'] != 'At Sea'}),
        'latest': newest and {'date': newest[0], 'id': newest[1], 'title': newest[2], 'course': newest[3], 'slug': newest[4]},
        'pts': [[round(c['lat'], 2), round(c['lng'], 2)] for c in courses if c['state'] != 'At Sea'],
    })


def main():
    issues = []
    courses = read_map(issues)
    looked_up = fill_titles(courses, issues)
    tag(courses)
    save('courses-base.json', courses)
    videos = sum(c['n'] for c in courses)
    print(f'Map: {len(courses)} courses, {videos} videos ({looked_up} new titles looked up)')

    sys.path.insert(0, HERE)
    import sync_playlists
    sync_playlists.main()
    write_stats()

    import update_merch
    try:
        items = update_merch.update_merch()
        print(f'Merch: {len(items)} products for the home page')
    except Exception as e:  # a store hiccup should never block the course update
        issues.append(f'Merch store could not be read today ({e}); keeping yesterday\'s products')

    if issues:
        print(f'\n{len(issues)} things in the map to check:')
        for i in issues:
            print('  -', i)


if __name__ == '__main__':
    main()
