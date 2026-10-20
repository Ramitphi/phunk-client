# The microsite

A static site built from `docs/TUTORIAL.md` and the pages in `site/content/`.
No framework: `build.py` uses the standard library only.

```sh
python site/build.py           # writes site/_site/
python site/build.py --serve   # same, then serves on http://localhost:8000
```

The build fails on a broken internal link or `#anchor`, and prints a warning
for every `PLACEHOLDER` still on the site.

## Where things live

| Path | Page |
|---|---|
| `site/content/index.md` | Home page and the three-command quickstart |
| `docs/TUTORIAL.md` | `tutorial.html` (same file GitHub shows) |
| `docs/WRITING.md`, else `site/content/writing.md` | `writing.html` |
| `site/content/concepts/*.md` | One page per concept; `order:` in the front matter sets the order |
| `site/static/` | CSS, JS (copy buttons, theme toggle, TOC) and the icon |

Front matter is `key: value` lines between `---` at the top of a page:
`title`, `summary` (the line under the title, and the meta description),
and `order` for concepts.

Beyond plain Markdown: `::: note Title` ... `:::` wraps blocks in a box
(`note`, `warning`, `placeholder`, `step`, `quickstart`, `hero`), and a line
starting with `<` is passed through as HTML.

The "Background" links under tutorial headings come from `TUTORIAL_ASIDES`
in `build.py`. If you rename a tutorial heading, update that table; the build
tells you if one goes missing.

## Open slots

- **Quickstart commands**: the three `PLACEHOLDER` lines in
  `site/content/index.md`.
- **Writing to the network**: add `docs/WRITING.md` (with `title:` and
  `summary:` front matter) and the build uses it in place of the stub in
  `site/content/writing.md`.

## Deploying

`.github/workflows/pages.yml` builds on every push and pull request that
touches `docs/` or `site/`, and deploys from `main`. Turn it on once under
Settings → Pages → Source: GitHub Actions.
