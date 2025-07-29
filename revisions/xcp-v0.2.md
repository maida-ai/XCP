<!---
Copyright © 2025 Maida.AI contributors.
Licensed under CC-BY-4.0: https://creativecommons.org/licenses/by/4.0/
-->

# Cross-Agent Communication Protocol (XCP)

- Owner: Maida.AI
- Version: 0.2
- *Status*: **Draft -- July 29 2025**
- *Intended Audience*: AI-infrastructure engineers, LLM platform maintainers
- *Description*: Layered specification for low-latency, schema-aware communication between AI agents and services.

## What's New in v0.2

The second public draft consolidates feedback from prototype deployments and introduces the following headline features:

* **Ether envelope integration** -- the data payload is now a self-describing `Ether` JSON object that carries `kind`, `schema_version`, `payload`, `metadata`, and optional `attachments`.
* **Composite `SchemaKey`** -- uniquely identifies a semantic schema with `(namespace, kind, major, minor, hash128)` and prevents hash collisions.
* **Deterministic little-endian framing** -- the endianness of all multibyte integers is normative.
* **CRC32C trailer** -- guards against payload corruption before decryption.
* **Arrow IPC (0x0020) and DLPack (0x0021) codecs** -- enable zero-copy exchange of columnar batches and GPU tensors.
* **Tensor header for `TENSOR_*` codecs** -- a fixed 32-byte prefix supplies `ndim`, `dtype`, `shape`, and quantization flags.
* **Compression-then-encryption order** -- maximises compression ratio and mitigates chosen-ciphertext risks.
* **Numeric error codes** -- uniform, machine-readable failure semantics (`ERR_SCHEMA_UNKNOWN`, `ERR_CODEC_UNSUPPORTED`, ...).
* **Chunking with `MORE` flag** -- formal state machine for large messages split across frames.
* **Capability negotiation** -- `HELLO` advertises supported codecs and `(kind,major,[minor...])` ranges.
* **Security tweaks** -- prescribes ChaCha20-Poly1305 AEAD and deterministic nonce derivation.

## 1 Purpose

XCP provides a binary-first, capability-negotiated channel that moves *structured knowledge* (tensors, tables, text, control messages) between interchangeable nodes--both **in-process** and **cross-server**.  The protocol now mainstreams **Ether**, a self-describing data envelope used inside Maida's pipelines.

Goals:

1. **Efficiency** -- zero-copy where possible, zstd+AEAD when needed.
2. **Evolvability** -- versioned schemas and adapters.
3. **Observability** -- trace/idempotency fields, numeric error codes.
4. **Polyglot** -- deterministic LE framing; control plane defined with Cap'n Proto; data codecs include JSON, Apache Arrow IPC, DLPack.

## 2 Terminology

| Term           | Meaning                                                                               |
| -------------- | ------------------------------------------------------------------------------------- |
| **Ether**      | Self-describing envelope: `{kind, schema_version, payload, metadata, attachments}`    |
| **Frame**      | Top-level unit on the wire (header + payload + CRC).                                  |
| **Channel**    | Independent ordered stream within a connection (maps to QUIC / HTTP^2 stream).         |
| **Codec**      | Concrete serialization of an Ether or control body (JSON, ARROW\_IPC, ...).             |
| **SchemaKey**  | Composite identifier of a semantic schema: `(namespace, kind, major, minor, hash128)` |
| **Attachment** | External or inline binary blob referenced from an Ether.                              |

All integers are **unsigned little-endian**.

## 3 Layer Model

```
L6  Application logic          ── task orchestration, tool calls
L5  Semantics                  ── Ether kinds & schema versions
L4  Representation            ── Codecs: JSON, ARROW_IPC, DLPACK
L3  Serialization / Envelope  ── Ether encoded + length-prefix
L2  Session / Control         ── HELLO, CAPS, ACK, NACK, ERROR
L1  Framing                   ── Multiplexed streams
L0  Transport                 ── QUIC v1 (+mTLS) . HTTP/2 (+mTLS)
```

*Ether spans L3--L5*: it is created by the application layer, serialized by a codec at L4, and carried as the *payload* of a DATA frame defined at L1/L2.

## 4 Framing (L0 & L1)

### 4.1 Frame Layout

```
0      4    5     6      8               8+n      8+n+PLEN   8+n+PLEN+p 8+n+PLEN+p+4
+------+----+-----+------+---------------+----------+---------+---------+
|MAGIC |VER |FLAGS|HLEN  |   HEADER      |  PLEN    | PAYLOAD | CRC32C  |
+------+----+-----+------+---------------+----------+---------+---------+
```

