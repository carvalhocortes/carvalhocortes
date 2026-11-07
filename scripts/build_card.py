"""Build dark_mode.svg and light_mode.svg: ASCII portrait + neofetch-style profile card.

Stdlib only, so the GitHub Action needs no installs.
Env: ACCESS_TOKEN (PAT with repo + read:user for private stats; falls back to GITHUB_TOKEN),
     USER_NAME (defaults to carvalhocortes). Without any token, stats show as "-".
"""
import hashlib
import json
import os
import urllib.request
from datetime import date, timedelta
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER = os.environ.get("USER_NAME", "carvalhocortes")
TOKEN = os.environ.get("ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")
BIRTHDAY = date(1984, 2, 7)
CACHE = ROOT / "cache/loc.json"
WIDTH = 62  # characters in the info column

# ---------------------------------------------------------------- GitHub stats


def gql(query: str, variables: dict) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {TOKEN}", "User-Agent": f"{USER}-profile-card"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.load(resp)
    if body.get("errors") and not body.get("data"):
        raise RuntimeError(body["errors"])
    return body["data"]


def profile_stats() -> dict:
    q = """query($login: String!, $after: String) {
      user(login: $login) {
        id
        followers { totalCount }
        repositoriesContributedTo(contributionTypes: [COMMIT, PULL_REQUEST, REPOSITORY]) { totalCount }
        repositories(first: 100, after: $after, ownerAffiliations: OWNER) {
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes { stargazerCount }
        }
      }
    }"""
    stars, after = 0, None
    while True:
        u = gql(q, {"login": USER, "after": after})["user"]
        repos = u["repositories"]
        stars += sum(n["stargazerCount"] for n in repos["nodes"])
        if not repos["pageInfo"]["hasNextPage"]:
            break
        after = repos["pageInfo"]["endCursor"]
    return {
        "id": u["id"],
        "repos": repos["totalCount"],
        "contributed": u["repositoriesContributedTo"]["totalCount"],
        "stars": stars,
        "followers": u["followers"]["totalCount"],
    }


def all_repos() -> list[dict]:
    q = """query($login: String!, $after: String) {
      user(login: $login) {
        repositories(first: 100, after: $after,
                     ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]) {
          pageInfo { hasNextPage endCursor }
          nodes {
            nameWithOwner
            defaultBranchRef { target { ... on Commit { history { totalCount } } } }
          }
        }
      }
    }"""
    out, after = [], None
    while True:
        r = gql(q, {"login": USER, "after": after})["user"]["repositories"]
        out += r["nodes"]
        if not r["pageInfo"]["hasNextPage"]:
            return out
        after = r["pageInfo"]["endCursor"]


def repo_loc(name_with_owner: str, author_id: str) -> dict:
    owner, name = name_with_owner.split("/")
    q = """query($owner: String!, $name: String!, $author: ID!, $after: String) {
      repository(owner: $owner, name: $name) {
        defaultBranchRef { target { ... on Commit {
          history(first: 100, after: $after, author: {id: $author}) {
            totalCount
            pageInfo { hasNextPage endCursor }
            nodes { additions deletions }
          }
        } } }
      }
    }"""
    add = dele = commits = 0
    after = None
    while True:
        h = gql(q, {"owner": owner, "name": name, "author": author_id, "after": after})
        h = h["repository"]["defaultBranchRef"]["target"]["history"]
        commits = h["totalCount"]
        add += sum(n["additions"] for n in h["nodes"])
        dele += sum(n["deletions"] for n in h["nodes"])
        if not h["pageInfo"]["hasNextPage"]:
            return {"commits": commits, "add": add, "del": dele}
        after = h["pageInfo"]["endCursor"]


def loc_stats(author_id: str) -> dict:
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    fresh = {}
    for repo in all_repos():
        ref = repo["defaultBranchRef"]
        if not ref:
            continue  # empty repo
        name, total = repo["nameWithOwner"], ref["target"]["history"]["totalCount"]
        key = hashlib.sha256(name.encode()).hexdigest()  # cache is public: never store private repo names
        if key in cache and cache[key]["total"] == total:
            fresh[key] = cache[key]
            continue
        try:
            fresh[key] = {"total": total, **repo_loc(name, author_id)}
        except Exception as exc:  # no access (e.g. org blocks the token): skip
            print(f"skip {name}: {exc}")
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(fresh, indent=1, sort_keys=True) + "\n")
    return {
        "commits": sum(r["commits"] for r in fresh.values()),
        "add": sum(r["add"] for r in fresh.values()),
        "del": sum(r["del"] for r in fresh.values()),
    }


def github_stats() -> dict:
    if not TOKEN:
        return {}
    stats = profile_stats()
    stats.update(loc_stats(stats["id"]))
    return stats


# ---------------------------------------------------------------- card text


def uptime(born: date, today: date) -> str:
    y, m, d = today.year - born.year, today.month - born.month, today.day - born.day
    if d < 0:
        m -= 1
        d += (date(today.year, today.month, 1) - timedelta(days=1)).day
    if m < 0:
        y, m = y - 1, m + 12
    plural = lambda n, w: f"{n} {w}{'s' if n != 1 else ''}"
    return f"{plural(y, 'year')}, {plural(m, 'month')}, {plural(d, 'day')}"


