"""Create the private data repo and the public Space, and fill both.

Run `hf auth login` first: this script never takes a token as an argument and
never prints one. It uses whatever credential the CLI stored.

    python scripts/deploy_to_hf.py --dry-run     # say what it would do
    python scripts/deploy_to_hf.py               # do it

Afterwards one manual step remains, and it stays manual on purpose: the Space
needs a READ token of its own, in Settings -> Variables and secrets -> HF_TOKEN,
so it can pull the private data repo. Paste that in the browser; it should not
pass through a script, a shell history or a chat log.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fraudgraph.config import PATHS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-repo", default="fraudgraph-data",
                    help="name of the PRIVATE dataset repo (default: fraudgraph-data)")
    ap.add_argument("--space", default="fraud-console",
                    help="name of the PUBLIC Space (default: fraud-console)")
    ap.add_argument("--bundle", default=str(PATHS.build / "deploy_bundle"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from huggingface_hub import HfApi
    from huggingface_hub.errors import HfHubHTTPError

    api = HfApi()
    try:
        me = api.whoami()["name"]
    except Exception as exc:                                    # noqa: BLE001
        print(f"Not logged in ({type(exc).__name__}). Run:  hf auth login", file=sys.stderr)
        return 2

    data_id = f"{me}/{args.data_repo}"
    space_id = f"{me}/{args.space}"
    bundle = Path(args.bundle)
    if not bundle.is_dir():
        print(f"No bundle at {bundle}. Run: python scripts/make_deploy_bundle.py", file=sys.stderr)
        return 2
    size = sum(f.stat().st_size for f in bundle.rglob("*") if f.is_file()) / 1048576
    files = sum(1 for f in bundle.rglob("*") if f.is_file())

    print(f"account      {me}")
    print(f"data repo    {data_id}   (private dataset, {files} files, {size:.1f} MB)")
    print(f"space        {space_id}   (public, docker, port 7860)")
    if args.dry_run:
        print("\n--dry-run: nothing was created.")
        return 0

    # 1. the private data repo
    api.create_repo(data_id, repo_type="dataset", private=True, exist_ok=True)
    info = api.repo_info(data_id, repo_type="dataset")
    if not info.private:                     # never upload the dataset to a public repo
        print(f"REFUSING: {data_id} is public. Make it private and re-run.", file=sys.stderr)
        return 1
    print(f"uploading {size:.1f} MB ...")
    api.upload_folder(repo_id=data_id, repo_type="dataset", folder_path=str(bundle),
                      commit_message="Data bundle for the deployed console")
    print(f"  https://huggingface.co/datasets/{data_id}  (private)")

    # 2. the Space: two files, and the variable naming the data repo
    api.create_repo(space_id, repo_type="space", space_sdk="docker", exist_ok=True)
    api.upload_file(path_or_fileobj=str(REPO_ROOT / "deploy" / "Dockerfile"),
                    path_in_repo="Dockerfile", repo_id=space_id, repo_type="space",
                    commit_message="Build the console image")
    api.upload_file(path_or_fileobj=str(REPO_ROOT / "deploy" / "space" / "README.md"),
                    path_in_repo="README.md", repo_id=space_id, repo_type="space",
                    commit_message="Space card")
    try:
        api.add_space_variable(space_id, "FG_DATA_REPO", data_id)
    except HfHubHTTPError as exc:
        print(f"  could not set FG_DATA_REPO ({exc}); set it in the Space settings.")

    print(f"  https://huggingface.co/spaces/{space_id}")
    print("\nOne step left, in the browser, because a token should not pass "
          "through a script:")
    print(f"  {space_id} -> Settings -> Variables and secrets -> New secret")
    print("  HF_TOKEN = a READ token from https://huggingface.co/settings/tokens")
    print("  (optional) TG_HOST and TG_SECRET, to serve from TigerGraph")
    print("\nThe Space rebuilds on that change. Watch its Logs for [boot] lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
