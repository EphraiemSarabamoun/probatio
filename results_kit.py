#!/usr/bin/env python3
"""results_kit: the parts every probatio results page shares (docs: the results-pages ruling, P1 and P2).

A results page is one self-contained file beside the text page it views (results.html beside scorecard.md): inline
CSS and script, a system font stack, a Content-Security-Policy that forbids the network, readable without scripts.
This module holds what every kind's page draws with, so every page has the same grammar (head, run line, chips,
teaser, figures, the highlighted box, the limits lists, the foot) and the same look:

    words           SHOW_NUMBERS, SETUP, DIAGNOSTICS, EVEN_NOTE, PAGE_IS_VIEW: the structural words every page uses
    CSP, COLOURS    the page's policy, and the one home of its colours (the stylesheet's custom properties are made
                    from COLOURS; the drawing data names a colour by its key)
    escaping        text() for the markup (probatio's display guard first, then HTML escaping), data_block() for
                    the inert JSON the script reads
    markup          attrs, tag, para, items, table, numbers, setup, figure, after; pills, teaser_panel, takeaway,
                    identity_table
    drawing data    pos, bar, stack, state, tinted, row: what the script draws, every printed text the text page's own
    the shell       document(title, main, figures, extra_style="", extra_script=None), as_bytes(page)
    STYLE, SCRIPT   the stylesheet and the base script (bars with intervals, stacked bars, status words, tabs,
                    legends, hovers, the teaser's small bars); a page that needs another drawing adds a second
                    script and stylesheet through document's extra_* and never edits these, which are part of every
                    page's bytes

Each kind's page is a thin module over this kit: versus_html.py (a head-to-head run), single_html.py (a single
target). Every number and every sentence that states a result is the kind's text page's own, handed in by its
builder; the kit formats no number and writes no result sentence. It imports probatio's display guard and export
filter and nothing else of probatio's modules. Standard library only, Python 3.9 or later. No network, no model, no
file read or written.
"""
import html
import json
import re

from probatio import display, export_text

# ---- the words every page uses: structure only, never a result -------------------------------------------------------
PAGE_IS_VIEW = ("This page is a view of scorecard.json, the run's card, in the same folder; scorecard.md is the same "
                "card as text.")
SHOW_NUMBERS = "Show the numbers"
SETUP = "Setup"
DIAGNOSTICS = "Diagnostics"
EVEN_NOTE = "50 = even"
FONT = "system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"   # ordo.FONT, the figures' own stack:
# copied, not imported, so a page never depends on ordo (a test replaces ordo while a run is scored); a test checks they
# are the same
CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:"

# ---- the colours: one home. The stylesheet's custom properties are made from this table, and the drawing data names
# a colour by its key, never by its value --------------------------------------------------------------------------------
COLOURS = {
    "accent": "#1f6f9e", "accent-light": "#9cc3da", "orange": "#c25a12", "orange-light": "#e8b48f",
    "charcoal": "#2e3338", "charcoal-light": "#a9aeb4", "lottery": "#7d848c",
    "ink": "#222222", "ink-soft": "#444b52", "ink-faint": "#5c636c", "bg": "#ffffff", "bg-soft": "#edf5fa",
    "hl": "#d3e7f3", "frame": "#c9ced5", "rule": "#dde5ea", "track": "#eef0f3", "even": "#8b929b",
    "tab-ink": "#6a717a", "tab-rule": "#e1e4e8", "cell-rule": "#eceef1", "chip": "#19506c",
}


# ---- escaping: one home for the markup, one for the data ----------------------------------------------------------------
def text(s):
    """Any string bound for the markup: versus_page's display guard (every control, format or invisible character
    written as its escape, uncut, so a line the text page built keeps every part), then HTML-escaped, quotes
    included. The one way text reaches the page."""
    return html.escape(display(s, cap=None), quote=True)


