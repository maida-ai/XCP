<!---
Copyright © 2025 Maida.AI contributors.
Licensed under CC-BY-4.0: https://creativecommons.org/licenses/by/4.0/
-->
# XCP Codec Reference *(Non-Normative)*

- Version: 0.2

This appendix catalogues every **`bodyCodec`** identifier defined for XCP v0.2 and explains the exact byte layout, recommended use-cases, and interoperability tips. It supplements the normative specification but **implementations remain conformant even if they only support JSON (0x0001)**.

> All multibyte integers are **unsigned little-endian (LE)**.

## 1  Registry Overview

| ID       | Name           | Typical payload      | Max efficiency when      | Wire media-type (suggested)           |
| -------- | -------------- | -------------------- | ------------------------ | ------------------------------------- |
| `0x0001` | `JSON`         | $\leq$ 8 KB control/data  | human-debuggable         | `application/json`                    |
| `0x0002` | `TENSOR_F32`   | dense float32 tensor | low-latency GPU ingest   | `application/x-raw-tensor`            |
| `0x0003` | `TENSOR_F16`   | dense float16 tensor | bandwidth-sensitive      | same as above                         |
| `0x0004` | `TENSOR_QNT8`  | row-quantised INT8   | mass batch / index build | same as above                         |
| `0x0008` | `PROTOBUF`     | any Ether            | multi-lang control       | `application/x-protobuf`              |
| `0x0010` | `MIXED_LATENT` | hetero tensors       | researchʹy encodings     | `application/x-latent-set`            |
| `0x0020` | `ARROW_IPC`    | columnar tables      | zero-copy, batching      | `application/vnd.apache.arrow.stream` |
| `0x0021` | `DLPACK`       | device tensors       | GPU-GPU hop              | `application/x-dlpack`                |
| `0x00FF` | `RESERVED`     | --                    | future                   | --                                     |

> **Adding a codec** -- submit a PR that appends a new row and reserves one byte ID.  IDs `0x0040--0x00EF` are unallocated.

## 2  `JSON` (0x0001)

* **Encoding**: UTF-8, no BOM, canonical whitespace optional.  Entries in `Ether.attachments[*].inline_bytes` MUST be base64.
* **Size hint**: if body > 64 KB, sender SHOULD prefer a binary codec.
* **Compression**: enable `COMP` flag only if payload > 1 KB.

### Example

```http
Content-Type: application/json

{"kind":"text","schema_version":1,"payload":{"text":"hello"},"metadata":{}}
```

## 3  Raw Tensor Codecs `TENSOR_*` (0x0002-0x0004)

All share the 32-byte header below followed by a contiguous data blob.  **No padding between rows.**

| Byte offset | Field      | Bytes | Description                                                          |
| ----------- | ---------- | ----- | -------------------------------------------------------------------- |
| 0           | `ndim`     | 1     | Number of dimensions (1--8)                                           |
| 1           | `dtype`    | 1     | Enum: `0=F32`, `1=F16`, `2=INT8`                                     |
| 2           | `flags`    | 1     | bit0 `row_qnt` (per-row quantised), bit1 `col_major`                 |
| 3           | *reserved* | 1     | `0x00`                                                               |
| 4           | `shape[0]` | 4     | dim-0                                                                |
| 8           | `shape[1]` | 4     | ...                                                                    |
| ...           | ...          | ...     | unused dims = 0                                                      |
| 28          | `scale`    | 4     | `float32`; if `row_qnt=1`, this is *global default*, else per-tensor |

### 3.1  `TENSOR_F32` (0x0002)

* **Body**: `float32` values in **row-major** unless `flags.col_major=1`.
* **Size**: `N = ∏ shape[i]`; bytes = `4xN`.
* **Attachments**: if tensor > 32 KB, spec RECOMMENDS sending header in payload and placing raw bytes in `Ether.attachments` with `uri` or inline.

### 3.2  `TENSOR_F16` (0x0003)

Identical header. Body is IEEE 754-2008 binary16.

### 3.3  `TENSOR_QNT8` (0x0004)

