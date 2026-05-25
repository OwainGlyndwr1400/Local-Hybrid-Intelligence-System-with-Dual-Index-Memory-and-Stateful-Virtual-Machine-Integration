import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from .chat import ChatSession
from .config import get_settings
from .ingest import build_all, build_identity, build_knowledge
from .llm.lm_studio import ChatMessage, LMStudioClient
from .log import configure_logging
from .persistence import load_recent_message_pairs
from .prompts import SystemPromptError, load_system_prompt
from .retrieval import IndexMissingError

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def ping() -> None:
    """Verify the LM Studio endpoint is reachable and list available models."""

    async def _run() -> None:
        settings = get_settings()
        console.print(f"[dim]base_url[/] [bold]{settings.lm_studio_base_url}[/]")
        client = LMStudioClient()
        try:
            models = await client.list_models()
        finally:
            await client.aclose()

        if not models:
            console.print("[yellow]No models loaded in LM Studio.[/]")
            raise typer.Exit(code=1)

        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("id")
        table.add_column("object", style="dim")
        for m in models:
            table.add_row(str(m.get("id", "")), str(m.get("object", "")))
        console.print(table)

        light_present = any(settings.model_light in str(m.get("id", "")) for m in models)
        heavy_present = any(settings.model_heavy in str(m.get("id", "")) for m in models)
        for label, name, present in [
            ("light", settings.model_light, light_present),
            ("heavy", settings.model_heavy, heavy_present),
        ]:
            mark = "[green]ok[/]" if present else "[red]missing[/]"
            console.print(f"  {label:<6} {name}  {mark}")

    asyncio.run(_run())


@app.command()
def info() -> None:
    """Print resolved settings (sources, paths, cache locations)."""
    settings = get_settings()
    table = Table(show_header=False, box=None, pad_edge=False)
    for k, v in settings.model_dump().items():
        if isinstance(v, Path):
            v = str(v)
        table.add_row(f"[dim]{k}[/]", str(v))
    console.print(table)


@app.command("prompt-check")
def prompt_check() -> None:
    """Load the Lumos system prompt and show resolved path + first/last lines."""
    settings = get_settings()
    try:
        text = load_system_prompt()
    except SystemPromptError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(code=1) from e

    resolved = settings.system_prompt_path.expanduser()
    if not resolved.is_absolute():
        resolved = (Path.cwd() / resolved).resolve()

    lines = text.splitlines()
    head = "\n".join(lines[:3])
    tail = "\n".join(lines[-3:]) if len(lines) > 6 else ""
    body = head + ("\n...\n" + tail if tail else "")

    console.print(f"[dim]path[/]   {resolved}")
    console.print(f"[dim]bytes[/]  {len(text.encode('utf-8')):,}")
    console.print(f"[dim]lines[/]  {len(lines):,}")
    console.print(Panel(body, title="system prompt (head/tail)", border_style="dim"))


@app.command("embed-check")
def embed_check() -> None:
    """Round-trip a tiny embedding request to confirm LM Studio embedding endpoint."""

    async def _run() -> None:
        settings = get_settings()
        client = LMStudioClient()
        try:
            vectors = await client.embed(
                ["The lion watches the lion.", "Truth our sword, knowledge our shield."],
                model=settings.lm_studio_embedding_model,
            )
        finally:
            await client.aclose()
        if not vectors:
            console.print("[red]No vectors returned.[/]")
            raise typer.Exit(code=1)
        dim = len(vectors[0])
        console.print(f"[dim]model[/]   {settings.lm_studio_embedding_model}")
        console.print(f"[dim]inputs[/]  2")
        console.print(f"[dim]dim[/]     {dim}")
        if dim != settings.embedding_dim:
            console.print(
                f"[yellow]warning:[/] settings.embedding_dim={settings.embedding_dim} "
                f"does not match server dim={dim}. Update LUMOS_EMBEDDING_DIM."
            )
        else:
            console.print("[green]ok[/]")

    asyncio.run(_run())


