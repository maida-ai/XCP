<!---
Copyright © 2025 Maida.AI contributors.
Licensed under CC-BY-4.0: https://creativecommons.org/licenses/by/4.0/
-->
# Cross Agent Communication Protocol (XCP)

Owner: Maida.AI

*Status*: **Draft -- July 16 2025**

*Intended Audience*: AI-infrastructure engineers, LLM platform maintainers

*Copyright*: (c) 2025 - CC-BY-4.0

---

## Table of Contents

1. [Introduction](#)
2. [Terminology and Conventions](#)
3. [Architectural Overview](#)
4. [Transport & Framing (L0-L2)](#)
5. [Representation Layer (L3-L4)](#)
6. [Semantic Layer (L5)](#)
7. [Application Layer (L6)](#)
8. [Clarification & Error-Handling](#)
9. [Security Considerations](#)
10. [Backward Compatibility](#)
11. [Reference Implementation](#)
12. [Glossary](#)
13. [References](#)

---

## 1 Introduction

Modern multi-agent systems still rely on ad-hoc, human-oriented message formats (e.g. JSON chat turns).  XCP proposes a **layered, binary-first protocol** that separates *semantics* from *representation*, enabling:

- Transport-efficient interchange of latent vectors, tensors, and multimodal blobs.
- Negotiation of codecs and capabilities at runtime.
- Typed control frames for congestion, negotiation and semantic clarification -- avoiding brittle free-form "AI-chat".

## 2 Terminology and Conventions

The keywords **MUST**, **SHOULD**, **MAY** follow RFC 2119.
"Agent" denotes any autonomous component (LLM, tool wrapper, orchestrator) implementing XCP.
QUIC is per RFC 9000.

## 3 Architectural Overview

```
+-------------+---------------------------------------------------+
| Layer (L6)  | Application  -- task planning, tool orchestration |
| Layer (L5)  | *Semantics*  -- versioned Payload schemas         |
| Layer (L4)  | Representation -- codecs (JSON, TENSOR_F16 ...)   |
| Layer (L3)  | Serialization  -- Envelope, length-prefix         |
| Layer (L2)  | Session / Control -- HELLO, CAPs, ACK/NACK        |
| Layer (L1)  | Framing -- multiplexed streams                    |
| Layer (L0)  | Transport -- TCP, QUIC, IPC                       |
+-------------+---------------------------------------------------+
```

## 4 Transport & Framing (L0-L2)

### 4.1 Frame Layout

```
0      4      5      6      8             8+n
+------+------+------+------+---------------+
|MAGIC |VER   |FLAGS |HLEN  | H E A D E R   |
+------+------+------+------+---------------+
8+n    8+n+4                8+n+4+PAYLOAD_LEN
+------|------------------------------------|
|PLEN  | P A Y L O A D  (compressed?)       |
+------|------------------------------------|
```

| Field | Size | Description |
| --- | --- | --- |
| **MAGIC** | 4 B | `0xA9A17A10` (identifies XCP) |
| **VER** | 1 B | Major (4 bits) / Minor (4 bits) |
| **FLAGS** | 1 B | `COMP`, `CRYPT`, `MORE`, `LARGE` |
| **HLEN** (n) | 2 B | Header length in bytes |
| **HEADER** | n B | Cap'n Proto struct (see #4.2) |
| **PLEN** (p) | 4 B / 8 B | Payload length |
| **PAYLOAD** | m B | Body encoded per `body_codec` |

`LARGE` flag defines the payload length. Parsers **MUST** assume `PLEN` to be 8 B if set.

### 4.2 Header Schema (Cap'n Proto)

```
struct FrameHeader {
  channelId @0 :UInt32;
  msgType   @1 :UInt32;  # CONTROL=0x0*, DATA=0x1*
  bodyCodec @2 :UInt32;  # enum CodecID
  schemaId  @3 :UInt64;  # FNV-1a hash of L5 schema
  msgId     @4 :UInt64;  # optional -- enables ACK/NACK
  inReplyTo @5 :UInt64;  # correlation
  tags      @6 :List(Tag);
}

struct Tag {
  key @0 :UInt16;  # Numeric enum keys
  val @1 :Data;    # Binary blobs supported
}
```

### 4.3 Control Message Types

| `msgType` | Direction | Purpose |
| --- | --- | --- |
| `0x01 HELLO` | C->S | Capability advert, optional auth material |
| `0x02 CAPS_ACK` | S->C | Negotiated codec & window sizes |
| `0x03 PING` / `0x04 PONG` | bi | Keep-alive |
| `0x05 NEGOTIATE` | bi | Mid-session codec switch |
| `0x06 UNSUPPORTED` | bi | Unknown codec/schema |
| `0x07 ACK` / `0x08 NACK` | bi | Reliability |

## 5 Representation Layer (L3-L4)

### 5.1 Codec Registry

| ID | Codec | Notes |
| --- | --- | --- |
| `0x01` | `JSON` | UTF-8 fallback |
| `0x02` | `TENSOR_F32` | Raw IEEE-754 little-endian |
| `0x03` | `TENSOR_F16` | 1/2 size, requires f16 kernels |
| `0x04` | `TENSOR_QNT8` | INT8 + scale/zero-pt per row |
| `0x10` | `MIXED_LATENT` | Varint-delimited segments |

Codecs **MUST** be negotiated during the handshake; senders **SHOULD** default to `JSON` if no intersection exists.

## 6 Semantic Layer (L5)

XCP reuses Pydantic-based **`Payload` registry**.  Each schema gets a 64-bit `schemaId`.

Example:

```python
@Payload.register_schema(
    payload_keys=["embedding","task"],
    metadata_keys=["model","dims","dtype"])
class EmbeddingTask(BaseModel):
    embedding: list[float]
    task: str           # e.g. 'similarity_search'
    model: str          # 'text-xyz-002'
    dims: int           # 1536
    dtype: str          # 'f16'
```
* The wire body is encoded with the negotiated `bodyCodec` (JSON or binary tensor packer).

* ?`FNV-1a` hash of the schema might collide
  * Upgrade to 128 bits? (SipHash, CityHash)
  * Add `(namespace, name, version)` alongside the hash?

## 7 Application Layer (L6)

XCP deliberately leaves higher-level dialogue (e.g. MCP, A2A, ReAct) outside its scope.  Such protocols **SHOULD** use `msgType` >= `0x100`.

## 8 Clarification & Error-Handling

### 8.1 Semantic Clarification Frames

```
enum MsgType {
  CLARIFY_REQ  @0x30;   # missing slot / ambiguity
  CLARIFY_RES  @0x31;   # disambiguation payload
  CONFIRM_REQ  @0x32;   # 'I plan to do X - OK?'
  CONFIRM_RES  @0x33;   # yes / no / correction
}
```

Typical flow:

```
TASK#42 -> ...missing top_k...
        <- CLARIFY_REQ(reason=MISSING_FIELD)
        -> CLARIFY_RES(top_k=20)
TASK#42 -> ...execute...

```

Agents **SHOULD** minimize semantic clarifications via:

- Required fields in schemas.
- Local auto-retry when confidence < \tau.
- Advertising `maxTokens`, `maxVectorDims`, etc., in `HELLO`.

To avoid "thundering-herd" problem, agents **SHOULD** use jitter for consistency across stacks.

```
delay = random.uniform(0, base*2**attempt)
```

### 8.2 Transport Errors

Retransmissions use duplicate `msgId` plus `NACK`.  Idempotent handlers **MUST** drop duplicates.

## 9 Security Considerations

- **Transport**: Mutual TLS or QUIC 1.3.
- **Integrity & Privacy**: Optional AEAD on the *Payload* (set `CRYPT` flag).
- **Replay**: `msgId` + 64-bit nonce; receivers store a sliding window.
- **mTLS Profile**:
  - `TLS_AES_128_GCM_SHA256` (at minimum)
  - Key rotation  at least every 24 hours or 2GB, whichever comes first

## 10 Backward Compatibility

Major version bump (high 4 bits of **V**) indicates breaking change.  Minor versions are additive.  Receivers **MAY** ignore unknown header fields flagged as optional.

* TODO: Multiple schema revisions in `HELLO`? How would receiver pick one?

## 11 Reference Implementation

A Python proof-of-concept lives in `examples/py-xcp/` and demonstrates:

- HTTP/2 + h2 multiplexing for L0-L2.
- Two codecs (`JSON`, `TENSOR_F32`).
- Planner/Worker demo exchanging 10-KB embeddings at ~2.1x speed-up over plain JSON chat.

## 12 Glossary

| Term | Meaning |
| --- | --- |
| **Agent** | Autonomous component implementing XCP |
| **Frame** | Smallest wire unit (header + payload) |
| **Codec** | Algorithm that encodes/decodes `Body` |
| **SchemaId** | 64-bit hash of a Payload schema |

## 13 References

- RFC 2119 -- Key words for use in RFCs.
- Cap'n Proto 1.0 spec.
- QUIC Transport RFC 9000.
- A2A: *Agent-to-Agent Coordination Protocol*, 2024.

---

*This document is a working draft and subject to change prior to formal adoption.*
