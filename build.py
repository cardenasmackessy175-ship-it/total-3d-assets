#!/usr/bin/env python3
"""Rebuild the total-3d-assets pages: a page per source that shows every asset of that site, with the site's
categories in a left sidebar; each category has its own page under <source>/cat/<category>/.

The repo's img/ and vid/ trees say what is on the site (one <source>/<plan or category>/<asset>.webp per asset);
the category of each asset comes from the pipeline's candidate index (name<TAB>category<TAB>plan<TAB>source).
Old pages are left in place; only index.html, <source>/index.html and <source>/cat/** are rewritten.
"""
import json
import re
import shutil
import sys
import urllib.parse
from collections import Counter
from pathlib import Path

STYLE = """
:root{color-scheme:dark;--bg:#141417;--panel:#17171b;--line:#26262d;--ink:#e8e8ea;--dim:#9a9aa4;--acc:#8ab4ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
.wrap{display:flex;min-height:100vh}
nav{width:250px;flex:none;position:sticky;top:0;height:100vh;overflow-y:auto;background:var(--panel);border-right:1px solid var(--line);padding:14px 12px 40px}
nav .back{display:block;color:var(--dim);font-size:13px;margin-bottom:10px}
nav h2{font-size:15px;margin:0 0 8px;font-weight:600}
nav input{width:100%;background:#101013;border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:6px 8px;font:13px inherit;margin-bottom:10px}
nav a.cat{display:flex;justify-content:space-between;gap:8px;padding:5px 8px;border-radius:5px;color:var(--ink);font-size:13.5px}
nav a.cat span.n{color:var(--dim);font-variant-numeric:tabular-nums;flex:none}
nav a.cat:hover{background:#202027;text-decoration:none}
nav a.cat.on{background:#233149;color:#cfe0ff}
main{flex:1;min-width:0}
header{position:sticky;top:0;z-index:5;background:#141417e6;backdrop-filter:blur(6px);padding:14px 20px;border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0;font-weight:600}
.meta{color:var(--dim);font-size:13px;margin-top:2px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px;padding:12px}
.tile{position:relative;background:#1c1c21;border-radius:6px;overflow:hidden;aspect-ratio:1}
.tile img,.tile video{width:100%;height:100%;object-fit:cover;display:block;background:#1c1c21}
.end{text-align:center;color:#666;padding:24px}
@media(max-width:760px){.wrap{display:block}nav{position:static;width:auto;height:auto;border-right:0;border-bottom:1px solid var(--line)}}
"""