| Field       | Bytes  | Description                                                                       |
| ----------- | ------ | --------------------------------------------------------------------------------- |
| **MAGIC**   | 4      | `0xA9A17A10` (ASCII "XCP\n")                                                      |
| **VER**     | 1      | 4-bit major / 4-bit minor. Current `0x02` = v0.2                                  |
| **FLAGS**   | 1      | Bit-mask: `COMP` (zstd), `CRYPT` (AEAD), `MORE` (chunking), `LARGE` (8-byte PLEN) |
| **HLEN**    | 2      | Header length *n* (bytes)                                                         |
| **HEADER**  | *n*    | Cap'n Proto **FrameHeader** struct (§4.2)                                         |
| **PLEN**    | 4 or 8 | Payload length *p* (pre-CRC, post-compress)                                       |
| **PAYLOAD** | *p*    | DATA or CONTROL body. DATA body-format = *Encoded Ether*.                         |
| **CRC32C**  | 4      | Castagnoli CRC of compressed+encrypted payload                                    |

Compression -> Encryption ordering is fixed to maximise compression ratio and avoid known chosen-ciphertext risks.

### 4.2 FrameHeader Definition

```capnp
@0xbf514fc46d4d410b;
struct FrameHeader {
  channelId  @0 :UInt32;               # QUIC/HTTP2 stream id
  msgType    @1 :UInt16;               # 0x0000‥0x00FF CONTROL, 0x0100 DATA
  bodyCodec  @2 :UInt16;               # see §5.1
  schemaKey  @3 :SchemaKey;            # identifies Ether schema (for DATA)
  msgId      @4 :UInt64;               # for ACK/NACK, dedup
  inReplyTo  @5 :UInt64;               # correlation id (0 if none)
  tags       @6 :List(Tag);            # free-form key/value
}
struct SchemaKey {
  nsHash   @0 :UInt32;                 # FNV-1a namespace hash
  kindId   @1 :UInt32;                 # FNV-1a of kind string
  major    @2 :UInt16;                 # breaking version
  minor    @3 :UInt16;                 # additive version
  hash128  @4 :Data(16);               # first 128 bits SHA-256 canonical schema JSON
}
struct Tag { key @0 :Text; val @1 :Text; }
```

### 4.3 Stream & Chunking

* All DATA for a logical message share the same `msgId`.  If payload exceeds negotiated `max_frame_bytes`, sender splits into chunks and sets `MORE=1` on all but the final chunk.  Receiver re-assembles in buffer order.
* Frames on different `channelId`s MAY be interleaved; in-channel ordering is guaranteed by transport.

## 5 Representation (L3 & L4)

### 5.1 Ether Envelope (language-agnostic)

```jsonc
{
  "kind": "embedding",            // REQUIRED -- logical type
  "schema_version": 1,            // REQUIRED -- additive integer $\geq$1
  "payload": {                    // REQUIRED -- kind-defined keys
    "values": [0.1, ...],
    "dim": 768
  },
  "metadata": {                   // REQUIRED -- free-form but reserved keys exist
    "trace_id": "uuid",           // OPTIONAL
    "producer": "embedder@1.2.0",
    "created_at": "2025-07-29T00:01:02Z",
    "lineage": [ { "node": "embedder", "ver": "1.2.0", "ts": "..." } ]
  },
  "extra_fields": {},             // OPTIONAL -- unclassified data
  "attachments": [                // OPTIONAL -- zero or more binaries
    {
      "id": "vec-0",
      "uri": "shm://emb/42",
      "media_type": "application/x-raw-tensor",
      "codec": "TENSOR_F32",
      "shape": [768],
      "dtype": "float32",
      "size_bytes": 3072
    }
  ]
}
```

### 5.2 Codecs

| ID       | Name           | Purpose                               |
| -------- | -------------- | ------------------------------------- |
| `0x0001` | `JSON`         | Human-readable debug / small messages |
| `0x0002` | `TENSOR_F32`   | Raw LE float32 (header §5.3)          |
| `0x0003` | `TENSOR_F16`   | Raw LE float16                        |
| `0x0004` | `TENSOR_QNT8`  | INT8 + per-row scale/zero-pt          |
| `0x0010` | `MIXED_LATENT` | Varint-delimited tensor segments      |
| `0x0020` | `ARROW_IPC`    | Columnar batches / tables             |
| `0x0021` | `DLPACK`       | GPU tensor hand-off                   |

