"""
Find A Course: pull the newest merch for the home page carousel.

Public data only: reads shopbrooksholt.com's "All Products" page for the
product list (in the store's own order), then each product page's standard
product data (name, price, photos). Skips repeats and sold-out items.

Output: data/merch.json  [{name, url, image, price}, ...]
Run on its own:  python3 build/update_merch.py
"""
import json, os, re, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), 'data')
STORE = 'https://shopbrooksholt.com'
HOW_MANY = 8
UA = {'User-Agent': 'Mozilla/5.0 (FindACourseSync/1.0)'}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read().decode('utf-8', 'replace')


def product_data(html):
    for m in re.finditer(r'<script[^>]*ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if d.get('@type') == 'Product':
            return d
    return None


def update_merch():
    listing = get(STORE + '/collections/all')
    slugs = list(dict.fromkeys(re.findall(r'href="/products/([\w-]+)"', listing)))
    items, names = [], set()
    for slug in slugs:
        if len(items) >= HOW_MANY:
            break
        p = product_data(get(f'{STORE}/products/{slug}'))
        if not p or not p.get('image'):
            continue
        name = p['name'].strip()
        offers = p.get('offers') or []
        offers = offers if isinstance(offers, list) else [offers]
        if offers and all('InStock' not in str(o.get('availability', '')) for o in offers):
            continue  # sold out
        if name.lower() in names:
            continue  # same design listed twice
        names.add(name.lower())
        prices = [float(o['price']) for o in offers if o.get('price')]
        image = p['image'][0] if isinstance(p['image'], list) else p['image']
        items.append({'name': name, 'url': f'{STORE}/products/{slug}', 'image': image,
                      'price': f'${min(prices):.0f}' if prices else None})
    if not items:
        raise RuntimeError('no products found on the store page')
    path = os.path.join(DATA, 'merch.json')
    with open(path + '.tmp', 'w') as f:
        json.dump(items, f, indent=1, ensure_ascii=False)
    os.replace(path + '.tmp', path)
    return items


if __name__ == '__main__':
    for i in update_merch():
        print(f"{i['price'] or '':>4}  {i['name']}")
