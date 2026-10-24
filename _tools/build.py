#!/usr/bin/env python3
"""WordPress(WXR) -> 静的サイト変換 (pacman-frog.com / ハバネロの蔵)

使い方: python3 build.py <export.xml> <uploads_dir> <out_dir>
"""
import sys, re, os, shutil, json, html, collections
from urllib.parse import unquote, urlparse, urlunparse, parse_qsl, urlencode
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup, Comment

NS = {'wp': 'http://wordpress.org/export/1.2/', 'content': 'http://purl.org/rss/1.0/modules/content/'}
SITE_URL = 'https://www.pacman-frog.com'
SITE_NAME = 'ハバネロの蔵'
DOMAIN_RE = re.compile(r'https?://(?:www\.)?pacman-frog\.com', re.I)

# アフィリエイト/広告の中継ドメイン(リンクは外して文字だけ残す、画像のみなら丸ごと削除)
AD_LINK_HOSTS = ('px.a8.net', 'ck.jp.ap.valuecommerce.com', 'hb.afl.rakuten.co.jp', 'pt.afl.rakuten.co.jp',
                 'www.blogclick.jp', 'ck.jp.ap.valuecommerce.com', 'af.moshimo.com', 'click.linksynergy.com')
# 画像ごと削除する広告・計測ピクセルのドメイン
AD_IMG_HOSTS = ('www.assoc-amazon.jp', 'ad.jp.ap.valuecommerce.com', 'thumbnail.image.rakuten.co.jp') + \
    tuple('www%d.a8.net' % i for i in range(0, 40)) + ('i.moshimo.com', 'hbb.afl.rakuten.co.jp', 'hbb.afl.rakuten.co.jp')
AD_IFRAME_HOSTS = ('ad.jp.ap.valuecommerce.com', 'xml.bidders.co.jp', 'rcm-jp.amazon.co.jp', 'rcm.amazon.com')
KEEP_IFRAME_HOSTS = ('www.youtube.com', 'www.youtube-nocookie.com', 'player.vimeo.com')

ALLBLOCKS = ('(?:table|thead|tfoot|caption|col|colgroup|tbody|tr|td|th|div|dl|dd|dt|ul|ol|li|pre|form|map|area|'
             'blockquote|address|math|style|p|h[1-6]|hr|fieldset|legend|section|article|aside|hgroup|header|footer|'
             'nav|figure|figcaption|details|menu|summary)')


def wpautop(pee):
    """WordPressのwpautopを簡略移植(旧記事の改行→段落)"""
    if not pee.strip():
        return ''
    pee = pee.replace('\r\n', '\n').replace('\r', '\n') + '\n'
    pee = re.sub(r'<br\s*/?>\s*<br\s*/?>', '\n\n', pee)
    pee = re.sub('(<' + ALLBLOCKS + r'[\s/>])', r'\n\n\1', pee)
    pee = re.sub('(</' + ALLBLOCKS + r'>)', r'\1\n\n', pee)
    pee = re.sub(r'\n\n+', '\n\n', pee)
    pees = [p for p in re.split(r'\n\s*\n', pee) if p.strip()]
    pee = ''.join('<p>' + p.strip('\n') + '</p>\n' for p in pees)
    pee = re.sub(r'<p>\s*</p>', '', pee)
    pee = re.sub(r'<p>([^<]+)</(div|address|form)>', r'<p>\1</p></\2>', pee)
    pee = re.sub(r'<p>\s*(</?' + ALLBLOCKS + r'[^>]*>)\s*</p>', r'\1', pee)
    pee = re.sub(r'<p>(<li.+?)</p>', r'\1', pee)
    pee = re.sub(r'<p><blockquote([^>]*)>', r'<blockquote\1><p>', pee)
    pee = pee.replace('</blockquote></p>', '</p></blockquote>')
    pee = re.sub(r'<p>\s*(</?' + ALLBLOCKS + r'[^>]*>)', r'\1', pee)
    pee = re.sub(r'(</?' + ALLBLOCKS + r'[^>]*>)\s*</p>', r'\1', pee)
    pee = re.sub(r'(?<!<br />)\s*\n', '<br />\n', pee)
    pee = re.sub(r'(</?' + ALLBLOCKS + r'[^>]*>)\s*<br />', r'\1', pee)
    pee = re.sub(r'<br />(\s*</?(?:p|li|div|dl|dd|dt|th|pre|td|ul|ol)[^>]*>)', r'\1', pee)
    pee = re.sub(r'\n</p>$', '</p>', pee)
    return pee