LIST_STYLE = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#141417;color:#e8e8ea;font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
a{color:#8ab4ff;text-decoration:none}a:hover{text-decoration:underline}
header{padding:22px 20px 10px;max-width:900px;margin:0 auto}
h1{font-size:20px;margin:0;font-weight:600}
.meta{color:#9a9aa4;font-size:13px;margin-top:3px}
.list{max-width:900px;margin:8px auto 40px;padding:0 20px}
.list a.row{display:flex;justify-content:space-between;padding:11px 0;border-bottom:1px solid #232329}
.list a.row:hover{background:#1b1b21;text-decoration:none;padding-left:8px;transition:padding .1s}
.list a.row .n{color:#9a9aa4;font-variant-numeric:tabular-nums}
"""

GRID_JS = """
const grid=document.getElementById('g');let i=0;
function add(){const n=Math.min(180,ITEMS.length-i);const f=document.createDocumentFragment();
for(let k=0;k<n;k++){const it=ITEMS[i++];const d=document.createElement('div');d.className='tile';const u=IMG+'/'+it.p+'.webp';
if(it.v){const v=document.createElement('video');v.src=VID+'/'+it.p+'.mp4';v.poster=u;v.muted=true;v.loop=true;v.playsInline=true;v.preload='none';v.controls=true;d.appendChild(v);}
else{const im=document.createElement('img');im.loading='lazy';im.src=u;d.appendChild(im);}
grid.appendChild(d);}
if(i>=ITEMS.length){document.getElementById('end').style.display='block';if(window.ob)window.ob.disconnect();}}
window.ob=new IntersectionObserver(es=>{if(es[0].isIntersecting)add()},{rootMargin:'1200px'});
window.ob.observe(document.getElementById('end'));
add();add();
const q=document.getElementById('q');
if(q)q.addEventListener('input',()=>{const t=q.value.toLowerCase();document.querySelectorAll('nav a.cat').forEach(a=>{a.style.display=(a.dataset.name||'').includes(t)||a.classList.contains('on')?'':'none';});});
"""

CIRCLED = "·"


def esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def read_categories(paths):
    """name -> (category, source) from the candidate index dumps."""
    seen = {}
    for path in paths:
        for line in open(path, encoding="utf-8"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            name, category, _plan, source = parts
            if name and name not in seen:
                seen[name] = (category or "_", source)
    return seen


def scan_images(repo):
    """source -> list of {p: image key, n: asset name, c: category, v: has video}."""
    by_source = {}
    img_root = repo / "img"
    vid_root = repo / "vid"
    for path in sorted(img_root.rglob("*.webp")):
        rel = path.relative_to(img_root).with_suffix("").as_posix()
        parts = rel.split("/")
        if len(parts) < 3:
            continue
        source, name = parts[0], parts[-1]
        by_source.setdefault(source, []).append({"p": rel, "n": name, "v": (vid_root / (rel + ".mp4")).exists()})
    return by_source


def page(depth, title, heading, meta, categories, active, items, back_href, back_text):
    prefix = "../" * depth
    links = []
    for cat, count in categories:
        href = prefix + ("" if cat is None else "cat/%s/" % urllib.parse.quote(cat, safe=""))
        cls = "cat on" if (cat or "") == (active or "") else "cat"
        label = "untagged" if cat == "_" else (cat if cat is not None else "全部")
        links.append('<a class="%s" data-name="%s" href="%s"><span>%s</span><span class="n">%d</span></a>' % (
            cls, esc(label.lower()), esc(href), esc(label), count))
    data = json.dumps([{"p": it["p"], "v": 1 if it["v"] else 0} for it in items], ensure_ascii=False, separators=(",", ":"))
    return """<!doctype html><html lang=zh><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>%(title)s</title><style>%(style)s</style><body>
<div class=wrap><nav><a class=back href="%(back_href)s">%(back_text)s</a><h2>%(heading)s</h2>
<input id=q placeholder="筛选分类">%(links)s</nav>
<main><header><h1>%(heading)s</h1><div class=meta>%(meta)s</div></header>
<div class=grid id=g></div><div class=end id=end style=display:none>·</div></main></div>
<script>const ITEMS=%(data)s;const IMG="%(prefix)simg",VID="%(prefix)svid";%(js)s</script></body></html>
""" % dict(title=esc(title), style=STYLE, back_href=esc(back_href), back_text=esc(back_text), heading=esc(heading),
           meta=esc(meta), links="".join(links), data=data, prefix=prefix, js=GRID_JS)


def build(repo, categories_file):
    cats = read_categories([categories_file])
    by_source = scan_images(repo)
    counts = {}
    for source, items in by_source.items():
        per = Counter()
        for it in items:
            it["c"] = cats.get(it["n"], ("_", source))[0].strip() or "_"
            per[it["c"]] += 1
        counts[source] = per
        per_name = sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))
        total = len(items)
        sidebar = [(None, total)] + per_name
        out = repo / source
        # the site's own pages under <source>/index.html
        (out / "index.html").write_text(page(1, source, source, "%d 个资产" % total, sidebar, None, items,
                                             "../index.html", "total 3d assets"), encoding="utf-8")
        shutil.rmtree(out / "cat", ignore_errors=True)
        for cat, count in per_name:
            sub = items if False else [it for it in items if it["c"] == cat]
            target = out / "cat" / cat if cat != "_" else out / "cat" / "_"
            target.mkdir(parents=True, exist_ok=True)
            (target / "index.html").write_text(
                page(3, "%s · %s" % (source, cat), "%s · %s" % (source, "untagged" if cat == "_" else cat),
                     "%d 个资产" % count, sidebar, cat, sub, "../../../index.html", "total 3d assets"),
                encoding="utf-8")
    # the front page: sources, biggest first
    rows = "".join('<a class=row href="%s/index.html"><span>%s</span><span class=n>%d</span></a>' % (
        esc(s), esc(s), sum(counts[s].values())) for s in sorted(counts, key=lambda s: -sum(counts[s].values())))
    total = sum(sum(c.values()) for c in counts.values())
    (repo / "index.html").write_text(
        """<!doctype html><html lang=zh><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>total 3d assets</title><style>%(style)s</style><body>
<header><h1>total 3d assets</h1><div class=meta>%(total)d</div></header><div class=list>%(rows)s</div></body></html>
""" % dict(style=LIST_STYLE, total=total, rows=rows), encoding="utf-8")
    return counts, total


if __name__ == "__main__":
    repo = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    counts, total = build(repo, sys.argv[2] if len(sys.argv) > 2 else "/tmp/asset_cats.tsv")
    for source in sorted(counts, key=lambda s: -sum(counts[s].values())):
        print("%-10s %6d assets, %3d categories" % (source, sum(counts[source].values()), len(counts[source])))
    print("total", total)