* **Quantisation**: INT8 values range 0--255.  Denormalise: `f_real = (i -- 128) x scale`.
* If `row_qnt=1`, a `float32` scale factor **prepends each row** (4 bytes) followed by that row's bytes.
* If `row_qnt=0`, scale is global (header's `scale` field).

### Sample decoder (Python)

```python
import numpy as np, struct

def read_tensor(buf: bytes):
    ndim, dtype_id, flags = struct.unpack_from('<BBB', buf, 0)
    shape = struct.unpack_from('<8I', buf, 4)[:ndim]
    scale = struct.unpack_from('<f', buf, 28)[0]
    body = memoryview(buf)[32:]
    if dtype_id == 0:
        return np.frombuffer(body, '<f4').reshape(shape)
    if dtype_id == 1:
        return np.frombuffer(body, '<f2').astype('f4').reshape(shape)
    if dtype_id == 2:
        arr = np.frombuffer(body, 'u1').astype('f4')
        return (arr - 128) * scale
```

## 4  `PROTOBUF` (0x0008)

### 4.1  Message `EtherProto`

See *Implementation Guide* §3.1.  Body is a serialized `EtherProto` message.
**Unknown-field rule**: receivers MUST ignore unknown fields for forward-compatibility.

### 4.2  When to use

* Control frames (`HELLO`, `NACK`) when JSON is too bulky.
* Small data envelopes (<8 KB) when multi-language interoperability matters.

## 5  `MIXED_LATENT` (0x0010)

Experimental codec for research traffic (heterogeneous tensors). Reserved for internal use; not interoperable across vendors.  Layout:

```
<VarUInt tensor_count>
repeat tensor_count:
  <1-byte subtype>  # aligns with 0x0002..0x0004
  <VarUInt length>
  <bytes...>
```

## 6  `ARROW_IPC` (0x0020)

### 6.1  Format

Body is a complete **Arrow IPC stream** (no continuation markers) that serializes one or more `RecordBatch` objects.  The schema **MUST** match the registered `(kind,version)` in the schema registry.

### 6.2  Ether mapping rules

| Ether property      | Arrow location                                     |
| ------------------- | -------------------------------------------------- |
| `payload.*`         | columns                                            |
| `metadata.trace_id` | schema metadata key `trace_id`                     |
| `attachments[...]`  | Arrow **buffermap extension** or Flight descriptor |

### 6.3  Shared-Memory Optimisation

If peers negotiated `shared_mem=true`, the sender MAY:

1. Persist IPC stream to a Posix-SHM file.
2. Set `uri = "shm://arrow/<name>#<off>,<size>"` in **attachments**.
3. Transmit only a JSON stub (\~100 B) instead of the full bytes.
   Receiver maps the same SHM and feeds it to an Arrow reader.

**Throughput**: 1 M embeddings (float32, 768 dim) streamed in < 450 ms host-to-host using SHM vs 2.3 s over raw TCP.

## 7  `DLPACK` (0x0021)

### 7.1  Format

Body is a single **DLPack capsule** (version 0.7) representing a tensor.  No additional header; shape/dtype are in the DL tensor struct.

### 7.2  Usage pattern

* GPU-resident model -> GPU-resident serving worker with zero host-device copy.
* Sender sets `media_type = "application/x-dlpack"` in the DATA frame header; `schemaKey.kindId` must map to a registered tensor kind (`embedding`, `image`, ...).

### 7.3  Lifetime

Receiver MUST consume or copy the tensor **before the sending process frees the original GPU memory**; otherwise UB.

## 8  Codec Capability Flags in `HELLO`

Example JSON (encoded via `JSON` or `PROTOBUF`):

```json
{
  "codecs": [1, 8, 32, 33],
  "max_frame_bytes": 1048576,
  "shared_mem": true
}
```

Peers use the intersection of codec sets to choose the best encoding.

## 9  Extending the Registry

1. Open an issue proposing a new codec (include use-case, media-type, draft layout).
2. Reserve a hex ID in `xcp-v0.2-codecs.md`.
3. Submit reference encoder/decoder tests.
4. On merge, bump **minor** spec version if wire-visible constants are added.
