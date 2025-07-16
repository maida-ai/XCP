<!---
Copyright © 2025 Maida.AI contributors.
Licensed under CC-BY-4.0: https://creativecommons.org/licenses/by/4.0/
-->
# XCP Implementation

This directory contains a minimal proof-of-concept implementation of the **XCP (eXtensible Coordination Protocol)** as specified in `revisions/xcp-v0.1.md`.

## Overview

The implementation provides:

- **Binary frame format** following the XCP v0.1 specification
- **Client/Server classes** for easy communication
- **Frame and FrameHeader classes** for structured message handling
- **Support for multiple codecs** (JSON, binary, tensor formats)
- **Basic handshake protocol** (HELLO/CAPS_ACK)
- **Backward compatibility** with the original PoC API

## Architecture

The XCP package is organized into logical modules for better maintainability:

```
xcp/
├── __init__.py          # Public API exports
├── constants.py         # Protocol constants and enums
├── frames.py           # Frame structures and serialization
├── client.py           # Client implementation
├── server.py           # Server implementation
├── legacy.py           # Backward compatibility API
└── demo.py             # Demo functionality
```

### Frame Structure

The implementation follows the XCP v0.1 frame format:

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

- **MAGIC**: `0xA9A17A10` (identifies XCP)
- **VER**: Version byte (major/minor)
- **FLAGS**: Compression, crypto, more data, large payload flags
- **HLEN**: Header length in bytes
- **HEADER**: JSON-encoded header structure
- **PLEN**: Payload length (4 or 8 bytes)
- **PAYLOAD**: Actual message data

### Core Classes

#### `Frame` and `FrameHeader`

```python
@dataclass
class FrameHeader:
    channelId: int = 0
    msgType: int = MsgType.DATA
    bodyCodec: int = CodecID.JSON
    schemaId: int = 0
    msgId: int = 0
    inReplyTo: int = 0
    tags: list = None

@dataclass
class Frame:
    header: FrameHeader
    payload: bytes
```

#### `Client`

```python
client = Client("127.0.0.1", 9944)
frame = Frame(header=FrameHeader(...), payload=b"data")
response = client.request(frame)
client.close()
```

#### `Server`

```python
def custom_handler(frame: Frame) -> Frame:
    # Process frame and return response
    return response_frame

server = Server("127.0.0.1", 9944, on_frame=custom_handler)
server.serve_forever()
```

## Message Types

| Type | Value | Description |
|------|-------|-------------|
| `HELLO` | `0x01` | Initial handshake |
| `CAPS_ACK` | `0x02` | Capability acknowledgment |
| `PING` | `0x03` | Keep-alive |
| `PONG` | `0x04` | Keep-alive response |
| `NEGOTIATE` | `0x05` | Mid-session codec switch |
| `UNSUPPORTED` | `0x06` | Unknown codec/schema |
| `ACK` | `0x07` | Acknowledgment |
| `NACK` | `0x08` | Negative acknowledgment |
| `DATA` | `0x20` | Data payload |

## Codecs

| ID | Codec | Description |
|----|-------|-------------|
| `0x01` | `JSON` | UTF-8 JSON encoding |
| `0x02` | `TENSOR_F32` | IEEE-754 float32 |
| `0x03` | `TENSOR_F16` | IEEE-754 float16 |
| `0x04` | `TENSOR_QNT8` | INT8 quantization |
| `0x10` | `BINARY` | Raw binary data |

## Usage Examples

### Basic Echo Server

```python
from xcp import Server, Frame, FrameHeader, MsgType

def echo_handler(frame):
    if frame.header.msgType == MsgType.DATA:
        # Echo the frame back
        response_header = FrameHeader(
            channelId=frame.header.channelId,
            msgType=frame.header.msgType,
            bodyCodec=frame.header.bodyCodec,
            schemaId=frame.header.schemaId,
            inReplyTo=frame.header.msgId
        )
        return Frame(header=response_header, payload=frame.payload)
    return None

server = Server("127.0.0.1", 9944, on_frame=echo_handler)
server.serve_forever()
```

### Client Communication

```python
from xcp import Client, Frame, FrameHeader, MsgType, CodecID

client = Client("127.0.0.1", 9944)

# Send JSON data
json_data = {"message": "Hello", "number": 42}
frame = Frame(
    header=FrameHeader(
        msgType=MsgType.DATA,
        bodyCodec=CodecID.JSON
    ),
    payload=json.dumps(json_data).encode()
)

response = client.request(frame)
print(f"Received: {response.payload.decode()}")

client.close()
```

### Custom Frame Handler

```python
def my_handler(frame: Frame) -> Frame:
    if frame.header.msgType == MsgType.DATA:
        # Process the payload
        payload = frame.payload.decode()
        processed = f"Processed: {payload}"

        # Return response
        response_header = FrameHeader(
            msgType=MsgType.DATA,
            bodyCodec=CodecID.JSON,
            inReplyTo=frame.header.msgId
        )
        return Frame(header=response_header, payload=processed.encode())
    return None

server = Server("127.0.0.1", 9944, on_frame=my_handler)
```

## Benchmarking

The implementation includes a benchmark script that compares XCP performance against HTTP/2:

```bash
# Install dependencies
pip install -r requirements.txt

# Run benchmark
python benchmarks/poc_http2_vs_xcp.py --runs 1000 --size 10240
```

## Testing

Run the test suite:

```bash
python test_xcp.py
```

Run the demo:

```bash
python demo.py
```

## Limitations

This is a **proof-of-concept implementation** with the following limitations:

- **Single TCP connection** (no QUIC support)
- **No multiplexing** (single channel)
- **Basic handshake** (no full capability negotiation)
- **No compression** or encryption
- **No schema validation**
- **No flow control** or congestion management

## Future Enhancements

- QUIC transport support
- Multi-channel multiplexing
- Full capability negotiation
- Compression and encryption
- Schema validation
- Flow control and congestion management
- Tensor codec implementations
- Semantic clarification frames

## API Reference

### `Client(host, port)`

Creates a client connection to an XCP server.

**Methods:**
- `request(frame: Frame) -> Frame`: Send a frame and wait for response
- `close()`: Close the connection

### `Server(host, port, on_frame=None)`

Creates an XCP server.

**Methods:**
- `serve_forever()`: Start the server and handle connections
- `stop()`: Stop the server

**Parameters:**
- `on_frame`: Optional callback function `(Frame) -> Frame` for custom frame handling

### `Frame(header, payload)`

Represents an XCP frame.

**Attributes:**
- `header`: FrameHeader instance
- `payload`: bytes payload

### `FrameHeader(...)`

Represents frame header information.

**Attributes:**
- `channelId`: Channel identifier
- `msgType`: Message type (see MsgType enum)
- `bodyCodec`: Codec identifier (see CodecID enum)
- `schemaId`: Schema identifier
- `msgId`: Message identifier
- `inReplyTo`: Reply-to message identifier
- `tags`: List of tags

## License

This implementation is provided under the MIT License for the reference code, with documentation under CC-BY-4.0.