### 5.3 Tensor Header (`TENSOR_*` codecs)

A fixed 32-byte header precedes the raw bytes:

| Off | B   | Field   | Description                        |
| --- | --- | ------- | ---------------------------------- |
| 0   | 1   | `ndim`  | Rank (1‥8)                         |
| 1   | 1   | `dtype` | Enum `F32=0,F16=1,I8=2`            |
| 2   | 1   | `flags` | Bit0 row-quantised, Bit1 col-major |
| 3   | 1   | *pad*   | 0                                  |
| 4   | 4x8 | `shape` | Dims (unused dims = 0)             |
| 28  | 4   | `scale` | Row/global scale (float)           |

## 6 Semantics (L5)

### 6.1 Schema Registry

Schemes are identified by **SchemaKey**. A registry file (`schemas/index.json`) lists canonical JSON Schema per `(namespace, kind, major, minor)`.

* **Minor** increments are additive and backward-compatible.
* **Major** increments may break; a converter MUST be advertised via CAPS for cross-major exchange.

### 6.2 Standard Kinds (baseline)

| Kind        | Version | Purpose                |
| ----------- | ------- | ---------------------- |
| `text`      | 1       | Plain UTF-8 text       |
| `tokens`    | 1       | Token IDs + mask       |
| `embedding` | 1       | Vector of float32/INT8 |
| `image`     | 1       | Image tensor (H, W, C) |

Adapters may promote/downgrade between versions (e.g., `embedding v1` <-> `embedding v2`).

## 7 Application Layer (L6)

Agents exchange domain messages whose *body* is an Ether encoded using any mutually supported codec.  Tool-specific contracts (e.g., "vector-search request") sit atop Ether and reference a particular `kind`.

## 8 Control, Clarification & Errors

### 8.1 Control Message Types

| `msgType` | Name          | Payload schema                                                                  |
| --------- | ------------- | ------------------------------------------------------------------------------- |
| `0x0000`  | `HELLO`       | Agent capabilities: codecs, max\_frame\_bytes, accepted `(kind,major,[minor...])` |
| `0x0001`  | `ACK`         | Acknowledges `msgId`                                                            |
| `0x0002`  | `NACK`        | Numeric `error_code`, optional `retry_after_ms`                                 |
| `0x0003`  | `PING`        | Keep-alive                                                                      |
| `0x0004`  | `PONG`        | Response                                                                        |
| `0x0005`  | `CLARIFY_REQ` | Ask peer to supply missing/ambiguous fields                                     |
| `0x0006`  | `CLARIFY_RES` | Answer with `inReplyTo`, optional `idempotency_key`                             |

### 8.2 Error Codes

| Code     | Description             |
| -------- | ----------------------- |
| `0x0000` | `OK`                    |
| `0x0001` | `ERR_SCHEMA_UNKNOWN`    |
| `0x0002` | `ERR_CODEC_UNSUPPORTED` |
| `0x0003` | `ERR_MESSAGE_TOO_LARGE` |
| `0x0004` | `ERR_KIND_MISMATCH`     |

Retries SHOULD back-off with `delay = rand(0, base.2^attempt)`.

## 9 Security

* **Transport layer** MUST provide confidentiality & integrity (QUIC + mTLS or HTTP/2 + mTLS).
* If `CRYPT=1`, payload is encrypted with **ChaCha20-Poly1305** (nonce = `HMAC-SHA256(static_key, msgId||channelId)[0:12]`).
* CRC32C footer enables early discard of corrupt frames prior to AEAD verification.

## 10 Reference Implementation

A Python reference lives in `examples/py-xcp/` and showcases:

1. Cap'n Proto framing & CRC.
2. Ether <-> JSON / Arrow IPC / DLPack codecs.
3. Adapter graph with costed Dijkstra routing.
4. Interop tests with Go implementation.

## 11 Glossary

See §2 Terminology for key concepts.

## 12 Versioning Note

This specification supersedes earlier drafts; network peers MUST advertise their major/minor in `HELLO` and decline frames with a higher major.

## 13 References

* Cap'n Proto Schema Language, rev 1.0.
* Apache Arrow IPC Format 1.0.
* DLPack v0.7.
* RFC 9000 (QUIC).
* OpenTelemetry Spec 1.23 (for trace fields).
