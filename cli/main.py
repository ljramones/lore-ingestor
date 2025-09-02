# cli/main.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer

from lore_ingest.api import ingest_file
from service.watcher import run_watcher

app = typer.Typer(add_completion=False, no_args_is_help=True, help="lore-ingest CLI")

@app.command("ingest")
def cli_ingest(
    path: Path,
    title: Optional[str] = typer.Option(None, "--title"),
    author: Optional[str] = typer.Option(None, "--author"),
    db: Path = typer.Option(Path(os.getenv("DB_PATH", "./tropes.db")), "--db", help="SQLite DB path"),
    profile: Optional[str] = typer.Option(None, "--profile", help="Segmentation profile (default|dense|sparse|markdown|screenplay)"),
):
    """Ingest a single file into the DB."""
    res = ingest_file(path=path.as_posix(), title=title, author=author, db_path=db.as_posix(), profile=profile)
    typer.echo(f"work_id={res.work_id} sha1={res.content_sha1} sizes={res.sizes}")

@app.command("watch")
def cli_watch(
    inbox: Path = typer.Option(Path(os.getenv("INBOX", "./inbox")), "--inbox"),
    success: Path = typer.Option(Path(os.getenv("SUCCESS_DIR", "./success")), "--success"),
    fail: Path = typer.Option(Path(os.getenv("FAIL_DIR", "./fail")), "--fail"),
    db: Path = typer.Option(Path(os.getenv("DB_PATH", "./tropes.db")), "--db"),
    profile: Optional[str] = typer.Option(os.getenv("INGEST_PROFILE", None), "--profile"),
):
    """Watch a folder and ingest new files."""
    run_watcher(
        inbox=inbox,
        success_dir=success,
        fail_dir=fail,
        db_path=db.as_posix(),
        profile=profile,
    )

def main():
    app()

if __name__ == "__main__":
    main()