@app.command()
def ingest(
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Force rebuild even if manifest matches source."
    ),
    identity_only: bool = typer.Option(
        False, "--identity-only", help="Build only the identity index."
    ),
    knowledge_only: bool = typer.Option(
        False, "--knowledge-only", help="Build only the knowledge index."
    ),
) -> None:
    """Build the split-lane FAISS indexes from conversations.json + dream_pings.jsonl."""
    if identity_only and knowledge_only:
        console.print("[red]--identity-only and --knowledge-only are mutually exclusive.[/]")
        raise typer.Exit(code=2)

    configure_logging(get_settings().log_level)

    async def _run() -> dict:
        if identity_only:
            return {"identity": await build_identity(rebuild=rebuild)}
        if knowledge_only:
            return {"knowledge": await build_knowledge(rebuild=rebuild)}
        return await build_all(rebuild=rebuild)

    try:
        result = asyncio.run(_run())
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(code=1) from e

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("lane")
    table.add_column("chunks", justify="right")
    table.add_column("status")
    table.add_column("path", style="dim")
    for lane, info in result.items():
        status = "[dim]skipped (fresh)[/]" if info.get("skipped") else "[green]built[/]"
        table.add_row(lane, f"{info.get('chunks', 0):,}", status, info.get("path", ""))
    console.print(table)


@app.command()
def search(
    query: str = typer.Argument(..., help="Query string to retrieve against both indexes."),
    lane: str = typer.Option(
        "both", "--lane", help="Which lane to search: identity, knowledge, or both."
    ),
    top_k: int = typer.Option(6, "--top-k", min=1, max=64),
) -> None:
    """Run a quick retrieval against the built indexes (for sanity-checking ingest)."""
    from .vectors import VectorStore

    settings = get_settings()
    cache = settings.cache_dir.expanduser()
    if not cache.is_absolute():
        cache = (Path.cwd() / cache).resolve()

    async def _embed() -> list[float]:
        client = LMStudioClient()
        try:
            vectors = await client.embed([query], model=settings.lm_studio_embedding_model)
        finally:
            await client.aclose()
        return vectors[0]

    query_vec = asyncio.run(_embed())

    lanes: list[tuple[str, Path, Path]] = []
    if lane in ("identity", "both"):
        lanes.append(("identity", cache / "identity.faiss", cache / "identity.jsonl"))
    if lane in ("knowledge", "both"):
        lanes.append(("knowledge", cache / "knowledge.faiss", cache / "knowledge.jsonl"))
    if not lanes:
        console.print(f"[red]Unknown lane: {lane}[/]")
        raise typer.Exit(code=2)

    for name, idx, meta in lanes:
        if not (idx.exists() and meta.exists()):
            console.print(f"[yellow]{name}: index not built (run `lumos ingest`).[/]")
            continue
        store = VectorStore.load(idx, meta)
        hits = store.search(query_vec, top_k=top_k)
        console.print(f"\n[bold]{name}[/]  ({store.size:,} vectors)")
        for score, m in hits:
            preview = (m.get("text") or "").strip().splitlines()[0][:120]
            label = m.get("conversation_title") or m.get("subject") or m.get("agent") or ""
            console.print(f"  [cyan]{score:.3f}[/]  [dim]{label}[/]  {preview}")


