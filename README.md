<!---
Copyright © 2025 Maida.AI contributors.
Licensed under CC-BY-4.0: https://creativecommons.org/licenses/by/4.0/
-->
# XCP -- eXtensible Coordination Protocol
**Layer-0-to-4 transport substrate for AI agents and tools**
> [!NOTE]
> Part of the **Maida.AI** project

## Revision List

| Revision | Changes |
| -------- | ------- |
| [XCP 0.1](revisions/xcp-v0.1.md) | Initial Draft |


## Why does XCP exist?

Modern agent protocols (MCP, ACP, A2A) focus on *what* agents say -- tasks, tool calls, agent cards -- but assume a human-oriented JSON/HTTP substrate.
As payloads grow (embeddings, multimodal tensors) that substrate becomes a bottleneck.
**XCP standardises *how* agents talk below the JSON layer**, offering binary framing, codec negotiation, flow-control and built-in semantic clarification without redefining higher-level semantics.

| Stack slice                   | MCP                  | ACP                 | A2A                      | **XCP**                                                                     |
| ----------------------------- | -------------------- | ------------------- | ------------------------ | --------------------------------------------------------------------------- |
| **L0-L2 Transport & Framing** | HTTP/JSON-RPC        | HTTP/REST           | HTTP + SSE               | **Binary frames over TCP/QUIC; multiplexed streams; ACK/NACK**              |
| **L3-L4 Representation**      | JSON envelope        | JSON body           | JSON "parts"             | **Negotiable codecs (JSON, f16, INT8, mixed) + compression & crypto flags** |
| **L5 Semantics**              | Tool & memory schema | P2P message schema  | Task & Agent-Card schema | **Schema IDs + optional wire-validation**                                   |
| **L6 Application**            | Tool execution       | Peer agent workflow | Task lifecycle           | *Open*                                                                      |

**Take-away:** XCP starts *below* MCP/ACP/A2A, so those protocols can bind to XCP for higher throughput without changing their semantics.

## Key benefits

1. **Wire efficiency** -- PoC shows \~2x speed-up vs. HTTP/JSON when streaming 10 KB f16 embeddings.
2. **Codec negotiation** -- fall back to JSON, upgrade to binary tensors when supported.
3. **Fine-grained reliability** -- per-frame `ACK/NACK`, duplicate suppression, congestion hints.
4. **Semantic clarification frames** -- `CLARIFY_REQ/RES` pause delivery until ambiguities are resolved.
5. **Pluggable security** -- mutual TLS or QUIC 1.3 plus optional AEAD payload encryption.
6. **Layer composability** -- any agent protocol can ride on XCP by assigning `msgType >= 0x100`.


## Integration patterns

| Pattern                       | Example                                                                                                     |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------- |
| **A2A-over-XCP**              | Replace HTTP/SSE with XCP streams; keep Agent Cards & JSON parts, send video frames as `TENSOR_F16`.        |
| **ACP gateway**               | REST endpoint auto-converts to XCP when both peers support it; negotiates best `bodyCodec`.                 |
| **MCP tool calls inside XCP** | Carry MCP JSON in a single `DATA` frame; large tool outputs arrive as compressed binary instead of base-64. |

> [!NOTE]
> **Non-goals:** XCP will never define tasks, tools, or agent roles -- it is purely a transport & representation layer.


## Repository layout

```
xcp/
- README.md        # You are here
- revisions/       # Versioned spec drafts (Markdown)
- benchmarks/      # Benchmark code & result artifacts
```

*The reference implementation lives in [`benchmarks/`](benchmarks/) and demonstrates HTTP/2 and QUIC bindings plus JSON <-> f16 tensor codecs.*


## Getting started

1. Clone the repo and install deps: `pip install -r benchmarks/requirements.txt`.
2. Run the demo: `PYTHONPATH=. python benchmarks/poc_http2_vs_xcp.py` -> see throughput table.
3. Run the multi-codec benchmark: `PYTHONPATH=. python benchmarks/poc_http2_vs_xcp_multi.py` -> compare HTTP+JSON, HTTP+F16, XCP+JSON, XCP+F16.
4. Read the latest spec draft in [`revisions/`](revisions/).


## Benchmarks


|Transport + Codec | p50 (us, ↓) | p95 (us, ↓) | p99 (us, ↓) | Throughput (MiB/s, ↑) |
|------------------|------------ |-------------|-------------| ----------------------|
| XCP + JSON       | 66.18       | 74.50       | 78.09       | **97.2**              |
| HTTP/2 + JSON    | 334.58      | 335.87      | 337.63      | 23.8                  |
| Protobuf HTTP/2  | 315.70      | 317.27      | 319.55      | 43.2                  |
| ---------------  | ----------- | ----------- | ----------- | --------------------- |
| XCP + F16        | 47.35       | 82.61       | 86.42       | 107.1                 |
| HTTP/2 + F16     | 323.41      | 323.74      | 324.42      | 27.6                  |
| Protobuf TCP     | 27.81       | 31.70       | 33.19       | **180.9**             |


> [!NOTE]
> The `Protobuf` over `TCP` is faster.
> Compiled XCP with non-JSON headers is the next optimization


## Known Limitations and Optimizations

### Why might Protobuf over TCP be faster than XCP?

- **Serialization/Deserialization Overhead:** Protobuf uses highly optimized binary serialization (with C++ extensions in Python), while XCP uses JSON for headers, which is slower to encode/decode in Python.
- **Implementation Language:** Protobuf's core is implemented in C++ (even when used from Python), making it much faster than pure Python code. XCP is currently implemented in pure Python.
- **Frame Structure and Overhead:** XCP frames include a JSON-encoded header, adding both size and parsing overhead. Protobuf messages are compact and binary.
- **Message Size:** Protobuf messages are typically smaller than equivalent JSON or JSON+binary hybrid messages, reducing transmission and parsing time.
- **Socket Handling:** Both use TCP, but extra work in XCP (e.g., more complex framing, thread contention) can add overhead.
- **Python GIL and Threading:** XCP's use of threads and Python-level work can be bottlenecked by the Global Interpreter Lock (GIL), especially under high concurrency.

### Potential Optimizations for XCP

- Use a binary format for headers (e.g., struct, msgpack, or Protobuf for headers) instead of JSON.
- Implement performance-critical parts in C, Cython, or Rust to reduce Python overhead.
- Reduce per-message overhead by avoiding JSON for every frame.
- Profile and optimize socket handling and threading to minimize bottlenecks.

## Contributing

Pull requests welcome!  Open a discussion or issue first if you plan large changes.
Higher-layer protocol adapters (MCP/A2A/ACP) and additional codecs are especially appreciated.


## License

CC-BY-4.0 for docs.  Reference code dual-licensed MIT/Apache-2.0.


## References

* MCP -- *Multimodal Capabilities Protocol* (Anthropic, 2024)
* ACP -- *Agent Communication Protocol* (IBM + BeeAI, 2024)
* A2A -- *Agent-to-Agent Coordination* (Google, 2024)
* XCP Drafts -- see [`revisions/`](revisions/)
