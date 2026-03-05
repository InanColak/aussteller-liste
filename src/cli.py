from __future__ import annotations

import asyncio

import typer

from src.exporters import export_csv, export_excel
from src.orchestrator import scrape_url
from src.platforms.registry import list_platforms

app = typer.Typer(
    name="aussteller",
    help="Scrape exhibitor lists from trade fair websites.",
)


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

    if format == "csv":
        path = export_csv(result)
    else:
        path = export_excel(result)

    typer.echo(f"Exported to: {path}")


@app.command()
def platforms() -> None:
    """List known scraper platforms."""
    plats = list_platforms()
    if not plats:
        typer.echo("No platforms registered.")
        return
    for p in plats:
        typer.echo(f"  {p['name']}: {p['description']}")
        typer.echo(f"    Patterns: {p['patterns']}")


if __name__ == "__main__":
    app()
