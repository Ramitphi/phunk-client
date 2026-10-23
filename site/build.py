"""Build the phunk-client microsite.

    python site/build.py            # writes site/_site/
    python site/build.py --serve    # builds, then serves on http://localhost:8000

Standard library only. Sources:

- docs/TUTORIAL.md          -> tutorial.html (steps 1-8) and writing.html (step 9)
- site/content/*.md and site/content/concepts/*.md -> one page each

The Markdown dialect is the small subset the docs use: headings, paragraphs,
lists, tables, fenced code, block quotes, inline code, links, bold and italic.
Two extras: `::: name` ... `:::` wraps blocks in <div class="name">, and lines
starting with `<` pass through as raw HTML.

The build fails if an internal link or #anchor points nowhere. It prints a
warning for every PLACEHOLDER left in the output.
"""

from __future__ import annotations

import html
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
CONTENT = SITE / "content"
STATIC = SITE / "static"
OUT = SITE / "_site"

REPO = "https://github.com/Ramitphi/phunk-client"
BLOB = f"{REPO}/blob/main"


# --------------------------------------------------------------------------
# Inline Markdown
# --------------------------------------------------------------------------

def slugify(text: str) -> str:
    """GitHub-style heading slug, so links into docs/TUTORIAL.md keep working."""
    text = re.sub(r"<[^>]+>", "", text).lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def strip_inline(md: str) -> str:
    """Markdown heading text -> plain text."""
    md = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md)
    return re.sub(r"[`*]", "", md)