def host(u):
    try:
        return urlparse(u).netloc.lower()
    except Exception:
        return ''


def fmt_date(d):
    y, m, dd = d[:10].split('-')
    return '%d年%d月%d日' % (int(y), int(m), int(dd))


def esc(s):
    return html.escape(s or '', quote=True)


class Stats(collections.Counter):
    pass


STATS = Stats()
USED_UPLOADS = set()
MISSING_UPLOADS = collections.defaultdict(set)


def clean_content(raw, kind, ctx_url, upload_index):
    """本文のHTMLを整形: 広告除去 / 内部リンク相対化 / 画像パス修正"""
    raw = re.sub(r'<!--more-->|<!--nextpage-->', '', raw)
    gutenberg = '<!-- wp:' in raw
    # script / noscript / style を先に除去
    raw = re.sub(r'<script\b.*?</script\s*>', '', raw, flags=re.S | re.I)
    raw = re.sub(r'<noscript\b.*?</noscript\s*>', '', raw, flags=re.S | re.I)
    raw = re.sub(r'<script\b[^>]*/>', '', raw, flags=re.I)
    # タグ内の改行を空白に(wpautopでbrが入り込むのを防ぐ)
    raw = re.sub(r'<[a-zA-Z/][^<>]*>', lambda m: re.sub(r'\s+', ' ', m.group(0)), raw)
    if not gutenberg:
        raw = wpautop(raw)
    soup = BeautifulSoup(raw, 'html.parser')
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()

    # iframe
    for f in soup.find_all('iframe'):
        h = host(f.get('src', ''))
        if h in KEEP_IFRAME_HOSTS:
            f['loading'] = 'lazy'
            continue
        f.decompose(); STATS['ad_iframe_removed'] += 1
    # 広告画像・計測ピクセル
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if host(src) in AD_IMG_HOSTS or img.get('width') == '1' and img.get('height') == '1':
            parent = img.parent
            img.decompose(); STATS['ad_img_removed'] += 1
            if parent and parent.name == 'a' and not parent.get_text(strip=True) and not parent.find('img'):
                parent.decompose()
    # 広告中継リンク
    for a in soup.find_all('a'):
        href = a.get('href', '')
        if host(href) in AD_LINK_HOSTS:
            STATS['ad_link_unwrapped'] += 1
            if a.get_text(strip=True) or a.find('img'):
                a.unwrap()
            else:
                a.decompose()
        elif 'amazon.co.jp' in host(href) or 'amazlet.com' in host(href):
            # Amazonアフィリエイトのタグだけ外す(リンク自体は残す)
            p = urlparse(href)
            q = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in ('tag', 'linkcode', 'creative', 'camp')]
            a['href'] = urlunparse(p._replace(query=urlencode(q)))
            a['rel'] = 'noopener nofollow'; a['target'] = '_blank'

    # 内部リンク・画像の書き換え
    def fix_url(u):
        if not u:
            return u
        u2 = DOMAIN_RE.sub('', u)
        if u2 == '':
            return '/'
        if u2.startswith('/wp-content/uploads/'):
            rel = unquote(u2[len('/wp-content/uploads/'):].split('?')[0].split('#')[0])
            real = upload_index.get(rel.lower())
            if real:
                USED_UPLOADS.add(real)
                return '/wp-content/uploads/' + real
            MISSING_UPLOADS[rel].add(ctx_url)
            return u2
        return u2

    for tag, attr in (('a', 'href'), ('img', 'src'), ('iframe', 'src'), ('source', 'src')):
        for el in soup.find_all(tag):
            if el.get(attr):
                el[attr] = fix_url(el[attr])
    for img in soup.find_all('img'):
        for k in ('srcset', 'sizes', 'data-link', 'data-id', 'data-full-url'):
            if k in img.attrs:
                if k == 'srcset':
                    img[k] = ', '.join(fix_url(p.strip().split(' ')[0]) + (' ' + p.strip().split(' ')[1] if ' ' in p.strip() else '')
                                       for p in img[k].split(','))
                else:
                    del img[k]
        img['loading'] = 'lazy'
        if not img.get('alt'):
            img['alt'] = ''
        # 画像が元大きさへのリンク(WP添付ページ)の場合は画像自体へのリンクにする
    for a in soup.find_all('a'):
        h = a.get('href', '')
        if a.find('img') and re.match(r'^/(?:post-\d+|page-\d+|[^/]+)/[^/]+/$', h or '') and not h.startswith('/category/'):
            # 添付ファイルページ(/post-N/slug/)へのリンク → 画像そのものに
            im = a.find('img')
            if im and im.get('src', '').startswith('/wp-content/'):
                a['href'] = im['src']
    # 空になった要素の掃除
    for _ in range(2):
        for el in soup.find_all(['p', 'div', 'span', 'center', 'strong', 'a']):
            if not el.get_text(strip=True) and not el.find(['img', 'iframe', 'br', 'hr', 'table']):
                el.decompose()
    # 連続<br>を整理
    out = str(soup)
    out = re.sub(r'(?:<br\s*/?>\s*){3,}', '<br/><br/>', out)
    out = re.sub(r'^(?:\s*<br\s*/?>)+', '', out)
    return out


