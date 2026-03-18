"""
Saturn Goose Extensions — custom tools and profile management for ZDPAS.

This package extends Goose (https://github.com/block/goose) with tools
optimized for the ZDPAS Scala/Java project. It can be used as:

  1. A Goose toolkit plugin (if goose-ai is installed as a library)
  2. A standalone tools provider via MCP (Model Context Protocol)

When installed, Goose will have access to ZDPAS-specific tools:
  - search_scala_files   — fast pattern search across Scala source
  - get_module_context   — rich context about a ZDPAS module
  - get_compilation_errors — parse scalac error output into structured data
  - get_test_failures    — parse ScalaTest output into structured failures
  - get_project_structure — ZDPAS package/module overview

Usage with goose-ai library:
    from saturn_goose.toolkit import SaturnZDPASToolkit
    # Register with Goose session...

Usage as standalone MCP server:
    from saturn_goose.mcp_server import start_mcp_server
    start_mcp_server(workspace="/path/to/zdpas", port=9090)
"""
