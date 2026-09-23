"""Produce the Dev.to-ready version of the post: front matter + images.

docs/BLOG.md stays the source. This adds the front matter Dev.to's editor
reads and drops the generated images (docs/img/) in at three anchors, using
raw.githubusercontent URLs so the post works the moment it is pasted.

    python scripts/blog_devto.py      # -> build/BLOG_devto.md
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RAW = "https://raw.githubusercontent.com/AKRai-2005/fraudgraph/main/docs/img"

FRONT = f"""---
title: Our fraud classifier scored 0.963 AUC. We threw it away.
published: false
description: Building an agentic fraud investigator on a 590,742-transaction TigerGraph, what a 0.963 AUC actually measured, and the number we had to withdraw.
tags: tigergraph, ai, python, showdev
cover_image: {RAW}/cover.png
---
"""

# (text that ends the paragraph the image belongs after, image, caption)
IMAGES = [
    ("it argues with the score in both directions.",
     "divergence.png",
     "The console opens on this: the bank's score along the bottom, the agent's "
     "assessment up the side. Points off the diagonal are disagreements."),
    ("Nothing about the transaction is remarkable.",
     "case-head.png",
     "The case as the console shows it: verdict, the agent's probability beside the "
     "bank's score, and what remains uncertain."),
    ("all labelled by the bank's own analysts as matching no documented typology.",
     "graph.png",
     "Two hops from a $74.96 payment: the device profile (centre) and the cards it "
     "touched. The purple nodes are prior closed cases found through the same device."),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO / "build" / "BLOG_devto.md"))
    args = ap.parse_args(argv)

    md = (REPO / "docs" / "BLOG.md").read_text(encoding="utf-8")

    # the title and dek live in the front matter now
    body = md.split("\n", 1)[1].lstrip("\n")
    if body.startswith("*What we learned"):
        body = body.split("\n\n", 1)[1]

    missing = []
    for anchor, image, caption in IMAGES:
        # the source wraps its lines, so an anchor can straddle a newline
        pattern = re.compile(r"\s+".join(re.escape(w) for w in anchor.split()))
        found = pattern.search(body)
        if not found:
            missing.append(image)
            continue
        block = f"\n\n![{caption}]({RAW}/{image})\n*{caption}*"
        body = body[:found.end()] + block + body[found.end():]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(FRONT + "\n" + body, encoding="utf-8")

    print(f"{out}  --  {len(out.read_text(encoding='utf-8').split())} words")
    for image in (REPO / "docs" / "img").glob("*.png"):
        print(f"  image: {image.name}")
    if missing:
        print("\nANCHOR NOT FOUND for:", ", ".join(missing))
        print("The post text changed — update IMAGES in this script.")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