def shown_strings(value):
    """value with every string in it through the display guard (uncut), for the data block."""
    if isinstance(value, str):
        return display(value, cap=None)
    if isinstance(value, dict):
        return {k: shown_strings(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [shown_strings(v) for v in value]
    return value


def data_block(figures):
    """The embedded data as the page holds it: JSON (keys sorted, ASCII only), every string displayed first, and "<",
    ">", "&" written as \\u escapes, so the text inside the script element can never close it or open a comment, and
    U+2028 and U+2029 cannot end a line."""
    raw = json.dumps(shown_strings(figures), sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                     allow_nan=False)
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def attrs(**kw):
    """HTML attributes from keyword arguments (cls for class, data_x for data-x), each value through text(); a value
    of None leaves the attribute out, True writes it bare."""
    out = []
    for k, v in kw.items():
        if v is None or v is False:
            continue
        k = "class" if k == "cls" else k.replace("_", "-")
        out.append(f" {k}" if v is True else f' {k}="{text(v)}"')
    return "".join(out)


def tag(name, body="", **kw):
    """One element: body is markup already (built from text() and tag())."""
    return f"<{name}{attrs(**kw)}>{body}</{name}>"


def para(s, **kw):
    return tag("p", text(s), **kw)


def items(lines, **kw):
    """A list of plain lines."""
    return tag("ul", "".join(tag("li", text(s)) for s in lines), **kw)


def table(head, rows, caption=None, **kw):
    """A plain table in a box that scrolls on its own when wide: head is a list of header texts, rows lists of cell
    texts (each through text()); the first cell of a row is its header."""
    cap = tag("caption", text(caption)) if caption else ""
    thead = tag("thead", tag("tr", "".join(tag("th", text(h), scope="col") for h in head))) if head else ""
    body = "".join(tag("tr", tag("th", text(r[0]), scope="row") + "".join(
        tag("td", text(c), cls="n" if NUMBER_CELL.fullmatch(c) else None) for c in r[1:])) for r in rows)
    return tag("div", tag("table", cap + thead + tag("tbody", body), **kw), cls="scroll")


NUMBER_CELL = re.compile(r"[-+]?\d[\d.]*%?( \[[-+\d, ]+\])?( / \d+)?")     # a cell that keeps to one line


def numbers(*tables):
    """A figure's numbers, open: visible with scripts off; the script folds them under SHOW_NUMBERS."""
    return tag("details", tag("summary", text(SHOW_NUMBERS)) + "".join(tables), cls="numbers", open=True)


def setup(lines):
    """A figure's setup note, one paragraph per line, behind a "Setup" button that works without scripts."""
    return tag("details", tag("summary", text(SETUP)) + tag("div", "".join(para(s) for s in lines),
                                                            cls="fig-setup-body"), cls="fig-setup")


def figure(fig_id, title, subtitle, setup_lines, tables, notes=(), tabs=False):
    """One figure card: its setup note, its short title and the text page's line for what it measures (subtitle,
    none when the tab's own panels carry it), its tabs, legend and chart (drawn by the script from
    figures[fig_id]), the notes the text page prints beside it, and its numbers."""
    body = (setup(setup_lines) if setup_lines else "") + tag("div", text(title), cls="fig-title")
    body += para(subtitle, cls="fig-sub") if subtitle else ""
    body += (tag("div", "", cls="tabs", role="tablist") if tabs else "") + tag("div", "", cls="legend")
    body += tag("div", "", cls="chart")
    body += "".join(para(s, cls="fig-note") for s in notes) + numbers(*tables)
    return tag("figure", tag("div", body, cls="fig-card", id=fig_id, data_fig=fig_id))


def after(prefix, line):
    """The rest of a text-page line after its known prefix (the page's own builder wrote both)."""
    if not line.startswith(prefix):
        raise ValueError("a text-page line does not start as its builder writes it")
    return line[len(prefix):]


def pills(chips):
    """The head's chips: each a pill, in the order given."""
    return tag("div", "".join(tag("span", text(c), cls="pill") for c in chips), cls="pills")


def teaser_panel(step, task, numbers_markup, fig, line):
    """One teaser panel: its step (e.g. "Class I"), its task word, the numbers' markup, the small bars the script
    draws from figures[fig], and its one line."""
    return tag("div", tag("div", text(step), cls="tz-step") + tag("div", text(task), cls="tz-task")
               + numbers_markup + tag("div", "", cls="tz-vis", data_fig=fig) + tag("div", text(line), cls="tz-what"),
               cls="tz-panel")


def teaser(panels):
    """The teaser: its panels side by side (one under another on a phone)."""
    return tag("figure", tag("div", "".join(panels), cls="teaser"), cls="teaser-fig")


def takeaway(title, lines):
    """The highlighted box under its heading: the lines the kind's text page gives as its verdicts, as a list."""
    return tag("h2", text(title)) + tag("div", items(lines), cls="takeaway")


def identity_table(items_, trust=None, summary="What this card's identity holds"):
    """The foot's fold: what the card's identity holds as a small table of (label, value), then the card's own trust
    sentence when it has one."""
    ident = table(["", ""], [[label, str(v)] for label, v in items_], cls="mini")
    return tag("details", tag("summary", text(summary)) + ident + (para(trust) if trust else ""), cls="identity")


# ---- number formatting: versus_page's, plus the drawing's geometry --------------------------------------------------------
def pos(x):
    """A value on the 0 to 100 drawing scale: clipped, to two decimals. Geometry only: no printed number is made here."""
    return round(max(0.0, min(100.0, float(x))), 2)


def bar(value, interval, label, short, tips=()):
    """One drawn bar: its value and interval on the drawing scale, the text the page prints for it (label: the text
    page's own formatting), the rounded value written at its end (short) and the lines its hover adds."""
    lo, hi = (pos(interval[0]), pos(interval[1])) if interval else (None, None)
    return {"x": pos(value), "lo": lo, "hi": hi, "text": label, "short": short, "tip": list(tips)}


def stack(parts, total):
    """A stacked bar's segments, the one home of a stack's geometry: parts are (count, colour, text) in drawing order
    and total the denominator their shares use. Each segment runs from one boundary to the next (lo, hi), a boundary
    being the running count's share of the scale, round(100 * (c_1 + ... + c_i) / total, 2) through pos, the first
    from 0. Rounding the boundaries, not the lengths, makes the segments tile exactly and the stack end at the
    counts' whole share, 100 when they sum to total, never past it; a segment's length may differ from its own
    share rounded alone by 0.01, which nothing printed states. The script only places them; text is the page's."""
    segments, running, lo = [], 0, 0.0
    for count, colour, words in parts:
        running += count
        hi = pos(100 * running / total)
        segments.append({"lo": lo, "hi": hi, "colour": colour, "text": words})
        lo = hi
    return segments


def state(words):
    """A number drawn as its status words, never as a bar."""
    return {"state": words}


def tinted(mark, colour, em=False):
    """A bar in its own colour (em: its number in bold); a status mark stays its words alone."""
    return mark if "state" in mark else dict(mark, colour=colour, **({"em": True} if em else {}))


def row(label, marks, sub=(), notes=()):
    """One row of a bar chart: its label, small lines under the label, one mark per series, lines under the row."""
    return {"label": label, "sub": list(sub), "marks": marks, "notes": [n for n in notes if n]}


# ---- the shell --------------------------------------------------------------------------------------------------------
def document(title, main, figures, extra_style="", extra_script=None):
    """The whole file: doctype, the CSP, the viewport, the inline stylesheet, the body, the data block, the script.
    extra_style is added after the stylesheet and extra_script as a second script element after the base one, for a
    page that draws something the base script does not; with neither, every page is exactly the base shell."""
    return "\n".join([
        "<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{text(CSP)}">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="referrer" content="no-referrer">', '<link rel="icon" href="data:,">',
        f"<title>{text(title)}</title>", f"<style>{style()}{extra_style}</style>", "</head>", "<body>",
        f'<main class="wrap sl">{main}</main>', '<div id="tip" role="tooltip" hidden></div>',
        f'<script type="application/json" id="data">{data_block(figures)}</script>', f"<script>{SCRIPT}</script>",
        *([f"<script>{extra_script}</script>"] if extra_script else []), "</body>", "</html>", ""])


def as_bytes(page):
    """A page's bytes: through probatio's export filter (an ordinary page comes through unchanged), UTF-8."""
    return export_text(page).encode("utf-8")


# ---- the stylesheet: the reference post's components (loyalty-wording-study's post_body), its colours from COLOURS --------
def style():
    """The inline stylesheet: the custom properties from COLOURS and the font stacks, then STYLE."""
    props = "".join(f"--{k}:{v};" for k, v in COLOURS.items())
    return (f":root{{{props}--sans:{FONT};--mono:ui-monospace,'SF Mono',Menlo,Consolas,monospace;"
            "color-scheme:light}" + STYLE)


STYLE = """
*{box-sizing:border-box}
html{font-size:17px;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.55;overflow-x:hidden}
.wrap{max-width:calc(820px + 2.8rem);margin:0 auto;padding:1.6rem 1.4rem 3rem}
h1{margin:0 0 .5rem;font-size:2.1rem;line-height:1.15;font-weight:700;letter-spacing:-.01em;overflow-wrap:anywhere}
h2{margin:2.4rem 0 .8rem;padding-bottom:.45rem;font-size:1.55rem;font-weight:300;line-height:1.2;
  border-bottom:2px solid var(--hl)}
h3{margin:1.8rem 0 .6rem;font-size:1.2rem;font-weight:600}
h4{margin:1.2rem 0 .4rem;font-size:1rem;font-weight:600;overflow-wrap:anywhere}
p{margin:0 0 1rem;overflow-wrap:anywhere}
ul{margin:0 0 1rem;padding-left:1.2rem}
li{margin:0 0 .4rem;overflow-wrap:anywhere}
.fine{font-size:.86rem;color:var(--ink-faint)}
.lede{font-size:1.08rem}
.head .runline{margin:0 0 .3rem;font-size:.95rem;color:var(--ink-soft)}
.head .headline{margin:0 0 .3rem;font-size:.9rem;color:var(--ink-faint)}
.pills{display:flex;flex-wrap:wrap;gap:6px 8px;margin:.7rem 0 1.4rem}
.pill{padding:2px 10px;font-size:.8rem;font-weight:600;color:var(--chip);background:var(--bg-soft);
  border:1px solid var(--hl);border-radius:999px}
.sl .takeaway{margin:16px 0 24px;padding:12px 16px 4px;background:var(--hl);border:2px solid var(--accent);
  border-radius:10px}
.sl figure{position:relative;margin:1.6rem 0 2.2rem}
.sl .fig-card{position:relative;padding:16px 18px 18px;background:#fff;border:1px solid var(--frame)}
.sl .fig-title{margin:0 110px 2px 0;font-size:17px;font-weight:700;line-height:1.3;color:var(--ink)}
.sl .fig-sub{margin:0 110px 10px 0;font-size:14.5px;line-height:1.4;color:var(--ink-soft)}
.sl .fig-note{margin:10px 0 0;font-size:14px;color:var(--ink-soft)}
.sl .legend{display:flex;flex-wrap:wrap;gap:4px 18px;margin:6px 0 14px;font-size:14px}
.sl .legend:empty{display:none}
.sl .chip{display:inline-block;width:12px;height:12px;margin-right:6px;vertical-align:-1px}
.sl .tabs{display:flex;flex-wrap:wrap;gap:6px 22px;margin:6px 110px 12px 0;border-bottom:1px solid var(--tab-rule)}
.sl .tabs:empty{display:none}
.sl .tab{padding:6px 1px;font-family:var(--sans);font-size:15px;font-weight:600;color:var(--tab-ink);text-align:left;
  background:none;border:0;border-bottom:3px solid var(--tab-rule);cursor:pointer}
.sl .tab:hover{color:var(--ink)}
.sl .tab[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--accent)}
.sl .bars{display:grid;grid-template-columns:minmax(110px,210px) minmax(0,1fr) 46px;column-gap:14px;row-gap:12px;
  align-items:center}
.sl .bars .lab{font-size:15px;line-height:1.25;overflow-wrap:anywhere}
.sl .bars .lab small{display:block;font-size:13px;color:var(--ink-faint)}
.sl .set{position:relative;display:flex;flex-direction:column;gap:3px}
.sl .track{position:relative;height:12px;background:var(--track)}
.sl .bar{position:absolute;top:0;bottom:0;left:0;border-radius:0 3px 3px 0;
  transition:width .9s cubic-bezier(.2,.7,.2,1),left .9s cubic-bezier(.2,.7,.2,1)}
.sl .seg{position:absolute;top:0;bottom:0}
.sl .state{font-size:13.5px;font-style:italic;line-height:1.3;color:var(--ink-faint);overflow-wrap:anywhere}
.sl .even{position:absolute;top:-3px;bottom:-3px;width:0;border-left:1px dashed var(--even);z-index:1}
.sl .ci{position:absolute;top:5px;height:2px;background:var(--charcoal);z-index:2}
.sl .cap{position:absolute;top:2px;width:1.5px;height:8px;margin-left:-.75px;background:var(--charcoal);z-index:2}
.sl .zero{position:absolute;top:50%;width:6px;height:6px;margin:-3px 0 0 -3px;border-radius:50%;z-index:1}
.sl .vals{display:flex;flex-direction:column;gap:3px;font-size:13.5px;font-weight:600;line-height:12px;text-align:right;
  font-variant-numeric:tabular-nums;color:var(--ink-soft)}
.sl .vals .em{font-weight:700;color:var(--ink)}
.sl .vals span{height:12px;white-space:nowrap}
.sl .rownote{grid-column:2 / span 2;margin-top:-6px;font-size:13px;line-height:1.35;color:var(--ink-faint);
  overflow-wrap:anywhere}
.sl .panel + .panel{margin-top:22px}
.sl .panel-title{margin:0 0 10px;font-size:14.5px;font-weight:700;color:var(--ink);overflow-wrap:anywhere}
.sl .panel-lede{margin:0 0 12px;font-size:14.5px;line-height:1.4;color:var(--ink-soft)}
.sl .panel-note{margin:10px 0 0;font-size:13.5px;color:var(--ink-soft)}
.sl .fig-setup{position:absolute;top:12px;right:12px;z-index:20}
.sl .fig-setup > summary{padding:3px 11px;font-size:14px;font-weight:600;color:var(--accent);list-style:none;
  cursor:pointer;background:#fff;border:1.5px solid var(--accent)}
.sl .fig-setup > summary::-webkit-details-marker{display:none}
.sl .fig-setup > summary::before{content:"i";display:inline-block;width:15px;height:15px;margin-right:6px;
  font-family:Georgia,serif;font-size:11px;font-style:italic;font-weight:700;line-height:15px;text-align:center;
  color:#fff;vertical-align:1px;background:var(--accent);border-radius:50%}
.sl .fig-setup[open] > summary{color:#fff;background:var(--accent)}
.sl .fig-setup[open] > summary::before{color:var(--accent);background:#fff}
.sl .fig-setup-body{position:absolute;top:calc(100% + 6px);right:0;width:min(460px,calc(100vw - 48px));padding:12px 16px;
  font-size:14px;line-height:1.5;background:#fff;border:1px solid var(--frame);box-shadow:0 8px 24px rgba(0,0,0,.12)}
.sl .fig-setup-body p{margin:0 0 8px}
.sl .fig-setup-body p:last-child{margin:0}
.sl details.numbers{margin-top:12px;font-size:14px}
.sl details.numbers > summary,.sl details.identity > summary{font-weight:600;color:var(--accent);cursor:pointer}
.scroll{max-width:100%;overflow-x:auto}
.sl table{width:100%;margin-top:8px;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums}
.sl caption{padding:0 8px 6px;font-weight:700;text-align:left;color:var(--ink)}
.sl th,.sl td{padding:4px 8px;text-align:left;vertical-align:top;border-bottom:1px solid var(--cell-rule)}
.sl thead th{font-weight:600;color:var(--ink-soft)}
.sl tbody th{font-weight:400}
.sl table.mini{font-size:13.5px}
.sl td.n{white-space:nowrap}
.sl .scroll + .scroll,.sl .scroll + p{margin-top:18px}
#tip{position:fixed;z-index:60;max-width:300px;padding:8px 11px;font-size:14px;line-height:1.4;pointer-events:none;
  background:#fff;border:1px solid var(--frame);box-shadow:0 8px 24px rgba(0,0,0,.12)}
#tip b{display:block}
.sl .teaser-fig{position:relative;left:50%;width:min(1120px,calc(100vw - 32px));transform:translateX(-50%)}
.sl .teaser{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));column-gap:14px}
.sl .tz-panel{display:flex;flex-direction:column;padding:14px 14px 16px;border:1px solid var(--frame)}
.sl .tz-step{font-size:13px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--ink)}
.sl .tz-task{margin:6px 0 10px;padding:6px 12px;font-size:14.5px;font-weight:500;line-height:1.3;color:var(--ink);
  border:1px solid var(--frame);border-radius:18px;align-self:flex-start}
.sl .tz-vis{display:flex;flex-direction:column;justify-content:center;gap:8px;min-height:24px;margin:0 0 10px}
.sl .tz-vis:empty{display:none}
.sl .mini-row{display:grid;grid-template-columns:minmax(0,1fr) 30px;column-gap:8px;align-items:center}
.sl .mini-row .track{height:10px}
.sl .mini-row .ci{top:4px}
.sl .mini-row .cap{top:1px}
.sl .mini-row .vals span{height:auto;line-height:1}
.sl .mini-lab{grid-column:1 / span 2;font-size:12.5px;line-height:1.2;color:var(--ink-faint);overflow-wrap:anywhere}
.sl .tz-out{display:flex;flex-wrap:wrap;gap:8px 14px;margin:2px 0 12px}
.sl .tz-col{flex:1 1 56px;max-width:100%;overflow-wrap:break-word}
.sl .tz-big{font-size:28px;font-weight:700;line-height:1.05;color:var(--ink);font-variant-numeric:tabular-nums}
.sl .tz-iv{font-size:12.5px;font-weight:600;color:var(--ink-faint);white-space:nowrap}
.sl .tz-state{font-size:13px;font-style:italic;line-height:1.3;color:var(--ink-faint)}
.sl .tz-name{margin-top:3px;font-size:12.5px;font-weight:600;line-height:1.25;color:var(--ink-soft)}
.sl .tz-none{margin:0 0 8px;font-size:14px;font-style:italic;color:var(--ink-faint)}
.sl .tz-what{margin-top:auto;font-size:13px;font-weight:600;line-height:1.35;color:var(--chip)}
.foot{margin-top:2.4rem}
@media (max-width:760px){
  .wrap{padding:1.1rem 16px 2.4rem}
  h1{font-size:1.6rem}
  .sl .teaser-fig{left:0;width:auto;transform:none}
  .sl .teaser{grid-template-columns:minmax(0,1fr);row-gap:12px}
  .sl .bars{grid-template-columns:minmax(0,1fr) 44px;column-gap:10px;row-gap:6px}
  .sl .bars .lab{grid-column:1 / -1;margin-top:6px}
  .sl .rownote{grid-column:1 / -1;margin-top:0}
  .sl .fig-title,.sl .fig-sub,.sl .tabs{margin-right:0}
  .sl .fig-setup{position:static;margin-bottom:8px}
  .sl .fig-setup-body{position:static;width:auto;margin-top:6px}
}
@media (prefers-reduced-motion:reduce){.sl .bar{transition:none}}
"""

# ---- the script: draws the bars, tabs, legends and hovers from the data block; every string as text -----------------------
SCRIPT = r"""
(function () {
  'use strict';
  var D = JSON.parse(document.getElementById('data').textContent);
  var tip = document.getElementById('tip');
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  /* an element with its class and children: strings become text nodes, never markup */
  function el(tag, cls, kids) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    (kids || []).forEach(function (k) { if (k != null) e.append(typeof k === 'string' ? document.createTextNode(k) : k); });
    return e;
  }
  function colour(key) { return 'var(--' + key + ')'; }
  /* the hover: a label and its lines, at the pointer or beside a focused mark */
  function place(x, y) {
    var r = tip.getBoundingClientRect(), pad = 14;
    tip.style.left = (x + pad + r.width > innerWidth - 8 ? Math.max(8, x - r.width - pad) : x + pad) + 'px';
    tip.style.top = (y + pad + r.height > innerHeight - 8 ? Math.max(8, y - r.height - pad) : y + pad) + 'px';
  }
  function hover(node, lines) {
    node.setAttribute('tabindex', '0');
    function show(x, y) {
      tip.replaceChildren(el('b', '', [lines[0]]));
      lines.slice(1).forEach(function (l) { tip.append(el('div', '', [l])); });
      tip.hidden = false; place(x, y);
    }
    node.addEventListener('mouseenter', function (e) { show(e.clientX, e.clientY); });
    node.addEventListener('mousemove', function (e) { place(e.clientX, e.clientY); });
    node.addEventListener('mouseleave', function () { tip.hidden = true; });
    node.addEventListener('focus', function () { var r = node.getBoundingClientRect(); show(r.right, r.top); });
    node.addEventListener('blur', function () { tip.hidden = true; });
  }
  /* grow the bars once they scroll into view (at once with reduced motion or no observer) */
  function whenSeen(node, fn) {
    if (still || !('IntersectionObserver' in window)) return fn();
    var io = new IntersectionObserver(function (es) {
      if (es.some(function (e) { return e.isIntersecting; })) { io.disconnect(); fn(); }
    }, { threshold: 0.2 });
    io.observe(node);
  }
  /* a mark centred on a point of the track (a cap, or the dot of a bar of 0): it marks the point, never a length */
  function point(t, cls, x, paint) {
    var p = el('div', cls); p.style.left = x + '%';
    if (paint) p.style.background = paint;
    t.append(p);
  }
  /* an interval: its line exactly from lo to hi, and a cap centred on each endpoint (one cap when they meet) */
  function interval(t, lo, hi) {
    var w = el('div', 'ci');
    w.style.left = lo + '%'; w.style.width = 'calc(' + hi + '% - ' + lo + '%)';
    t.append(w);
    point(t, 'cap', lo);
    if (hi !== lo) point(t, 'cap', hi);
  }
  /* one mark: a bar with its interval, a stacked bar, or the number's status words */
  function track(m, s, label, grow) {
    if (m.state != null) return el('div', 'state', [m.state]);
    var t = el('div', 'track');
    if (m.stack) {                                  /* each segment from its boundary to the next (stack) */
      m.stack.forEach(function (p) {
        var seg = el('div', 'seg');
        seg.style.left = p.lo + '%'; seg.style.width = 'calc(' + p.hi + '% - ' + p.lo + '%)';
        seg.style.background = colour(p.colour); t.append(seg);
      });
      hover(t, [label].concat(m.stack.map(function (p) { return p.text; })));
      return t;
    }
    var b = el('div', 'bar'), paint = colour(m.colour || s.colour);
    b.style.background = paint; b.style.width = still ? m.x + '%' : '0%';
    t.append(b);
    if (m.x === 0) point(t, 'zero', 0, paint);
    if (m.lo != null) interval(t, m.lo, m.hi);
    grow.push(function () { b.style.width = m.x + '%'; });
    hover(t, [label].concat(s.label ? [s.label + ': ' + m.text] : [m.text], m.tip || []));
    return t;
  }
  function bars(box, rows, series, even) {
    var grid = el('div', 'bars'), grow = [];
    rows.forEach(function (r) {
      var set = el('div', 'set'), vals = el('div', 'vals');
      r.marks.forEach(function (m, i) {
        set.append(track(m, series[i] || series[0], r.label, grow));
        vals.append(el('span', m.em ? 'em' : '', [m.state != null || m.stack ? '' : m.short]));
      });
      if (even != null) { var ev = el('div', 'even'); ev.style.left = even + '%'; set.append(ev); }
      var lab = el('div', 'lab', [r.label].concat(r.sub.map(function (s) { return el('small', '', [s]); })));
      grid.append(lab, set, vals);
      r.notes.forEach(function (n) { grid.append(el('div', 'rownote', [n])); });
    });
    box.replaceChildren(grid);
    whenSeen(grid, function () { grow.forEach(function (f) { f(); }); });
  }
  function legend(box, series) {
    var named = series.filter(function (s) { return s.label; });
    box.replaceChildren.apply(box, named.length < 2 ? [] : named.map(function (s) {
      var c = el('span', 'chip'); c.style.background = colour(s.colour);
      return el('span', '', [c, s.label]);
    }));
  }
  function drawTab(fig, spec, tab) {
    var chart = fig.querySelector('.chart');
    chart.replaceChildren();
    tab.panels.forEach(function (p) {
      var box = el('div', '');
      var panel = el('div', 'panel', [p.lede ? el('p', 'panel-lede', [p.lede]) : null,
        p.title ? el('div', 'panel-title', [p.title]) : null, box]);
      chart.append(panel);
      bars(box, p.rows, spec.series, spec.even);
      (p.notes || []).forEach(function (n) { panel.append(el('p', 'panel-note', [n])); });
    });
  }
  function figure(fig, spec) {
    legend(fig.querySelector('.legend'), spec.series);
    var box = fig.querySelector('.tabs');
    if (box && spec.tabs.length > 1) {
      spec.tabs.forEach(function (t, i) {
        var b = el('button', 'tab', [t.label]);
        b.setAttribute('role', 'tab'); b.setAttribute('aria-selected', String(i === 0)); b.type = 'button';
        b.addEventListener('click', function () {
          box.querySelectorAll('.tab').forEach(function (x) { x.setAttribute('aria-selected', String(x === b)); });
          drawTab(fig, spec, t);
        });
        box.append(b);
      });
    }
    drawTab(fig, spec, spec.tabs[0]);
  }
  /* the teaser's small bars: each name's number against the even line */
  function mini(box, spec) {
    var grow = [];
    spec.rows.forEach(function (r) {
      var m = r.marks[0];
      if (m.state != null) return;
      var set = el('div', 'set', [track(m, spec.series[0], r.label, grow)]);
      var ev = el('div', 'even'); ev.style.left = spec.even + '%'; set.append(ev);
      box.append(el('div', 'mini-row', [el('div', 'mini-lab', [r.label]), set,
        el('div', 'vals', [el('span', '', [m.short])])]));
    });
    whenSeen(box, function () { grow.forEach(function (f) { f(); }); });
  }
  document.querySelectorAll('[data-fig]').forEach(function (node) {
    var spec = D[node.getAttribute('data-fig')];
    if (!spec) return;
    if (spec.kind === 'mini') mini(node, spec); else figure(node, spec);
  });
  document.querySelectorAll('details.numbers').forEach(function (d) { d.open = false; });
})();
"""
