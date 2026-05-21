"""CLI entry point for the STR Document Compliance Checker."""

import asyncio
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from app.handlers.check_handler import CheckHandler
from config.config import Config

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

console = Console()


def _print_summary(report) -> None:
    table = Table(title="Tikrinimo rezultatai", show_header=True, header_style="bold blue")
    table.add_column("Rodiklis", style="bold", width=36)
    table.add_column("Reikšmė", width=16)

    table.add_row("Dokumentas", report.filename)
    table.add_row("Puslapiai", str(report.total_pages))
    table.add_row("Patikrinta normatyvų", str(len(report.str_results)))
    table.add_row("[green]✅ Atitinka[/green]", str(report.pass_count))
    table.add_row("[red]❌ Neatitinka[/red]", str(report.fail_count))
    table.add_row("[yellow]⚠️  Dalinai[/yellow]", str(report.partial_count))
    table.add_row(
        "Privalomieji dokumentai",
        f"{report.docs_found_count}/{len(report.document_results)}",
    )

    console.print()
    console.print(table)

    if report.warnings:
        console.print()
        for w in report.warnings:
            console.print(f"[yellow]⚠️  {w}[/yellow]")


def main() -> None:
    if len(sys.argv) < 2:
        console.print("[bold red]Naudojimas:[/bold red] python main.py <kelias/iki/dokumento.pdf>")
        console.print("[dim]Pvz.: python main.py projektas.pdf[/dim]")
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        console.print(f"[red]Failas nerastas:[/red] {pdf_path}")
        sys.exit(1)

    if not pdf_path.suffix.lower() == ".pdf":
        console.print(f"[red]Failas turi būti PDF formatu:[/red] {pdf_path}")
        sys.exit(1)

    if not Config.validate():
        console.print(
            "[bold red]Klaida:[/bold red] OPENAI_API_KEY nenurodytas. "
            "Sukurkite .env failą pagal .env.example šabloną."
        )
        sys.exit(1)

    console.print(f"\n[bold blue]🏗️  STR Dokumentų tikrintuvas[/bold blue]")
    console.print(f"Dokumentas: [cyan]{pdf_path}[/cyan]\n")

    pdf_bytes = pdf_path.read_bytes()
    handler = CheckHandler()

    messages: list[str] = []

    def on_progress(msg: str) -> None:
        messages.append(msg)
        console.print(f"  [dim]{msg}[/dim]")

    try:
        report, excel_bytes = handler.check_from_bytes(
            pdf_bytes=pdf_bytes,
            filename=pdf_path.name,
            on_progress=on_progress,
        )
    except Exception as exc:
        console.print(f"\n[bold red]Tikrinimas nepavyko:[/bold red] {exc}")
        sys.exit(1)

    _print_summary(report)

    # Save Excel
    output_path = pdf_path.with_suffix(".xlsx")
    output_path.write_bytes(excel_bytes)
    console.print(f"\n[bold green]✅ Ataskaita išsaugota:[/bold green] {output_path}\n")


if __name__ == "__main__":
    main()