@app.command(name="dream-cycle")
def dream_cycle(
    limit: int | None = typer.Option(
        None, "--limit", min=1, help="Maximum number of pending turns to consolidate this run."
    ),
    reset: bool = typer.Option(
        False, "--reset", help="Re-consolidate all turns (ignore watermark)."
    ),
) -> None:
    """Consolidate pending chat turns from identity_events.jsonl into the live identity FAISS."""
    from .dream import dream_status, run_dream_cycle

    configure_logging(get_settings().log_level)
    status = dream_status()
    console.print(
        f"[dim]pending[/]  {status['pending']}    "
        f"[dim]total consolidated so far[/]  {status['state']['total_consolidated']}"
    )

    result = asyncio.run(run_dream_cycle(limit=limit, reset=reset))

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("metric")
    table.add_column("value", justify="right")
    if result.get("skipped"):
        table.add_row("status", f"[dim]skipped ({result.get('reason', '')})[/]")
    else:
        table.add_row("status", "[green]consolidated[/]")
        table.add_row("new chunks", f"{result.get('consolidated', 0):,}")
        table.add_row("cluster assignments", f"{result.get('cluster_assignments', 0):,}")
        table.add_row(
            "identity index size",
            f"{result.get('index_size', 0):,}",
        )
    console.print(table)


@app.command(name="tools-list")
def tools_list() -> None:
    """List all registered tools the model can call."""
    from .tools import get_registry

    reg = get_registry()
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("name", style="bold")
    table.add_column("description", style="dim")
    for name, tool in reg.items():
        desc = tool.description
        if len(desc) > 100:
            desc = desc[:100] + "…"
        table.add_row(name, desc)
    console.print(table)
    console.print(f"\n[dim]{len(reg)} tools registered[/]")

    from .config import get_settings
    settings = get_settings()
    console.print(
        f"\n[dim]tools_enabled[/] = "
        f"[{'green' if settings.tools_enabled else 'red'}]{settings.tools_enabled}[/]"
    )
    console.print(
        f"[dim]tool_allowed_paths[/] = "
        f"[yellow]{settings.tool_allowed_paths or '(empty — no file access)'}[/]"
    )


@app.command(name="urevm-status")
def urevm_status(
    limit: int = typer.Option(20, "--limit", min=1, max=200),
) -> None:
    """Print URE-VM tick + recent trace."""
    from .urevm import get_vm

    vm = get_vm()
    console.print(f"[dim]tick[/]   {vm.tick}")
    if not vm.trace:
        console.print("[dim]no trace entries yet[/]")
        return
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("tick", justify="right", style="dim")
    table.add_column("op")
    table.add_column("operand", style="dim")
    table.add_column("result", style="dim")
    for entry in vm.trace[-limit:]:
        operand_str = (
            ", ".join(f"{k}={v}" for k, v in (entry.operand or {}).items())
            if entry.operand
            else ""
        )
        result_str = ", ".join(
            f"{k}={v}" for k, v in (entry.result or {}).items() if k != "q"
        )
        table.add_row(str(entry.tick), entry.name, operand_str, result_str)
    console.print(table)


@app.command(name="atlas-build")
def atlas_build(
    rebuild: bool = typer.Option(False, "--rebuild", help="Force rebuild even if atlas exists."),
    n_identity: int = typer.Option(60, "--identity-clusters", min=2, max=500),
    n_knowledge: int = typer.Option(20, "--knowledge-clusters", min=2, max=500),
) -> None:
    """Build the neural atlas — k-means clusters of identity + knowledge embeddings."""
    from .atlas import build_atlas, reload_cluster_map

    configure_logging(get_settings().log_level)
    try:
        result = build_atlas(
            n_identity_clusters=n_identity,
            n_knowledge_clusters=n_knowledge,
            rebuild=rebuild,
        )
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]{e}[/]")
        raise typer.Exit(code=1) from e

    reload_cluster_map()

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("metric")
    table.add_column("value", justify="right")
    if result.get("skipped"):
        table.add_row("status", "[dim]skipped (exists)[/]")
    else:
        table.add_row("status", "[green]built[/]")
        table.add_row("identity clusters", f"{result.get('identity_clusters', 0):,}")
        table.add_row("knowledge clusters", f"{result.get('knowledge_clusters', 0):,}")
        table.add_row("edges", f"{result.get('edges', 0):,}")
    table.add_row("path", str(result.get("path", "")))
    console.print(table)