# --------------------------------------------------------------------------- テンプレート
CSS = """
:root{--bg:#f6f8f1;--card:#fff;--ink:#26301f;--sub:#6b7a5c;--accent:#3f7d2c;--accent2:#e8a21a;--line:#dfe6d2;--max:46rem}
@media(prefers-color-scheme:dark){:root{--bg:#161b12;--card:#1f2619;--ink:#e6ecdc;--sub:#9fb08c;--accent:#8fd16e;--accent2:#f0b84a;--line:#2e3a25}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.85 "Hiragino Kaku Gothic ProN","Hiragino Sans","Yu Gothic UI","Meiryo",system-ui,sans-serif}
a{color:var(--accent)}
a:hover{color:var(--accent2)}
header.site{background:var(--accent);color:#fff;padding:1.1rem 16px .5rem}
header.site .in,main,footer .in{max-width:var(--max);margin:0 auto}
header.site .title{font-size:1.5rem;font-weight:700;letter-spacing:.08em;color:#fff;text-decoration:none}
header.site .tag{font-size:.78rem;opacity:.9;margin:.2rem 0 .6rem}
nav.main{display:flex;flex-wrap:wrap;gap:.2rem .9rem;font-size:.9rem;padding:.3rem 0 .4rem}
nav.main a{color:#fff;text-decoration:none;opacity:.92;white-space:nowrap}
nav.main a:hover{text-decoration:underline;opacity:1;color:#fff}
main{padding:1.5rem 16px 3rem}
article,.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1.4rem 1.5rem;margin-bottom:1.5rem;overflow-wrap:anywhere}
h1.entry{font-size:1.5rem;line-height:1.45;margin:.2rem 0 .6rem}
.meta{font-size:.82rem;color:var(--sub);margin-bottom:1rem}
.meta a{color:var(--sub)}
.entry-body img{max-width:100%;height:auto;border-radius:4px}
.entry-body h2,.entry-body h3,.entry-body h4{line-height:1.4;margin:1.8em 0 .6em;border-left:5px solid var(--accent);padding-left:.6em}
.entry-body table{border-collapse:collapse;max-width:100%;display:block;overflow-x:auto}
.entry-body td,.entry-body th{border:1px solid var(--line);padding:.3em .6em}
.entry-body iframe{max-width:100%}
.entry-body blockquote{margin:1em 0;padding:.2em 1em;border-left:4px solid var(--line);color:var(--sub)}
.entry-body ul.wp-block-gallery{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:.6rem}
ul.plain{list-style:none;padding:0;margin:0}
ul.plain li{padding:.45rem 0;border-bottom:1px dashed var(--line);display:flex;gap:.9rem;align-items:baseline}
ul.plain time{font-size:.8rem;color:var(--sub);white-space:nowrap;min-width:7.6em}
h2.sec{font-size:1.15rem;margin:0 0 .8rem;color:var(--accent)}
.cats a{display:inline-block;margin:.15rem .3rem .15rem 0;padding:.05rem .65rem;border:1px solid var(--line);border-radius:99px;font-size:.82rem;text-decoration:none}
.comments{margin-top:2rem;border-top:2px solid var(--line);padding-top:1rem}
.comments h2{font-size:1.05rem}
.comments .note{font-size:.8rem;color:var(--sub)}
.comment{border:1px solid var(--line);border-radius:8px;padding:.7rem 1rem;margin:.8rem 0;background:var(--bg)}
.comment .who{font-size:.82rem;color:var(--sub)}
.comment .who b{color:var(--ink)}
.comment .reply{margin-left:1.2rem}
.pn{display:flex;justify-content:space-between;gap:1rem;font-size:.9rem;margin:1.5rem 0 0}
footer{border-top:1px solid var(--line);color:var(--sub);font-size:.8rem;padding:1.2rem 16px 2.5rem;text-align:center}
input.q{width:100%;padding:.6rem .8rem;font-size:1rem;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}
@media(max-width:560px){article,.card{padding:1.1rem 1rem}ul.plain li{flex-direction:column;gap:0}}
"""

