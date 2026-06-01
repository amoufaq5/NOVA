"""Nova Debug Adapter Protocol implementation.

A minimal DAP server that translates between DAP JSON messages (over
stdio, Content-Length framed like LSP) and `gdb --interpreter=mi3`.
The server itself does no DWARF parsing — gdb does. We just shuttle
breakpoint / step / continue requests in, and turn `*stopped` /
`*exited` async records back into DAP `stopped` / `terminated` events.
"""

__version__ = "0.1.0"
