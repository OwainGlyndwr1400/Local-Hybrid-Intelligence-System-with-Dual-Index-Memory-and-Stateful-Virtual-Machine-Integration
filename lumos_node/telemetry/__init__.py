"""Cosmic telemetry + airspace clients (Phase 32).

Two pillars:
  - cosmic.py  — NOAA SWPC, NASA DONKI/EONET/NeoWs, USGS
  - airspace.py — OpenSky Network (OAuth2 client-credentials post-2026 migration)

Tools layer in `lumos_node/tools/telemetry_tools.py` consumes these clients.
"""
