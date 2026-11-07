"""Build README.md: ASCII portrait (static SVG in assets/) + neofetch-style profile card as text.

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
WIDTH = 55  # characters in the info column

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


# A line is a list of segments (style, text[, href]). Styles: t=text k=key v=value d=dots h=header
def kv(key: str, value, width: int = WIDTH) -> list:
    """key ....... value, padded to `width`; value may be a str or a list of segments."""
    segs = [("v", value)] if isinstance(value, str) else value
    dots = width - len(key) - sum(len(sg[1]) for sg in segs) - 5
    return [("t", ". "), ("k", key), ("t", ":"), ("d", " " + "." * max(dots, 1) + " ")] + segs


def kv2(k1: str, v1, k2: str, v2, split: int = 30) -> list:
    left = kv(k1, v1, split)
    right = kv(k2, v2, WIDTH - split - 1)[1:]
    return left + [("t", " | ")] + right


def header(title: str, lead: str = "", href: str = "") -> list:
    return [("t", lead), ("h", title, href), ("t", " " + "—" * (WIDTH - len(lead) - len(title) - 1))]


def link(text: str, href: str) -> list:
    return [("v", text, href)]


def fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else "-"


def info_lines(s: dict) -> list:
    if "add" in s:
        loc = f"{fmt(s['add'] - s['del'])} ( {fmt(s['add'])}++, {fmt(s['del'])}-- )"
    else:
        loc = "-"
    repos = f"{fmt(s.get('repos'))} {{Contributed: {fmt(s.get('contributed'))}}}"
    return [
        header(f"{USER}@github", href=f"https://github.com/{USER}"),
        kv("OS", "macOS, Linux, Windows"),
        kv("Uptime", uptime(BIRTHDAY, date.today())),
        kv("Host", "São Paulo, Brazil"),
        kv("Kernel", "Development Lead"),
        kv("IDE", "VS Code"),
        [],
        kv("Languages.Code", "JavaScript, TypeScript, Python, C#"),
        kv("Languages.Computer", "HTML, CSS, JSON, YAML, SQL"),
        kv("Languages.Real", "Portuguese, English"),
        [],
        kv("Hobbies.Hardware", "Electronics, DIY"),
        kv("Hobbies.Life", "Bonsai, Family"),
        [],
        header("Contact", "- "),
        kv("Email.Personal", link("carvalhocortes@gmail.com", "mailto:carvalhocortes@gmail.com")),
        kv("LinkedIn", link("carvalhocortes", "https://www.linkedin.com/in/carvalhocortes/")),
        kv("Website", link("carvalhocortes.com.br", "https://www.carvalhocortes.com.br")),
        [],
        header("GitHub Stats", "- "),
        kv2("Repos", repos, "Stars", fmt(s.get("stars"))),
        kv2("Commits", fmt(s.get("commits")), "Followers", fmt(s.get("followers"))),
        kv("Lines of Code", loc),
    ]


# ---------------------------------------------------------------- README


def render_segment(seg) -> str:
    style, text, href = (*seg, "")[:3]
    html = escape(text, quote=False)
    if href:
        html = f'<a href="{escape(href)}">{html}</a>'
    return f"<b>{html}</b>" if style in ("k", "h") else html


def readme(lines: list) -> str:
    info = "\n".join("".join(render_segment(sg) for sg in line).rstrip() for line in lines)
    return f"""<!-- Generated by scripts/build_card.py (daily GitHub Action). Edit the script, not this file. -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/art_dark.svg">
  <img align="left" width="300" alt="ASCII portrait of Fernando Côrtes" src="assets/art_light.svg">
</picture>

<pre>
{info}
</pre>
"""


def main() -> None:
    (ROOT / "README.md").write_text(readme(info_lines(github_stats())))
    print("built README.md")


if __name__ == "__main__":
    main()