@app.command()
def serve(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    reload: bool = typer.Option(
        False, "--reload", help="Auto-reload on code changes (dev only)."
    ),
) -> None:
    """Run the FastAPI server (serves HUD and exposes /api/*)."""
    import uvicorn

    settings = get_settings()
    bind_host = host or settings.host
    bind_port = port or settings.port
    console.print(f"[dim]lumos server[/]  http://{bind_host}:{bind_port}")
    uvicorn.run(
        "lumos_node.api.app:create_app",
        host=bind_host,
        port=bind_port,
        reload=reload,
        factory=True,
        log_level=settings.log_level.lower(),
    )


@app.command()
def discord() -> None:
    """Start the Discord bridge bot. Operator-only DMs forward to /chat.

    Requires `lumos serve` running separately. Reads LUMOS_DISCORD_TOKEN and
    LUMOS_DISCORD_OPERATOR_ID from .env.
    """
    configure_logging()
    from .bridges.discord_bot import run as run_discord

    console.print("[dim]lumos discord bridge[/]  starting")
    try:
        run_discord()
    except RuntimeError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(code=1) from e


@app.command(name="mcp-serve")
def mcp_serve() -> None:
    """Run Lumos's MCP server over stdio.

    Exposes a read-only subset of tools (memory, telemetry, tasks, etc.) to
    external MCP clients like Claude Desktop and Claude Code. Typically NOT
    invoked manually — MCP clients spawn this subprocess via their own config.

    See `lumos mcp-list-tools` to inspect what's exposed.

    Stdio-only by design. Authentication is via process boundary (only the
    parent that spawned us can communicate). To run as a network-accessible
    HTTP server, see Phase 37 docs in HANDOFF.md.

    Note: logs go to stderr (stdio's stdin/stdout are reserved for MCP
    JSON-RPC traffic).
    """
    # MCP servers communicate over stdio so log output MUST go to stderr,
    # never stdout. Configure logging silently — no rich console banner.
    configure_logging()
    from .mcp_server import run as run_mcp_server
    run_mcp_server()


@app.command(name="mcp-list-tools")
def mcp_list_tools() -> None:
    """Show which tools are exposed via the MCP server, grouped by category.

    Useful for verifying the allowlist before pointing an external client at
    Lumos's MCP. Reads from `lumos_node/mcp_allowlist.py`.
    """
    configure_logging()
    # Import tools FIRST so the registry populates via side-effect imports.
    from . import tools  # noqa: F401
    from .mcp_allowlist import MCP_EXPOSED_TOOLS, MCP_TOOL_GROUPS
    from .mcp_server import exposed_tool_count
    from .tools import get_registry

    registry = get_registry()
    total_registered = len(registry)
    exposed = exposed_tool_count()

    console.print(
        f"[bold]Lumos MCP allowlist[/]  "
        f"{exposed}/{total_registered} tools exposed for external read access\n"
    )
    for group_name, tool_names in MCP_TOOL_GROUPS.items():
        console.print(f"[cyan]{group_name}[/]")
        for name in tool_names:
            if name in registry and name in MCP_EXPOSED_TOOLS:
                console.print(f"  [green]✓[/] {name}")
            elif name in registry:
                # in group definition but not in allowlist (shouldn't happen)
                console.print(f"  [yellow]?[/] {name}  [dim](in group but not allowlisted)[/]")
            else:
                console.print(f"  [red]✗[/] {name}  [dim](not registered)[/]")
        console.print()

    # Surface any allowlisted tools that aren't in any group — diagnostic for
    # operator if the groups dict and the allowlist drift.
    grouped_names: set[str] = {n for names in MCP_TOOL_GROUPS.values() for n in names}
    ungrouped = MCP_EXPOSED_TOOLS - grouped_names
    if ungrouped:
        console.print("[yellow]Allowlisted but ungrouped:[/]")
        for name in sorted(ungrouped):
            console.print(f"  [green]✓[/] {name}")
        console.print()

    # Surface registered tools that are intentionally NOT exposed (helps the
    # operator confirm the local-only ones are still excluded).
    not_exposed = sorted(set(registry.keys()) - MCP_EXPOSED_TOOLS)
    if not_exposed:
        console.print(f"[dim]Not exposed (local-only — {len(not_exposed)} tools):[/]")
        for name in not_exposed:
            console.print(f"  [dim]·[/] [dim]{name}[/]")


