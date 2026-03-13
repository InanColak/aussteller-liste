from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from src.exporters import export_csv, export_excel, list_caches, load_cache, save_cache
from src.orchestrator import scrape_url
from src.platforms.registry import list_platforms

app = typer.Typer(
    name="aussteller",
    help="Scrape exhibitor lists from trade fair websites.",
)


def _export_result(result, format: str) -> None:
    """Export result with automatic CSV fallback on Excel errors."""
    if format == "csv":
        path = export_csv(result)
        typer.echo(f"Exported to: {path}")
        return

    try:
        path = export_excel(result)
        typer.echo(f"Exported to: {path}")
    except Exception as e:
        typer.echo(f"Excel export failed: {e}")
        typer.echo("Falling back to CSV...")
        path = export_csv(result)
        typer.echo(f"Exported to: {path}")


@app.command()
def scrape(
    url: str = typer.Argument(..., help="Trade fair website URL"),
    format: str = typer.Option("excel", "--format", "-f", help="Output format: excel or csv"),
    limit: int = typer.Option(0, "--limit", "-l", help="Max exhibitors (0 = all)"),
) -> None:
    """Scrape exhibitors from a trade fair website."""
    typer.echo(f"Scraping: {url}")

    result = asyncio.run(scrape_url(url, limit=limit))

    typer.echo(f"\nFound {result.total_exhibitors} exhibitors.")

    if result.total_exhibitors == 0:
        typer.echo("No exhibitors found.")
        raise typer.Exit(1)

    # Save cache before export so data is never lost
    cache_path = save_cache(result)
    typer.echo(f"Data cached: {cache_path}")

    _export_result(result, format)


@app.command()
def export(
    cache_file: str = typer.Argument(None, help="Cache file path (omit to use latest)"),
    format: str = typer.Option("excel", "--format", "-f", help="Output format: excel or csv"),
) -> None:
    """Re-export exhibitor data from a cached scrape (no re-scraping needed)."""
    if cache_file:
        path = Path(cache_file)
    else:
        caches = list_caches()
        if not caches:
            typer.echo("No cached data found. Run 'aussteller scrape' first.")
            raise typer.Exit(1)
        path = caches[0]
        typer.echo(f"Using latest cache: {path}")

    if not path.exists():
        typer.echo(f"Cache file not found: {path}")
        raise typer.Exit(1)

    result = load_cache(path)
    typer.echo(f"Loaded {result.total_exhibitors} exhibitors from cache.")

    _export_result(result, format)


@app.command()
def platforms() -> None:
    """List known scraper platforms and learned profiles."""
    from src.learning.store import list_profiles

    plats = list_platforms()
    if plats:
        typer.echo("Built-in platforms:")
        for p in plats:
            typer.echo(f"  {p['name']}: {p['description']}")
            typer.echo(f"    Patterns: {p['patterns']}")

    profiles = list_profiles()
    if profiles:
        typer.echo("\nLearned profiles:")
        for prof in profiles:
            used = prof.last_used_at.strftime("%Y-%m-%d") if prof.last_used_at else "never"
            typer.echo(
                f"  {prof.platform_id}: {', '.join(prof.domain_patterns)} "
                f"(confidence: {prof.confidence:.0%}, last used: {used})"
            )

    if not plats and not profiles:
        typer.echo("No platforms or profiles registered.")


if __name__ == "__main__":
    app()