def inline(text: str, link) -> str:
    codes: list[str] = []

    def keep_code(m):
        codes.append(f"<code>{html.escape(m.group(2), quote=False)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"(`+)(.+?)\1", keep_code, text)
    text = html.escape(text, quote=False)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: f'<a href="{html.escape(link(m.group(2)))}">{m.group(1)}</a>',
        text,
    )
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*(?![\s*])(.+?)(?<![\s*])\*(?![*\w])", r"<em>\1</em>", text)
    text = text.replace("\\|", "|")
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


# --------------------------------------------------------------------------
# Code highlighting (comments, strings, keywords, numbers; nothing fancier)
# --------------------------------------------------------------------------

_STR = r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\''
_RULES = {
    "python": [
        ("c", r"#[^\n]*"),
        ("s", _STR),
        ("k", r"\b(?:async|await|def|for|in|if|elif|else|not|or|and|is|import|from|as|with|"
              r"return|while|break|yield|lambda|class|True|False|None)\b"),
        ("n", r"\b\d[\d_]*\b"),
    ],
    "sh": [
        ("c", r"(?:(?<=\s)|^)#[^\n]*"),
        ("s", _STR),
    ],
    "json": [
        ("s", r'"(?:\\.|[^"\\\n])*"'),
        ("n", r"-?\b\d+(?:\.\d+)?\b"),
        ("k", r"\b(?:true|false|null)\b"),
    ],
    "solidity": [
        ("c", r"//[^\n]*"),
        ("k", r"\b(?:struct|uint256|address|bytes|function|returns)\b"),
    ],
}
_RULES["bash"] = _RULES["sh"]
_RULES["py"] = _RULES["python"]


def highlight(code: str, lang: str) -> str:
    rules = _RULES.get(lang)
    if not rules:
        out = html.escape(code, quote=False)
    else:
        pattern = re.compile("|".join(f"(?P<{n}{i}>{r})" for i, (n, r) in enumerate(rules)), re.M)
        out, pos = [], 0
        for m in pattern.finditer(code):
            out.append(html.escape(code[pos:m.start()], quote=False))
            cls = m.lastgroup.rstrip("0123456789")
            out.append(f'<span class="t-{cls}">{html.escape(m.group(), quote=False)}</span>')
            pos = m.end()
        out.append(html.escape(code[pos:], quote=False))
        out = "".join(out)
    # Make unfinished commands impossible to miss.
    return re.sub(r"PLACEHOLDER[^\n<]*", lambda m: f'<mark class="ph">{m.group()}</mark>', out)


# --------------------------------------------------------------------------
# Block Markdown
# --------------------------------------------------------------------------

@dataclass
class Heading:
    level: int
    text: str
    id: str


@dataclass
class Doc:
    html: str
    headings: list[Heading] = field(default_factory=list)


_ITEM = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.*)$")


def _is_block_start(line: str) -> bool:
    s = line.lstrip()
    return bool(
        s.startswith(("```", "#", ":::", "> ", "|"))
        or _ITEM.match(line)
        or re.fullmatch(r"(-{3,}|\*{3,})", s)
    )


class Renderer:
    def __init__(self, link, ids: dict[str, int] | None = None):
        self.link = link
        self.ids = ids if ids is not None else {}
        self.headings: list[Heading] = []

    def unique(self, slug: str) -> str:
        n = self.ids.get(slug, 0)
        self.ids[slug] = n + 1
        return slug if n == 0 else f"{slug}-{n}"

    def render(self, text: str) -> str:
        return self.blocks(text.split("\n"))

    def blocks(self, lines: list[str]) -> str:
        out: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            s = line.strip()

            if not s:
                i += 1

            elif s.startswith("```"):
                lang = s[3:].strip()
                j = i + 1
                while j < len(lines) and not lines[j].strip().startswith("```"):
                    j += 1
                indent = len(line) - len(line.lstrip())
                code = "\n".join(l[indent:] if not l[:indent].strip() else l.lstrip()
                                 for l in lines[i + 1:j])
                cls = f' class="lang-{lang}"' if lang else ""
                out.append(f"<pre{cls}><code>{highlight(code, lang)}</code></pre>")
                i = j + 1

            elif s.startswith(":::") and s != ":::":
                name, _, title = s[3:].strip().partition(" ")
                depth, j = 1, i + 1
                while j < len(lines):
                    t = lines[j].strip()
                    if t == ":::":
                        depth -= 1
                        if depth == 0:
                            break
                    elif t.startswith(":::"):
                        depth += 1
                    j += 1
                inner = self.blocks(lines[i + 1:j])
                head = f'<p class="box-title">{inline(title, self.link)}</p>' if title else ""
                out.append(f'<div class="{html.escape(name)}">{head}{inner}</div>')
                i = j + 1

            elif m := re.match(r"^(#{1,6})\s+(.*)$", s):
                level, text = len(m.group(1)), m.group(2).strip()
                hid = self.unique(slugify(strip_inline(text)))
                self.headings.append(Heading(level, strip_inline(text), hid))
                anchor = f'<a class="anchor" href="#{hid}" aria-label="Link to this section">#</a>'
                out.append(f'<h{level} id="{hid}">{inline(text, self.link)}{anchor if level > 1 else ""}</h{level}>')
                i += 1

            elif re.fullmatch(r"(-{3,}|\*{3,})", s):
                out.append("<hr>")
                i += 1

            elif s.startswith("<") and not s.startswith("<http"):
                j = i
                while j < len(lines) and lines[j].strip():
                    j += 1
                out.append("\n".join(lines[i:j]))
                i = j

            elif s.startswith(">"):
                j = i
                while j < len(lines) and lines[j].strip().startswith(">"):
                    j += 1
                inner = [re.sub(r"^\s*>\s?", "", l) for l in lines[i:j]]
                out.append(f"<blockquote>{self.blocks(inner)}</blockquote>")
                i = j

            elif s.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
                def cells(row: str) -> list[str]:
                    row = row.strip().strip("|")
                    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]
                head = cells(line)
                j = i + 2
                rows = []
                while j < len(lines) and lines[j].strip().startswith("|"):
                    rows.append(cells(lines[j]))
                    j += 1
                th = "".join(f"<th>{inline(c, self.link)}</th>" for c in head)
                body = "".join(
                    "<tr>" + "".join(f"<td>{inline(c, self.link)}</td>" for c in r) + "</tr>"
                    for r in rows
                )
                out.append(f'<div class="table"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>')
                i = j

            elif m := _ITEM.match(line):
                ordered = m.group(2)[0].isdigit()
                base = len(m.group(1))
                items: list[list[str]] = []
                j = i
                while j < len(lines):
                    l = lines[j]
                    im = _ITEM.match(l)
                    if im and len(im.group(1)) == base and im.group(2)[0].isdigit() == ordered:
                        items.append([im.group(3)])
                    elif not l.strip():
                        # A blank line ends the list unless the next line is indented
                        # (continuation) or another item at this level.
                        nxt = lines[j + 1] if j + 1 < len(lines) else ""
                        nm = _ITEM.match(nxt)
                        indented = nxt[:base + 2].isspace() and nxt.strip()
                        if not (indented or (nm and len(nm.group(1)) == base)):
                            break
                        items[-1].append("")
                    elif l[:base + 2].isspace() or not _is_block_start(l):
                        items[-1].append(l[base + 2:] if l[:base + 2].isspace() else l.strip())
                    else:
                        break
                    j += 1
                lis = []
                for body in items:
                    inner = self.blocks(body)
                    if "\n" not in inner and inner.startswith("<p>") and inner.count("<p>") == 1:
                        inner = inner[3:-4]
                    elif inner.startswith("<p>") and inner.count("<p>") == 1:
                        inner = inner.replace("<p>", "", 1).replace("</p>", "", 1)
                    lis.append(f"<li>{inner}</li>")
                start = m.group(2)[:-1] if ordered else ""
                tag = "ol" if ordered else "ul"
                attr = f' start="{start}"' if ordered and start != "1" else ""
                out.append(f"<{tag}{attr}>{''.join(lis)}</{tag}>")
                i = j

            else:
                j = i + 1
                while j < len(lines) and lines[j].strip() and not _is_block_start(lines[j]):
                    j += 1
                para = " ".join(l.strip() for l in lines[i:j])
                out.append(f"<p>{inline(para, self.link)}</p>")
                i = j
        return "\n".join(out)


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