NAV = []  # [(label, url)]


def page_html(title, body, desc='', path='/', og_type='article', robots=None):
    full_title = SITE_NAME if path == '/' else '%s | %s' % (title, SITE_NAME)
    nav = ''.join('<a href="%s">%s</a>' % (esc(u), esc(l)) for l, u in NAV)
    desc = esc((desc or '').strip())[:160]
    canonical = SITE_URL + path
    rb = '<meta name="robots" content="%s">' % robots if robots else ''
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(full_title)}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{canonical}">
<meta property="og:title" content="{esc(full_title)}"><meta property="og:type" content="{og_type}">
<meta property="og:url" content="{canonical}"><meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:description" content="{desc}">
{rb}
<link rel="stylesheet" href="/assets/style.css">
</head><body>
<header class="site"><div class="in">
<a class="title" href="/">{SITE_NAME}</a>
<div class="tag">ベルツノガエル・クランウェルツノガエルの飼育情報サイト</div>
<nav class="main">{nav}<a href="/archive/">記事一覧</a><a href="/search/">検索</a></nav>
</div></header>
<main>
{body}
</main>
<footer><div class="in">© 2005–2026 {SITE_NAME}（ハバネロ） / <a href="/archive/">記事一覧</a> / <a href="/search/">サイト内検索</a></div></footer>
</body></html>
"""


def write(out, path, content):
    """path: '/post-1/' -> out/post-1/index.html"""
    if path.endswith('/'):
        fp = os.path.join(out, path.strip('/'), 'index.html') if path != '/' else os.path.join(out, 'index.html')
    else:
        fp = os.path.join(out, path.strip('/'))
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(content)


def text_of(html_str, n=120):
    t = BeautifulSoup(html_str, 'html.parser').get_text(' ', strip=True)
    return re.sub(r'\s+', ' ', t)[:n]


def main(xml, uploads_dir, out):
    ch = ET.parse(xml).getroot().find('channel')
    upload_files = os.listdir(uploads_dir)
    upload_index = {f.lower(): f for f in upload_files}

    cats = {}
    for c in ch.findall('wp:category', NS):
        nn = c.findtext('wp:category_nicename', namespaces=NS)
        cats[nn] = c.findtext('wp:cat_name', namespaces=NS)
        # "ツノガエル【餌の蔵】" 等はそのまま使用

    items = []
    for it in ch.findall('item'):
        t = it.find('wp:post_type', NS).text
        if t not in ('post', 'page'):
            continue
        status = it.find('wp:status', NS).text
        if status != 'publish':
            STATS['skipped_nonpublic'] += 1
            continue
        link = DOMAIN_RE.sub('', it.findtext('link'))
        link = unquote(link) if not re.fullmatch(r'/(post|page)-\d+/', link) else link
        link = link if link.endswith('/') else link + '/'
        comments = []
        for cm in it.findall('wp:comment', NS):
            if cm.findtext('wp:comment_approved', namespaces=NS) != '1':
                continue
            if cm.findtext('wp:comment_type', namespaces=NS) not in ('comment', '', None):
                continue
            comments.append(dict(id=cm.findtext('wp:comment_id', namespaces=NS),
                                 parent=cm.findtext('wp:comment_parent', namespaces=NS) or '0',
                                 author=html.unescape(cm.findtext('wp:comment_author', namespaces=NS) or '名無しさん'),
                                 date=cm.findtext('wp:comment_date', namespaces=NS) or '',
                                 body=cm.findtext('wp:comment_content', namespaces=NS) or ''))
        items.append(dict(
            id=it.findtext('wp:post_id', namespaces=NS), type=t, title=it.findtext('title') or '(無題)',
            date=it.findtext('wp:post_date', namespaces=NS), link=link,
            raw=it.findtext('content:encoded', namespaces=NS) or '',
            cats=[c.get('nicename') for c in it.findall('category') if c.get('domain') == 'category'],
            comments=comments))

    posts = sorted([i for i in items if i['type'] == 'post'], key=lambda x: x['date'], reverse=True)
    pages = [i for i in items if i['type'] == 'page']

    # ナビ(元サイトのメニュー順)
    menu_ids = ['1188', '3358', '3360', '3362', '3366', '3368']
    by_id = {p['id']: p for p in pages}
    for mid in menu_ids:
        p = by_id.get(mid)
        if p:
            NAV.append(('ホーム' if mid == '1188' else p['title'], '/' if mid == '1188' else p['link']))
    if not any(l == 'ホーム' for l, _ in NAV):
        NAV.insert(0, ('ホーム', '/'))

    os.makedirs(out, exist_ok=True)
    os.makedirs(os.path.join(out, 'assets'), exist_ok=True)
    with open(os.path.join(out, 'assets', 'style.css'), 'w') as f:
        f.write(CSS)

    # ---- 本文処理
    for i in items:
        i['html'] = clean_content(i['raw'], i['type'], i['link'], upload_index)

    def cat_links(i):
        return ''.join('<a href="/category/%s/">%s</a>' % (esc(c), esc(cats.get(c, c))) for c in i['cats'] if c in cats)

    def comments_html(i):
        cs = i['comments']
        if not cs:
            return ''
        kids = collections.defaultdict(list)
        ids = {c['id'] for c in cs}
        for c in sorted(cs, key=lambda c: c['date']):
            kids[c['parent'] if c['parent'] in ids else '0'].append(c)

        def render(c, depth=0):
            body = re.sub(r'[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '(メールアドレス非表示)', esc(html.unescape(c['body']))).replace('\n', '<br>')
            s = '<div class="comment%s" id="comment-%s"><div class="who"><b>%s</b> ／ %s</div><p>%s</p></div>' % (
                ' reply' if depth else '', c['id'], esc(c['author']), fmt_date(c['date']) if c['date'] else '', body)
            for k in kids.get(c['id'], []):
                s += render(k, 1)
            return s
        inner = ''.join(render(c) for c in kids['0'])
        return ('<section class="comments"><h2>コメント（%d件）</h2>'
                '<p class="note">過去にいただいたコメントを掲載しています。現在、コメントの受付は終了しています。</p>%s</section>'
                % (len(cs), inner))

    # ---- 記事・固定ページ
    posts_asc = list(reversed(posts))
    pos = {p['id']: n for n, p in enumerate(posts_asc)}
    search_index = []
    for p in posts:
        n = pos[p['id']]
        prev = posts_asc[n - 1] if n > 0 else None
        nxt = posts_asc[n + 1] if n + 1 < len(posts_asc) else None
        pn = '<div class="pn"><span>%s</span><span>%s</span></div>' % (
            '← <a href="%s">%s</a>' % (esc(prev['link']), esc(prev['title'])) if prev else '',
            '<a href="%s">%s</a> →' % (esc(nxt['link']), esc(nxt['title'])) if nxt else '')
        body = ('<article><h1 class="entry">%s</h1><div class="meta">%s ／ <span class="cats">%s</span></div>'
                '<div class="entry-body">%s</div>%s</article>%s') % (
            esc(p['title']), fmt_date(p['date']), cat_links(p), p['html'], comments_html(p), pn)
        write(out, p['link'], page_html(p['title'], body, text_of(p['html']), p['link']))
        search_index.append(dict(t=p['title'], u=p['link'], d=p['date'][:10], x=text_of(p['html'], 160),
                                 c=[cats.get(c, c) for c in p['cats'] if c in cats]))

    home = by_id.get('1188')
    for pg in pages:
        body = '<article><h1 class="entry">%s</h1><div class="entry-body">%s</div>%s</article>' % (
            esc(pg['title']), pg['html'], comments_html(pg))
        write(out, pg['link'], page_html(pg['title'], body, text_of(pg['html']), pg['link']))
        search_index.append(dict(t=pg['title'], u=pg['link'], d=pg['date'][:10], x=text_of(pg['html'], 160), c=[]))

    # ---- トップ(ようこそページ + 新着)
    latest = ''.join('<li><time>%s</time><a href="%s">%s</a></li>' % (fmt_date(p['date']), esc(p['link']), esc(p['title']))
                     for p in posts[:15])
    home_body = ''
    if home:
        home_body += '<article><h1 class="entry">%s</h1><div class="entry-body">%s</div></article>' % (esc(home['title']), home['html'])
    home_body += '<section class="card"><h2 class="sec">新着記事</h2><ul class="plain">%s</ul><p><a href="/archive/">すべての記事を見る →</a></p></section>' % latest
    write(out, '/', page_html(SITE_NAME, home_body, 'ベルツノガエルやクランウェルツノガエルの飼育情報が満載！ ツノガエル飼育者の、ツノガエル飼育者による、ツノガエル飼育者のための、ツノガエル飼育情報サイトです。', '/', 'website'))

    # ---- カテゴリ
    cat_count = {}
    for nn, name in cats.items():
        ps = [p for p in posts if nn in p['cats']]
        cat_count[nn] = len(ps)
        if not ps:
            continue
        li = ''.join('<li><time>%s</time><a href="%s">%s</a></li>' % (fmt_date(p['date']), esc(p['link']), esc(p['title'])) for p in ps)
        body = '<section class="card"><h1 class="entry">%s</h1><div class="meta">%d件</div><ul class="plain">%s</ul></section>' % (esc(name), len(ps), li)
        write(out, '/category/%s/' % unquote(nn), page_html(name, body, '%s の記事一覧' % name, '/category/%s/' % unquote(nn), 'website'))

    # ---- 記事一覧(年別)
    byyear = collections.OrderedDict()
    for p in posts:
        byyear.setdefault(p['date'][:4], []).append(p)
    cat_nav = ''.join('<a href="/category/%s/">%s（%d）</a>' % (esc(unquote(nn)), esc(cats[nn]), c) for nn, c in cat_count.items() if c)
    body = '<section class="card"><h1 class="entry">記事一覧</h1><div class="cats">%s</div></section>' % cat_nav
    for y, ps in byyear.items():
        li = ''.join('<li><time>%s</time><a href="%s">%s</a></li>' % (fmt_date(p['date']), esc(p['link']), esc(p['title'])) for p in ps)
        body += '<section class="card"><h2 class="sec">%s年（%d件）</h2><ul class="plain">%s</ul></section>' % (y, len(ps), li)
    write(out, '/archive/', page_html('記事一覧', body, 'ハバネロの蔵の全記事一覧', '/archive/', 'website'))

    # ---- 検索
    with open(os.path.join(out, 'search-index.json'), 'w', encoding='utf-8') as f:
        json.dump(search_index, f, ensure_ascii=False, separators=(',', ':'))
    sbody = """<section class="card"><h1 class="entry">サイト内検索</h1>
