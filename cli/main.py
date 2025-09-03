# cli/main.py
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

import typer

from lore_ingest.api import ingest_file, resegment_work
from lore_ingest.events import build_ingested_event, build_failed_event, emit_async
from lore_ingest.pushgw import push_ingest, push_resegment

app = typer.Typer(add_completion=False, no_args_is_help=True, help="lore-ingest CLI")


def _open(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db.as_posix())
    conn.row_factory = sqlite3.Row
    return conn


@app.command("works")
def cli_works(
    db: Path = typer.Option(Path(os.getenv("DB_PATH", "./tropes.db")), "--db"),
    q: Optional[str] = typer.Option(None, "--q", help="Substring match on title/author"),
    limit: int = typer.Option(50, "--limit", min=1, max=1000),
    ids_only: bool = typer.Option(False, "--ids-only"),
):
    """List recent works (id, title). Use --ids-only for just IDs."""
    conn = _open(db)
    try:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT id, title, created_at FROM work "
                "WHERE COALESCE(title,'') LIKE ? OR COALESCE(author,'') LIKE ? "
                "ORDER BY datetime(created_at) DESC LIMIT ?",
                (like, like, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, created_at FROM work "
                "ORDER BY datetime(created_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        if ids_only:
            for r in rows:
                typer.echo(r["id"])
            return
        if not rows:
            typer.echo("(no works)")
            return
        typer.echo(f"{'ID':36}  {'TITLE':30}  {'CREATED_AT'}")
        typer.echo("-" * 80)
        for r in rows:
            typer.echo(f"{r['id']}  {(r['title'] or '')[:30]:30}  {r['created_at']}")
    finally:
        conn.close()


@app.command("works-ls")
def cli_works_ls(
    db: Path = typer.Option(Path(os.getenv("DB_PATH", "./tropes.db")), "--db"),
    q: Optional[str] = typer.Option(None, "--q", help="Substring match on title/author"),
    limit: int = typer.Option(50, "--limit", min=1, max=1000),
):
    """List works with counts: id, title, chars, scenes, chunks, created_at."""
    conn = _open(db)
    try:
        base = (
            "SELECT w.id, w.title, COALESCE(w.char_count,0) AS chars, w.created_at, "
            "COALESCE(sc.scenes,0) AS scenes, COALESCE(ch.chunks,0) AS chunks "
            "FROM work w "
            "LEFT JOIN (SELECT work_id, COUNT(*) AS scenes FROM scene GROUP BY work_id) sc ON sc.work_id = w.id "
            "LEFT JOIN (SELECT work_id, COUNT(*) AS chunks FROM chunk GROUP BY work_id) ch ON ch.work_id = w.id "
        )
        args: list = []
        if q:
            like = f"%{q}%"
            base += "WHERE COALESCE(w.title,'') LIKE ? OR COALESCE(w.author,'') LIKE ? "
            args.extend([like, like])
        base += "ORDER BY datetime(w.created_at) DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(base, args).fetchall()

        if not rows:
            typer.echo("(no works)")
            return
        typer.echo(f"{'ID':36}  {'TITLE':30}  {'CHARS':>7}  {'SCN':>3}  {'CHK':>3}  {'CREATED_AT'}")
        typer.echo("-" * 100)
        for r in rows:
            title = (r['title'] or "")[:30]
            typer.echo(f"{r['id']}  {title:30}  {r['chars']:7}  {r['scenes']:3}  {r['chunks']:3}  {r['created_at']}")
    finally:
        conn.close()


@app.command("ingest")
def cli_ingest(
    path: Path,
    title: Optional[str] = typer.Option(None, "--title"),
    author: Optional[str] = typer.Option(None, "--author"),
    db: Path = typer.Option(Path(os.getenv("DB_PATH", "./tropes.db")), "--db"),
    profile: Optional[str] = typer.Option(None, "--profile"),
    echo_event: bool = typer.Option(False, "--echo-event"),
):
    """
    Ingest a single file. Emits document.ingested on success and document.failed on error.
    Pushes an ok/fail marker to Pushgateway if configured.
    """
    try:
        res = ingest_file(path=path.as_posix(), title=title, author=author, db_path=db.as_posix(), profile=profile)
        typer.echo(f"work_id={res.work_id} sha1={res.content_sha1} sizes={res.sizes}")

        # fill title/author from DB if not provided
        conn = _open(db)
        row = conn.execute("SELECT title, author FROM work WHERE id = ?", (res.work_id,)).fetchone()
        conn.close()

        ev = build_ingested_event(
            db_path=db.as_posix(),
            work_id=res.work_id,
            source_path=path.as_posix(),
            title=(title or (row and row["title"])),
            author=(author or (row and row["author"])),
            content_sha1=res.content_sha1,
            sizes=res.sizes,
            profile=profile,
        )
        emit_async(ev)
        push_ingest("ok", duration_s=None, extra_labels={"source": "cli"})
        if echo_event:
            import json
            typer.echo(json.dumps(ev, ensure_ascii=False))

    except Exception as e:
        # failure path: event + push + error code
        fev = build_failed_event(
            source_path=path.as_posix(),
            title=title,
            author=author,
            reason=str(e),
            stage="cli-ingest",
            profile=profile,
        )
        emit_async(fev)
        push_ingest("fail", duration_s=None, extra_labels={"source": "cli"})
        typer.secho(f"[ingest] ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command("resegment")
def cli_resegment(
    work_id: str = typer.Option(..., "--work-id"),
    db: Path = typer.Option(Path(os.getenv("DB_PATH", "./tropes.db")), "--db"),
    profile: Optional[str] = typer.Option(None, "--profile"),
    window: int = typer.Option(512, "--window-chars"),
    stride: int = typer.Option(384, "--stride-chars"),
    echo_event: bool = typer.Option(False, "--echo-event"),
):
    """
    Force re-segmentation. Emits document.ingested with {"resegment": true} or document.failed on error.
    Pushes an ok/fail marker to Pushgateway if configured.
    """
    try:
        res = resegment_work(work_id=work_id, db_path=db.as_posix(), profile=profile, window_chars=window, stride_chars=stride)
        typer.echo(f"resegmented work_id={work_id} sizes={res.sizes} profile={profile or 'default'}")

        conn = _open(db)
        row = conn.execute(
            "SELECT title, author, source, content_sha1 FROM work WHERE id = ?",
            (work_id,),
        ).fetchone()
        conn.close()
        source = row["source"] if row and row["source"] else f"resegment:{work_id}"
        ev = build_ingested_event(
            db_path=db.as_posix(),
            work_id=work_id,
            source_path=source,
            title=(row and row["title"]),
            author=(row and row["author"]),
            content_sha1=(row and row["content_sha1"]),
            sizes=res.sizes,
            profile=profile,
            extra={"resegment": True},
        )
        emit_async(ev)
        push_resegment("ok", duration_s=None, extra_labels={"source": "cli"})
        if echo_event:
            import json
            typer.echo(json.dumps(ev, ensure_ascii=False))

    except Exception as e:
        fev = build_failed_event(
            source_path=f"resegment:{work_id}",
            title=None,
            author=None,
            reason=str(e),
            stage="cli-resegment",
            profile=profile,
        )
        emit_async(fev)
        push_resegment("fail", duration_s=None, extra_labels={"source": "cli"})
        typer.secho(f"[resegment] ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command("watch")
def cli_watch():
    """Run the folder watcher (env-driven)."""
    from service.watcher import load_config_from_env, run_watcher
    cfg = load_config_from_env()
    run_watcher(
        inbox=cfg.inbox,
        success_dir=cfg.success_dir,
        fail_dir=cfg.fail_dir,
        db_path=cfg.db_path,
        profile=cfg.profile,
    )


def main():
    app()


if __name__ == "__main__":
    main()