@app.command(name="compress-all")
def compress_all(
    lane: str = typer.Option(
        "both",
        "--lane",
        help="Which lane to compress: identity, knowledge, or both.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-compress chunks that already have compression metadata.",
    ),
    method: str = typer.Option(
        "extractive",
        "--method",
        help="extractive (fast heuristic, ~5min for 32K chunks) or llm (high quality, ~17-178hrs).",
    ),
) -> None:
    """Bulk-compress existing FAISS metadata for v3.6-style aggressive RAG compression.

    Walks identity.jsonl and/or knowledge.jsonl, generates 3-layer compression
    metadata (summary + anchor packet + operational packet) for any chunk that
    lacks it. After running, set LUMOS_PREFER_COMPRESSED_CHUNKS=true in .env
    and restart lumos serve — retrieval blocks drop from ~10K tokens/turn to
    ~2-3K tokens/turn for v3.6-comparable behavior.

    Method:
      extractive (default): pure heuristic (regex + frequency + entity), no LLM.
                            Lower quality but runs in minutes for 32K chunks.
      llm: uses Phase 26's compress_chunk via LM Studio. High quality, slow
            (17-178 hours depending on model). Reuses LUMOS_COMPRESSION_MODEL
            or falls back to LUMOS_MODEL_LIGHT.
    """
    configure_logging("INFO")
    import asyncio
    import orjson
    from pathlib import Path
    from .compression import extractive_compress, compress_chunk
    from .config import get_settings as _gs

    settings = _gs()
    cache = settings.cache_dir.expanduser()
    if not cache.is_absolute():
        cache = (Path.cwd() / cache).resolve()

    lanes_to_process = []
    if lane in ("identity", "both"):
        lanes_to_process.append(("identity", cache / "identity.jsonl"))
    if lane in ("knowledge", "both"):
        lanes_to_process.append(("knowledge", cache / "knowledge.jsonl"))
    if not lanes_to_process:
        console.print(f"[red]invalid lane: {lane}[/]")
        raise typer.Exit(code=1)

    if method not in ("extractive", "llm"):
        console.print(f"[red]invalid method: {method}[/]")
        raise typer.Exit(code=1)

    llm_model = settings.compression_model or settings.model_light

    async def _compress_one_llm(text: str) -> dict | None:
        return await compress_chunk(text, model=llm_model)

    for lane_name, path in lanes_to_process:
        if not path.exists():
            console.print(f"[yellow]skip[/] {lane_name}: {path} not found")
            continue

        console.print(Rule(f"[dim]{lane_name}[/]  {path}"))

        # Load all metadata lines (whole file in memory; ~30K entries fits easily)
        with path.open("rb") as f:
            entries = [orjson.loads(line) for line in f if line.strip()]

        total = len(entries)
        compressed_count = 0
        skipped = 0
        failed = 0

        for i, meta in enumerate(entries):
            text = (meta.get("text") or "").strip()
            if not text:
                skipped += 1
                continue
            if "compression" in meta and not force:
                skipped += 1
                continue
            try:
                if method == "extractive":
                    meta["compression"] = extractive_compress(text)
                else:
                    result = asyncio.run(_compress_one_llm(text))
                    if result is None:
                        failed += 1
                        continue
                    meta["compression"] = result
                compressed_count += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                console.print(f"[yellow]failed[/] entry {i}: {e}")

            if (i + 1) % 500 == 0 or (i + 1) == total:
                console.print(
                    f"  [{i + 1:>5} / {total}]  compressed={compressed_count}  skipped={skipped}  failed={failed}"
                )

        # Write back atomically (.tmp + os.replace)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("wb") as f:
            for meta in entries:
                f.write(orjson.dumps(meta))
                f.write(b"\n")
        import os as _os
        _os.replace(str(tmp), str(path))

        console.print(
            f"[green]done[/] {lane_name}: {compressed_count} compressed, "
            f"{skipped} skipped, {failed} failed → {path}"
        )

    console.print()
    console.print("[dim]Next steps:[/]")
    console.print("  1. Set [bold]LUMOS_PREFER_COMPRESSED_CHUNKS=true[/] in .env")
    console.print("  2. Restart [bold]lumos serve[/]")
    console.print("  3. Verify retrieval token cost dropped via HUD telemetry")