# A line is a list of (style, text) segments. Styles: t=text k=key v=value d=dots a=add r=del
def kv(key: str, value, width: int = WIDTH) -> list:
    """key ....... value, padded to `width`; value may be a str or a list of segments."""
    segs = [("v", value)] if isinstance(value, str) else value
    dots = width - len(key) - sum(len(t) for _, t in segs) - 5
    return [("t", ". "), ("k", key), ("t", ":"), ("d", " " + "." * max(dots, 1) + " ")] + segs


def kv2(k1: str, v1: str, k2: str, v2: str, split: int = 34) -> list:
    left = kv(k1, v1, split)
    right = kv(k2, v2, WIDTH - split - 1)[1:]
    return left + [("t", " | ")] + right


def header(title: str, lead: str = "") -> list:
    return [("t", lead), ("v" if not lead else "t", title), ("t", " " + "—" * (WIDTH - len(lead) - len(title) - 1))]


def fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else "-"


def info_lines(s: dict) -> list:
    if "add" in s:
        loc = [("v", fmt(s["add"] - s["del"])), ("t", " ( "), ("a", fmt(s["add"]) + "++"),
               ("t", ", "), ("r", fmt(s["del"]) + "--"), ("t", " )")]
    else:
        loc = "-"
    repos = [("v", fmt(s.get("repos"))), ("t", " {Contributed: "), ("v", fmt(s.get("contributed"))), ("t", "}")]
    return [
        header(f"{USER}@github"),
        kv("OS", "macOS, Linux, Windows"),
        kv("Uptime", uptime(BIRTHDAY, date.today())),
        kv("Host", "InBot"),
        kv("Kernel", "Development Lead"),
        kv("IDE", "VS Code"),
        [],
        kv("Languages.Programming", "JavaScript, TypeScript, Python, C#"),
        kv("Languages.Computer", "HTML, CSS, JSON, YAML, SQL"),
        kv("Languages.Real", "Portuguese, English"),
        [],
        kv("Hobbies.Hardware", "Electronics, DIY"),
        kv("Hobbies.Life", "Bonsai, Family"),
        [],
        header("Contact", "- "),
        kv("Email.Personal", "carvalhocortes@gmail.com"),
        kv("LinkedIn", "carvalhocortes"),
        kv("Website", "carvalhocortes.com.br"),
        [],
        header("GitHub Stats", "- "),
        kv2("Repos", repos, "Stars", fmt(s.get("stars"))),
        kv2("Commits", fmt(s.get("commits")), "Followers", fmt(s.get("followers"))),
        kv("Lines of Code on GitHub", loc),
    ]


# ---------------------------------------------------------------- SVG

THEMES = {
    "dark": dict(bg="#161b22", border="#30363d", t="#c9d1d9", k="#ffa657", v="#a5d6ff",
                 d="#616e7f", a="#3fb950", r="#f85149", art="#c9d1d9"),
    "light": dict(bg="#f6f8fa", border="#d0d7de", t="#24292f", k="#953800", v="#0a3069",
                  d="#c2cfde", a="#1a7f37", r="#cf222e", art="#24292f"),
}
FONT, CHAR_W, LINE_H, PAD = 16, 9.65, 20, 24
ART_FONT, ART_CHAR_W, ART_LINE_H = 12, 7.24, 15  # smaller font = more detail in the portrait


def svg(theme: str, art: list[str], lines: list) -> str:
    c = THEMES[theme]
    art_w = max(len(a) for a in art)
    info_x = PAD + art_w * ART_CHAR_W + 2 * CHAR_W
    body_h = max(len(art) * ART_LINE_H, len(lines) * LINE_H)
    width = round(info_x + (WIDTH + 1) * CHAR_W + PAD)
    height = round(PAD * 2 + body_h)
    art_top = PAD + (body_h - len(art) * ART_LINE_H) / 2
    info_top = PAD + (body_h - len(lines) * LINE_H) / 2
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" font-family="ConsolasFallback,Consolas,\'SF Mono\',Menlo,\'Courier New\',monospace" '
        f'width="{width}px" height="{height}px" font-size="{FONT}px">',
        "<style>",
        f".t{{fill:{c['t']}}} .k{{fill:{c['k']}}} .v{{fill:{c['v']}}} .d{{fill:{c['d']}}} "
        f".a{{fill:{c['a']}}} .r{{fill:{c['r']}}} text,tspan{{white-space:pre}}",
        "</style>",
        f'<rect width="{width}px" height="{height}px" fill="{c["bg"]}" rx="15" stroke="{c["border"]}"/>',
        f'<text x="{PAD}" y="{PAD}" fill="{c["art"]}" font-size="{ART_FONT}px">',
    ]
    for i, row in enumerate(art):
        out.append(f'<tspan x="{PAD}" y="{art_top + (i + 1) * ART_LINE_H - 4:.0f}">{escape(row)}</tspan>')
    out.append("</text>")
    out.append(f'<text x="{info_x:.0f}" y="{PAD}">')
    for i, segs in enumerate(lines):
        y = round(info_top + (i + 1) * LINE_H - 5)
        body = "".join(f'<tspan class="{s}">{escape(txt)}</tspan>' for s, txt in segs if txt)
        out.append(f'<tspan x="{info_x:.0f}" y="{y}">{body}</tspan>')
    out.append("</text>\n</svg>\n")
    return "\n".join(out)


def main() -> None:
    lines = info_lines(github_stats())
    for theme in THEMES:
        art = (ROOT / f"assets/ascii_{theme}.txt").read_text().splitlines()
        (ROOT / f"{theme}_mode.svg").write_text(svg(theme, art, lines))
    print("built", ", ".join(f"{t}_mode.svg" for t in THEMES))


if __name__ == "__main__":
    main()
