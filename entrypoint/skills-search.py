#!/usr/bin/env python3
"""Hermes Skills Hub search — bridge for hermes-workspace marketplace.

Originally vendored from outsourc-e/hermes-workspace@2149b86bbe42dc622cb4b86501bb2c5ba7387817:
  https://raw.githubusercontent.com/outsourc-e/hermes-workspace/main/scripts/skills-search.py

Modified to fix upstream bugs: the original maps fields from non-existent
SkillMeta attributes (source_label, author) so source/author/homepage end up
empty in every result. The actual SkillMeta attrs are: source, repo, path,
identifier, name, description, trust_level, tags, extra (dict with installs,
detail_url, repo_url). See the SkillMeta class in
/opt/hermes/tools/skills_hub.py for the canonical schema.

Container-side path alignment (~/hermes-agent -> /opt/hermes via symlink) and
PYTHONPATH for the workspace process are configured in the Dockerfile and
supervisord conf so this script can `import tools.skills_hub`.
"""
import json
import sys
import os

sys.path.insert(0, os.path.expanduser("~/hermes-agent"))

from tools.skills_hub import GitHubAuth, create_source_router, unified_search


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else ""
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    source_filter = sys.argv[3] if len(sys.argv) > 3 else "all"

    if not query:
        print(json.dumps({"results": [], "source": "idle"}))
        return

    auth = GitHubAuth()
    sources = create_source_router(auth)
    results = unified_search(query, sources, source_filter=source_filter, limit=limit)

    out = []
    for r in results:
        extra = getattr(r, "extra", {}) or {}
        source = getattr(r, "source", "")
        repo = getattr(r, "repo", "")
        # Bundle prefers homepage > repo > extra.homepage. Pass the real public
        # URL so the marketplace card's "Homepage" link goes somewhere users
        # can actually visit to verify the skill source.
        homepage = (
            extra.get("repo_url")
            or extra.get("detail_url")
            or extra.get("homepage")
            or None
        )
        # Bundle's author fallback chain: author > repo.split('/')[0] > extra.author > source > "Community".
        # Use the registry (e.g., "skills.sh") as a sensible author when nothing else is set.
        author = extra.get("author") or repo.split("/")[0] if repo else source

        out.append({
            "id": getattr(r, "identifier", r.name),
            "name": r.name,
            "description": getattr(r, "description", ""),
            "author": author,
            "category": getattr(r, "category", ""),
            "tags": getattr(r, "tags", []),
            "source": source,
            "repo": repo,
            "homepage": homepage,
            "extra": extra,
            "trust": getattr(r, "trust_level", "community"),
            "installCommand": f"hermes skills install {getattr(r, 'identifier', r.name)}",
            "installed": False,
        })

    print(json.dumps({"results": out, "source": "skills-hub", "total": len(out)}))


if __name__ == "__main__":
    main()