@app.command()
def chat(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show INFO-level structlog events during chat."
    ),
    fresh: bool = typer.Option(
        False, "--fresh", help="Skip restoring history from prior sessions."
    ),
    history: int | None = typer.Option(
        None,
        "--history",
        min=0,
        max=200,
        help="Override LUMOS_RESTORE_HISTORY_TURNS for this run.",
    ),
) -> None:
    """Interactive chat with Lumos. Requires `lumos ingest` to have completed."""
    settings = get_settings()
    configure_logging("INFO" if verbose else "WARNING")
    session = ChatSession(settings=settings)

    try:
        # Pre-load stores so we fail fast if indexes aren't built.
        from .retrieval import get_identity_store, get_knowledge_store
        get_identity_store(settings)
        get_knowledge_store(settings)
    except IndexMissingError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(code=1) from e
    except SystemPromptError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(code=1) from e

    n_restore = 0 if fresh else (history if history is not None else settings.restore_history_turns)
    restored_pairs: list[tuple[str, str]] = []
    if n_restore > 0:
        restored_pairs = load_recent_message_pairs(n_restore, settings)
        for user_msg, asst_msg in restored_pairs:
            session.history.append(ChatMessage(role="user", content=user_msg))
            session.history.append(ChatMessage(role="assistant", content=asst_msg))

    console.print(
        f"[dim]{settings.node_name} · {settings.node_role} · model: {settings.model_light}[/]"
    )
    restore_note = (
        f"    history: {len(restored_pairs)} prior turn{'s' if len(restored_pairs) != 1 else ''} restored"
        if restored_pairs
        else "    history: fresh"
    )
    console.print(f"[dim]session: {session.session_id}{restore_note}    type /quit to exit[/]")
    console.print(Rule(style="dim"))

    async def _stream_response(message: str) -> None:
        console.print(f"[bold]{settings.node_name}[/]  ", end="")
        async for delta in session.stream_turn(message):
            console.print(delta, end="", soft_wrap=True, highlight=False)
        console.print()

        footer_parts: list[str] = []
        if session.last_retrieval is not None:
            r = session.last_retrieval
            footer_parts.append(
                f"retrieved: {len(r.identity)} memory · {len(r.knowledge)} knowledge"
            )
        if session.last_usage is not None:
            u = session.last_usage
            prompt_t = u.get("prompt_tokens")
            completion_t = u.get("completion_tokens")
            total_t = u.get("total_tokens")
            if prompt_t is not None and completion_t is not None:
                footer_parts.append(
                    f"tokens: {total_t or (prompt_t + completion_t):,} "
                    f"(prompt {prompt_t:,} + completion {completion_t:,})"
                )
        if footer_parts:
            console.print(f"  [dim]{'    '.join(footer_parts)}[/]")

    while True:
        try:
            user_message = console.input(f"[bold]{settings.operator_name}[/]  ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not user_message:
            continue
        if user_message.lower() in ("/quit", "/exit", "/q"):
            break
        try:
            asyncio.run(_stream_response(user_message))
        except Exception as e:
            console.print(f"[red]error:[/] {e}")
        console.print()


if __name__ == "__main__":
    app()
