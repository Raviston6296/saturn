"""
Saturn MCP (Model Context Protocol) Server — ZDPAS Toolshed.

Following the Stripe Minions architecture (Layer 2 — Context Hydration with MCP),
this package provides a local MCP server that Goose (or any MCP-compatible AI
agent) can call to get fast, structured ZDPAS tooling without waiting for the
full 4-stage gate pipeline.

Architecture alignment with Stripe Minions:
    Layer 2 – Context Hydration with MCP (Toolshed)   ← this package
    Layer 6 – Three-Tier Testing & Feedback Loop       ← quick_compile.py

Three-tier feedback loop:
    Tier 1 – Quick compile (5–30 s)
               Agent calls compile_quick(files) during coding loop.
               Only compiles the changed files. Returns errors immediately.
               Agent fixes errors before full pipeline runs.
    Tier 2 – Module tests (2–10 min)
               Agent calls run_module_tests(module) for targeted test feedback.
               Runs only the affected module's ScalaTest suites.
    Tier 3 – Full gate pipeline (5–15 min)
               Saturn runs the full 4-stage gate pipeline for final validation.
               By the time it runs, Tier 1 & 2 have already caught most issues.

Server modes:
    Stdio MCP  – default; launched by Goose as a subprocess
    Run once   – python -m mcp.server --workspace /path/to/zdpas

Goose config (added to ~/.config/goose/config.yaml automatically by
agent/goose_profile.py):
    extensions:
      saturn-zdpas:
        type: stdio
        cmd: python
        args: ["-m", "mcp.server"]
        env_keys: [DPAAS_HOME, DPAAS_SOURCE_TAR, BUILD_FILE_HOME]
        timeout: 120
"""
