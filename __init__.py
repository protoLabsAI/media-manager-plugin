"""Media Manager plugin.

Ingest YouTube / direct video URLs and file uploads into a Jellyfin library with
generated NFO + poster metadata. Background worker drains an ingest queue; a
console view drives it.

register() wires: config → tools → view (page + gated data/upload routes) → the
background ingest worker (register_surface start/stop).
"""

import os


def register(registry) -> None:
    from . import ingest, jobs, tools, view

    cfg = dict(getattr(registry, "config", {}) or {})
    # Jellyfin API key: prefer the plugin secret, fall back to the env var.
    cfg["jellyfin_api_key"] = cfg.get("jellyfin_api_key") or os.environ.get("JELLYFIN_API_KEY", "")

    jobs.configure(cfg.get("db_path", "/sandbox/media-manager/jobs.db"))
    ingest.configure(cfg)

    for t in tools.get_tools():
        registry.register_tool(t)

    registry.register_router(view.build_view_router(), prefix="/plugins/media_manager")
    registry.register_router(view.build_data_router(), prefix="/api/plugins/media_manager")

    # Background ingest worker — a long-lived surface (start on boot, stop on
    # shutdown/disable). It drains the SQLite job queue and runs the pipeline.
    registry.register_surface(
        start=lambda: jobs.start_worker(ingest.process_job),
        stop=jobs.stop_worker,
        name="media-ingest-worker",
    )
