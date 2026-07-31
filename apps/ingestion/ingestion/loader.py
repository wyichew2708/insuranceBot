"""OKF bundle loader (§6.1.1): local path or git clone/pull of the CMS output."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from contracts.okf import OkfBlock, parse_okf_markdown

logger = logging.getLogger("ingestion.loader")


class BundleLoadError(Exception):
    pass


def sync_bundle_repo(git_url: str, git_ref: str, checkout_dir: Path) -> Path:
    """Clone or update the bundle repo at the given ref. Returns the checkout path."""
    if (checkout_dir / ".git").exists():
        subprocess.run(["git", "-C", str(checkout_dir), "fetch", "origin", git_ref], check=True)
        subprocess.run(["git", "-C", str(checkout_dir), "checkout", "-q", f"origin/{git_ref}"], check=True)
    else:
        checkout_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "-b", git_ref, git_url, str(checkout_dir)], check=True
        )
    return checkout_dir


def load_bundle(bundle_dir: Path) -> list[OkfBlock]:
    """Parse every *.md block. Fail on missing required fields; log-and-keep
    unknown frontmatter keys (tolerant reader)."""
    if not bundle_dir.is_dir():
        raise BundleLoadError(f"bundle dir {bundle_dir} does not exist")
    blocks: list[OkfBlock] = []
    errors: list[str] = []
    for path in sorted(bundle_dir.rglob("*.md")):
        if path.name.lower() in {"readme.md", "index.md"}:
            continue
        try:
            block = parse_okf_markdown(path.read_text(), source_path=str(path.relative_to(bundle_dir)))
        except Exception as exc:
            errors.append(f"{path.relative_to(bundle_dir)}: {exc}")
            continue
        unknown = block.frontmatter.unknown_keys()
        if unknown:
            logger.warning("%s: unknown frontmatter keys %s (kept)", path.name, unknown)
        blocks.append(block)
    if errors:
        raise BundleLoadError("bundle contains invalid blocks:\n" + "\n".join(errors))
    if not blocks:
        raise BundleLoadError(f"no OKF blocks found under {bundle_dir}")
    return blocks