@dataclass
class Page:
    out: str                  # output path, relative to _site, e.g. "concepts/fids.html"
    source: Path              # Markdown source
    meta: dict
    body_md: str
    title: str = ""
    summary: str = ""
    html: str = ""
    headings: list[Heading] = field(default_factory=list)


def read_page(src: Path, out: str) -> Page:
    text = src.read_text(encoding="utf-8")
    meta: dict = {}
    if text.startswith("---\n"):
        head, _, text = text[4:].partition("\n---\n")
        for line in head.splitlines():
            k, _, v = line.partition(":")
            if k.strip():
                meta[k.strip()] = v.strip()
    return Page(out=out, source=src, meta=meta, body_md=text,
                title=meta.get("title", ""), summary=meta.get("summary", ""))


def collect_pages() -> list[Page]:
    pages = [read_page(CONTENT / "index.md", "index.html"),
             # The quickstart in Chinese. The tutorial and concepts stay in English.
             read_page(CONTENT / "zh" / "index.md", "zh/index.html")]

    # docs/TUTORIAL.md is one file on GitHub. On the site, its writing chapter
    # (the "## 10. Writing..." section) gets a page of its own.
    src = ROOT / "docs" / "TUTORIAL.md"
    text = src.read_text(encoding="utf-8")
    m = re.search(rf"(?m)^## {re.escape(WRITING_HEADING)}.*\n", text)
    if not m:
        raise SystemExit(f"build: no '## {WRITING_HEADING}' chapter in docs/TUTORIAL.md")
    nxt = re.search(r"(?m)^## ", text[m.end():])
    end = m.end() + nxt.start() if nxt else len(text)
    reading_md = text[:m.start()] + text[end:]
    writing_md = re.sub(r"(?m)^###(?= )", "##", text[m.end():end])   # its h3s become sections

    common = {"toc": "yes", "edit": "docs/TUTORIAL.md"}
    pages.append(Page(
        # Steps 1-8 only, so the site title drops "and writing".
        out="tutorial.html", source=src, meta={**common, "lede": "no"}, body_md=reading_md,
        title="Reading Farcaster through a node",
        summary="From one curl to working out which app signed each cast. Eight short steps.",
    ))
    pages.append(Page(
        # A made-up source path keeps the two pages apart in the link map.
        out="writing.html", source=src.with_name("TUTORIAL.md#writing"), meta=dict(common),
        body_md=writing_md, title="Writing to the network",
        summary="Add a gasless app key, then post, like and follow through your own node. "
                "Step 9 of the tutorial.",
    ))

    pages.append(read_page(CONTENT / "concepts" / "index.md", "concepts/index.html"))
    concepts = [read_page(p, f"concepts/{p.stem}.html")
                for p in sorted((CONTENT / "concepts").glob("*.md")) if p.stem != "index"]
    concepts.sort(key=lambda p: int(p.meta.get("order", 99)))
    pages.extend(concepts)
    return pages


