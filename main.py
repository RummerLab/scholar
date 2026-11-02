#!/usr/bin/env python3
"""Orchestrate scholar data fetch: author, coauthors, publications, DOI, impact factor."""

import argparse
import sys

import src.cache_config  # noqa: F401 - configure HTTP cache before any requests
from src.config import Config
from src.doi_resolver import resolve_doi_for_publication
from src.logging_config import setup_logging
from src.output import (
    is_fresh,
    load_author_by_id,
    save_author_by_id,
    set_last_successful_index,
)
from src.scholar_fetcher import fetch_full_author
from src.standardise import standardise_authors


def _journal_is_symposium_or_conference(journal: str) -> bool:
    j = (journal or "").strip().lower()
    return any(x in j for x in ["symposium", "conference", "workshop", "annual meeting"])


def _enrich_publication(
    pub: dict,
    author_name: str,
    journal_impact_factor_dic: dict[str, str],
    previous_pub: dict | None,
) -> None:
    """Add standardised authors, DOI, and impact factor to a publication."""
    bib = pub.setdefault("bib", {})
    journal_name = (bib.get("journal") or "").strip().lower()
    if journal_name == "null":
        journal_name = ""

    if _journal_is_symposium_or_conference(journal_name):
        pub["doi"] = ""
        pub["doi_link"] = ""
        pub["doi_short"] = ""
        pub["doi_short_link"] = ""
        pub["doi_resolved_link"] = ""
        bib["impact_factor"] = ""
        return

    pub_title = bib.get("title", "")
    pub_url = pub.get("pub_url", "")
    author_last = author_name.split()[-1] if author_name else ""

    prev = previous_pub or {}
    prev_doi = prev.get("doi") or ""
    prev_link = prev.get("doi_link") or ""
    prev_short = prev.get("doi_short") or ""
    prev_short_link = prev.get("doi_short_link") or ""
    prev_resolved = prev.get("doi_resolved_link") or ""

    if prev_doi or prev_link:
        doi_res = {
            "doi": prev_doi or pub.get("doi", ""),
            "doi_link": prev_link or pub.get("doi_link", ""),
            "doi_short": prev_short or pub.get("doi_short", ""),
            "doi_short_link": prev_short_link or pub.get("doi_short_link", ""),
            "doi_resolved_link": prev_resolved or "",
        }
    else:
        try:
            doi_res = resolve_doi_for_publication(
                pub_url,
                pub_title,
                author_last,
                previous_doi=None,
                previous_doi_link=None,
                previous_doi_short=None,
                previous_doi_short_link=None,
            )
        except Exception:
            doi_res = {
                "doi": "",
                "doi_link": "",
                "doi_short": "",
                "doi_short_link": "",
                "doi_resolved_link": "",
            }

    pub["doi"] = doi_res.get("doi", "") or ""
    pub["doi_link"] = doi_res.get("doi_link", "") or ""
    pub["doi_short"] = doi_res.get("doi_short", "") or ""
    pub["doi_short_link"] = doi_res.get("doi_short_link", "") or ""
    pub["doi_resolved_link"] = doi_res.get("doi_resolved_link", "") or ""

    authors = bib.get("author", "")
    bib["authors_standardised"] = standardise_authors(authors)

    if journal_name:
        if journal_name in journal_impact_factor_dic:
            bib["impact_factor"] = journal_impact_factor_dic[journal_name]
        else:
            from src.journal_impact_factor import add_impact_factor

            add_impact_factor(journal_name, "")
            bib["impact_factor"] = ""


def run(scholar_id: str, config: Config | None = None) -> int:
    """Run the full scholar fetch pipeline. Returns exit code."""
    import logging

    log = logging.getLogger(__name__)
    if config is None:
        config = Config(scholar_id=scholar_id)

    from src.journal_impact_factor import load_impact_factor

    journal_impact_factor_dic = load_impact_factor()
    log.info("Loaded %d impact factors", len(journal_impact_factor_dic))

    data_dir = config.scholar_data_dir
    try:
        previous = load_author_by_id(scholar_id, data_dir)
    except ValueError as e:
        log.error("%s", e)
        return 1
    if previous:
        log.info("Loaded previous data for %s", scholar_id)

    if is_fresh(previous.get("last_fetched") if previous else None, config.fresh_data_seconds):
        log.info(
            "Data is fresh (within %ds). Skipping fetch.",
            config.fresh_data_seconds,
        )
        return 0

    log.info("Fetching author profile (may encounter CAPTCHA in Docker)")
    prev_pubs = (previous or {}).get("publications", [])

    def on_coauthor_save(author: dict, idx: int, total: int) -> None:
        set_last_successful_index(author, "coauthor", idx - 1)
        save_author_by_id(author, scholar_id, data_dir)

    def on_publication_save(author: dict, idx: int, total: int) -> None:
        set_last_successful_index(author, "publication", idx - 1)
        save_author_by_id(author, scholar_id, data_dir)

    try:
        author = fetch_full_author(
            config.scholar_id,
            previous,
            config.coauthor_delay_seconds,
            config.publication_delay_seconds,
            on_coauthor_filled=on_coauthor_save,
            on_publication_filled=on_publication_save,
        )
    except (ValueError, Exception) as e:
        log.exception("Fetch failed: %s", e)
        return 1

    for i, pub in enumerate(author["publications"]):
        prev_pub = prev_pubs[i] if i < len(prev_pubs) else None
        _enrich_publication(
            pub,
            author.get("name", ""),
            journal_impact_factor_dic,
            prev_pub,
        )
        set_last_successful_index(author, "publication", i)
        save_author_by_id(author, scholar_id, data_dir)

    out_path = save_author_by_id(author, scholar_id, data_dir)
    log.info("Author data written to %s", out_path)
    return 0


def main() -> None:
    setup_logging()
    p = argparse.ArgumentParser(description="Full Google Scholar scrape for one author ID.")
    p.add_argument("scholar_id", help="Google Scholar author ID (e.g. ynWS968AAAAJ)")
    args = p.parse_args()
    try:
        config = Config(scholar_id=args.scholar_id)
        # Validate path containment before running (rejects traversal in scholar_id).
        _ = config.output_path
    except ValueError as e:
        p.error(str(e))
    sys.exit(run(args.scholar_id, config))


if __name__ == "__main__":
    main()