<input class="q" id="q" type="search" placeholder="キーワードを入力（例：餌、病気、オタマジャクシ）" autofocus>
<p class="meta" id="cnt"></p><ul class="plain" id="res"></ul></section>
<script>
(function(){var idx=[],q=document.getElementById('q'),res=document.getElementById('res'),cnt=document.getElementById('cnt');
function norm(s){return (s||'').toLowerCase().normalize('NFKC')}
function run(){var w=norm(q.value).split(/\\s+/).filter(Boolean);res.innerHTML='';
if(!w.length){cnt.textContent='';return}
var hit=idx.filter(function(p){var h=norm(p.t+' '+p.x+' '+p.c.join(' '));return w.every(function(k){return h.indexOf(k)>=0})});
cnt.textContent=hit.length+'件';
hit.slice(0,100).forEach(function(p){var li=document.createElement('li');var t=document.createElement('time');t.textContent=p.d;var a=document.createElement('a');a.href=p.u;a.textContent=p.t;li.appendChild(t);li.appendChild(a);res.appendChild(li)})}
fetch('/search-index.json').then(function(r){return r.json()}).then(function(j){idx=j;run()});
q.addEventListener('input',run);
var m=location.search.match(/[?&]q=([^&]*)/);if(m){q.value=decodeURIComponent(m[1].replace(/\\+/g,' '));}
})();
</script>"""
    write(out, '/search/', page_html('サイト内検索', sbody, 'ハバネロの蔵のサイト内検索', '/search/', 'website'))

    # ---- 404
    write(out, '/404.html', page_html('ページが見つかりません', '<section class="card"><h1 class="entry">ページが見つかりません</h1><p>お探しのページは移動または削除された可能性があります。<a href="/">トップページ</a>、または<a href="/search/">サイト内検索</a>をご利用ください。</p></section>', '', '/404.html', robots='noindex'))

    # ---- sitemap / robots / CNAME / .nojekyll
    urls = ['/'] + [i['link'] for i in items if i['id'] != '1188'] + ['/archive/'] + ['/category/%s/' % unquote(n) for n, c in cat_count.items() if c]
    sm = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + ''.join(
        '<url><loc>%s</loc></url>\n' % esc(SITE_URL + u) for u in urls) + '</urlset>\n'
    # sitemapのURLは日本語をパーセントエンコード
    from urllib.parse import quote
    sm = re.sub(r'<loc>(.*?)</loc>', lambda m: '<loc>' + quote(html.unescape(m.group(1)), safe=":/%?&=#") + '</loc>', sm)
    open(os.path.join(out, 'sitemap.xml'), 'w', encoding='utf-8').write(sm)
    open(os.path.join(out, 'robots.txt'), 'w').write('User-agent: *\nAllow: /\nSitemap: %s/sitemap.xml\n' % SITE_URL)
    open(os.path.join(out, 'CNAME'), 'w').write('www.pacman-frog.com\n')
    open(os.path.join(out, '.nojekyll'), 'w').write('')

    # ---- 画像コピー(使用分 + 元画像[縮小版でないもの])
    dst = os.path.join(out, 'wp-content', 'uploads')
    os.makedirs(dst, exist_ok=True)
    resized = re.compile(r'-\d+x\d+(\.\w+)$')
    copy = set(USED_UPLOADS) | {f for f in upload_files if not resized.search(f)}
    for f in sorted(copy):
        shutil.copy2(os.path.join(uploads_dir, f), os.path.join(dst, f))

    # ---- レポート
    rep = dict(posts=len(posts), pages=len(pages), comments_shown=sum(len(i['comments']) for i in items),
               images_copied=len(copy), images_referenced=len(USED_UPLOADS),
               missing_images={k: sorted(v) for k, v in MISSING_UPLOADS.items()}, stats=dict(STATS))
    json.dump(rep, open(os.path.join(os.path.dirname(os.path.abspath(out)), 'report.json'), 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in rep.items() if k != 'missing_images'}, ensure_ascii=False))
    print('missing images:', len(MISSING_UPLOADS))


if __name__ == '__main__':
    main(*sys.argv[1:4])