def rel(from_out: str, to_out: str) -> str:
    """Relative URL between two output paths, so the site works under any base path."""
    depth = from_out.count("/")
    return "../" * depth + to_out


def make_linker(page: Page, by_source: dict[Path, str]):
    def link(url: str) -> str:
        if re.match(r"^[a-z]+:", url) or url.startswith("#"):
            return url
        path, _, frag = url.partition("#")
        frag = f"#{frag}" if frag else ""
        if path.startswith("/"):                        # site-absolute: /concepts/fids.html
            return rel(page.out, path.lstrip("/")) + frag
        target = (page.source.parent / path).resolve()
        if target in by_source:                         # another page we build
            return rel(page.out, by_source[target]) + frag
        if target.is_relative_to(ROOT) and target.exists():
            return f"{BLOB}/{target.relative_to(ROOT).as_posix()}{frag}"
        return url
    return link


# Short "background reading" notes added under tutorial headings. They live
# here, not in TUTORIAL.md, so the Markdown still reads well on GitHub.
TUTORIAL_ASIDES = {
    "1-talk-to-a-node": ["nodes"],
    "2-names-fids-and-profiles": ["fids", "custody"],
    "3-casts-and-farcaster-time": ["messages", "storage"],
    "7-which-app-signed-this": ["app-keys"],
    "8-being-a-good-guest-and-running-your-own-node": ["nodes"],
}


def prepare_tutorial(md: str) -> str:
    # The sidebar is the table of contents; drop the hand-written one.
    md = re.sub(r"(?m)^\d+\. \[[^\]]+\]\(#[^)]+\)\n", "", md)
    # The site has its own title block.
    md = re.sub(r"\A# .*\n", "", md)
    return md


WRITING_HEADING = "10. Writing"

# Background links at the top of the writing page.
WRITING_ASIDES = ["app-keys", "nodes", "seed-phrases"]


def aside_html(slugs: list[str], concepts: dict[str, Page], page: Page) -> str:
    links = "".join(
        f'<a href="{rel(page.out, concepts[s].out)}"><strong>{html.escape(concepts[s].title)}</strong>'
        f'<span>{html.escape(concepts[s].summary)}</span></a>'
        for s in slugs
    )
    return f'<aside class="aside"><p class="aside-label">Background</p>{links}</aside>'


def add_asides(body: str, concepts: dict[str, Page], page: Page) -> str:
    for hid, slugs in TUTORIAL_ASIDES.items():
        aside = aside_html(slugs, concepts, page)
        body, n = re.subn(rf'(<h2 id="{hid}">.*?</h2>)', lambda m: m.group(1) + aside, body, count=1)
        if not n:
            raise SystemExit(f"build: tutorial heading #{hid} not found; update TUTORIAL_ASIDES")
    return body


# --------------------------------------------------------------------------
# Template
# --------------------------------------------------------------------------

NAV = [
    ("index.html", "Quickstart"),
    ("tutorial.html", "Tutorial"),
    ("writing.html", "Writing"),
    ("concepts/index.html", "Concepts"),
]

# Pages with "lang: zh-CN" get this nav and these strings. Their nav still
# leads to the English tutorial and concepts.
NAV_ZH = [
    ("zh/index.html", "快速开始"),
    ("tutorial.html", "教程"),
    ("writing.html", "写入"),
    ("concepts/index.html", "概念"),
]
STRINGS = {
    "en": {"skip": "Skip to content", "license": "MIT licensed. Source and issues on",
           "refs": "Protocol references:", "edit": "Edit this page"},
    "zh-CN": {"skip": "跳到正文", "license": "MIT 许可证。源代码和问题反馈在",
              "refs": "协议参考：", "edit": "编辑此页"},
}

ICON_SUN = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'
ICON_MOON = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>'

TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="{root}favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{root}style.css">{alternate}
<script>try{{var t=localStorage.getItem("theme");if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
<script src="{root}site.js" defer></script>
</head>
<body class="{body_class}">
<a class="skip" href="#main">{s_skip}</a>
<header class="top">
  <div class="top-in">
    <a class="brand" href="{root}index.html"><span class="brand-mark" aria-hidden="true">ϕ</span>phunk-client</a>
    <nav class="nav" aria-label="Site">{nav}</nav>
    <div class="top-tools">{lang_switch}
      <a class="gh" href="{repo}" aria-label="Source on GitHub">GitHub</a>
      <button class="theme" type="button" aria-label="Switch colour theme">{sun}{moon}</button>
    </div>
  </div>
</header>
<div class="layout">
<main id="main">
{intro}
{toc}
<article class="prose">
{body}
</article>
{pager}
</main>
</div>
<footer class="foot">
  <p>{s_license} <a href="{repo}">GitHub</a>.{edit}</p>
  <p>{s_refs} <a href="https://docs.farcaster.xyz">docs.farcaster.xyz</a> and <a href="https://snapchain.farcaster.xyz">snapchain.farcaster.xyz</a>.</p>
</footer>
</body>
</html>
"""


def render_toc(page: Page) -> str:
    items = [h for h in page.headings if h.level in (2, 3)]
    if not items:
        return ""
    lis = "".join(
        f'<li class="l{h.level}"><a href="#{h.id}">{html.escape(h.text)}</a></li>' for h in items
    )
    return (
        '<details class="toc" open><summary>On this page</summary>'
        f'<nav aria-label="On this page"><ol>{lis}</ol></nav></details>'
    )


def is_home_page(page: Page) -> bool:
    """The quickstart, in any language: hero layout, no page header or pager."""
    return page.out == "index.html" or page.meta.get("home") == "yes"


def render_page(page: Page, pages: list[Page]) -> str:
    root = "../" * page.out.count("/")
    section = page.out.split("/")[0]
    def current(href: str) -> str:
        here = href == page.out or ("/" in page.out and href.startswith(section + "/"))
        return ' aria-current="page"' if here else ""

    lang = page.meta.get("lang", "en")
    strings = STRINGS.get(lang, STRINGS["en"])
    nav_items = NAV_ZH if lang.startswith("zh") else NAV
    nav = "".join(f'<a href="{root}{href}"{current(href)}>{label}</a>' for href, label in nav_items)
    is_home = is_home_page(page)

    # A link to the same page in the other language, from "alt_*" front matter.
    alternate = lang_switch = ""
    if page.meta.get("alt_href"):
        href, label, alt_lang = page.meta["alt_href"], page.meta["alt_label"], page.meta["alt_lang"]
        alternate = f'\n<link rel="alternate" hreflang="{alt_lang}" href="{href}">'
        lang_switch = f'\n      <a class="lang" href="{href}" hreflang="{alt_lang}" lang="{alt_lang}">{html.escape(label)}</a>'
    toc = render_toc(page) if page.meta.get("toc") == "yes" else ""
    intro = ""
    if not is_home:
        crumb = ""
        if page.out.startswith("concepts/") and page.out != "concepts/index.html":
            crumb = f'<p class="crumb"><a href="index.html">Concepts</a></p>'
        show = page.summary and page.meta.get("lede") != "no"
        lede = f'<p class="lede">{inline(page.summary, lambda u: u)}</p>' if show else ""
        intro = f'<header class="page-head">{crumb}<h1>{html.escape(page.title)}</h1>{lede}</header>'

    pager = ""
    seq = [p for p in pages if not is_home_page(p) and p.out != "concepts/index.html"]
    if page in seq:
        i = seq.index(page)
        prev_p = seq[i - 1] if i > 0 else None
        next_p = seq[i + 1] if i + 1 < len(seq) else None
        parts = []
        if prev_p:
            parts.append(f'<a class="prev" href="{rel(page.out, prev_p.out)}"><span>Previous</span>{html.escape(prev_p.title)}</a>')
        if next_p:
            parts.append(f'<a class="next" href="{rel(page.out, next_p.out)}"><span>Next</span>{html.escape(next_p.title)}</a>')
        pager = f'<nav class="pager" aria-label="Pages">{"".join(parts)}</nav>'

    edit = ""
    src = page.meta.get("edit") or page.source.relative_to(ROOT).as_posix()
    edit = f' <a href="{REPO}/edit/main/{src}">{strings["edit"]}</a>.'

    if is_home:
        title = page.meta.get("html_title", "phunk-client: read Farcaster from your own node")
    else:
        title = f"{page.title} · phunk-client"
    return TEMPLATE.format(
        title=html.escape(title),
        description=html.escape(page.summary or page.meta.get("description", "")),
        root=root,
        lang=html.escape(lang),
        alternate=alternate,
        lang_switch=lang_switch,
        s_skip=strings["skip"],
        s_license=strings["license"],
        s_refs=strings["refs"],
        body_class=("home" if is_home else "doc") + (" has-toc" if toc else ""),
        nav=nav,
        repo=REPO,
        sun=ICON_SUN,
        moon=ICON_MOON,
        toc=toc,
        intro=intro,
        body=page.html,
        pager=pager,
        edit=edit,
    )


def concept_list(concepts: list[Page], page: Page) -> str:
    lis = "".join(
        f'<li><a href="{rel(page.out, c.out)}"><strong>{html.escape(c.title)}</strong>'
        f'<span>{html.escape(c.summary)}</span></a></li>'
        for c in concepts
    )
    return f'<ul class="cards">{lis}</ul>'


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def check_links(out_dir: Path) -> list[str]:
    errors = []
    ids: dict[Path, set[str]] = {}
    files = sorted(out_dir.rglob("*.html"))
    for f in files:
        ids[f] = set(re.findall(r'\sid="([^"]+)"', f.read_text(encoding="utf-8")))
    for f in files:
        for url in re.findall(r'\s(?:href|src)="([^"]+)"', f.read_text(encoding="utf-8")):
            url = html.unescape(url)
            if re.match(r"^[a-z]+:", url):
                continue
            path, _, frag = url.partition("#")
            target = (f.parent / path).resolve() if path else f
            if not target.exists():
                errors.append(f"{f.relative_to(out_dir)}: broken link {url}")
            elif frag and target.suffix == ".html" and frag not in ids.get(target, set()):
                errors.append(f"{f.relative_to(out_dir)}: missing anchor {url}")
    return errors


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def build() -> None:
    pages = collect_pages()
    by_source = {p.source.resolve(): p.out for p in pages}
    concepts = [p for p in pages if p.out.startswith("concepts/") and p.out != "concepts/index.html"]
    by_slug = {Path(p.out).stem: p for p in concepts}

    for page in pages:
        md = page.body_md
        if page.out == "tutorial.html":
            page.title = page.title or re.match(r"# (.*)", md).group(1)
            md = prepare_tutorial(md)
        else:
            m = re.search(r"(?m)^# (.*)\n", md)
            if m and not is_home_page(page) and page.out != "writing.html":   # these set their own titles
                page.title = page.title or strip_inline(m.group(1))
                md = md.replace(m.group(0), "", 1)
        r = Renderer(make_linker(page, by_source))
        body = r.render(md)
        page.headings = r.headings
        if page.out == "tutorial.html":
            body = add_asides(body, by_slug, page)
        elif page.out == "writing.html":
            body = aside_html(WRITING_ASIDES, by_slug, page) + body
        body = body.replace("<!-- concepts -->", concept_list(concepts, page))
        page.html = body

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for f in STATIC.iterdir():
        shutil.copy2(f, OUT / f.name)
    (OUT / ".nojekyll").write_text("")

    for page in pages:
        dest = OUT / page.out
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(render_page(page, pages), encoding="utf-8")

    errors = check_links(OUT)
    for page in pages:
        text = re.sub(r"<!--.*?-->", "", (OUT / page.out).read_text(encoding="utf-8"), flags=re.S)
        for m in re.finditer(r"PLACEHOLDER[^\n<]*", text):
            print(f"warning: {page.out}: {m.group().strip()}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        raise SystemExit(f"build: {len(errors)} broken link(s)")
    print(f"built {len(pages)} pages into {OUT.relative_to(ROOT)}/")


def main() -> None:
    build()
    if "--serve" in sys.argv:
        import functools
        import http.server
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
        print("serving on http://localhost:8000")
        http.server.ThreadingHTTPServer(("127.0.0.1", 8000), handler).serve_forever()


if __name__ == "__main__":
    main()
