# XCP Package Structure

This document describes the modular structure of the XCP package after refactoring.

## Overview

The XCP package has been restructured into logical modules for better maintainability, readability, and separation of concerns. Each module has a specific responsibility and clear interfaces.

## Module Structure

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

## Module Responsibilities

### `constants.py`
**Purpose**: Protocol constants and enumerations

**Contents**:
- `MAGIC`: Protocol magic number (`0xA9A17A10`)
- `VERSION_BYTE`: Protocol version
- `Flag`: Frame flags (COMP, CRYPT, MORE, LARGE)
- `CodecID`: Supported codec identifiers
- `MsgType`: Message type identifiers

**Usage**:
```python
from xcp.constants import MsgType, CodecID, Flag
```

### `frames.py`
**Purpose**: Frame structures and serialization/deserialization

**Contents**:
- `FrameHeader`: Frame header dataclass
- `Frame`: Complete frame dataclass
- `pack_frame()`: Serialize frame to binary
- `parse_frame()`: Deserialize frame from binary
- `recv_exact()`: Socket utility for exact byte reception

**Usage**:
```python
from xcp.frames import Frame, FrameHeader, pack_frame, parse_frame
```

### `client.py`
**Purpose**: Client-side connection and communication

**Contents**:
- `Client`: Main client class for sending frames to servers

**Features**:
- Automatic connection establishment
- HELLO/CAPS_ACK handshake
- Thread-safe message ID generation
- Frame request/response handling

**Usage**:
```python
from xcp.client import Client

client = Client("127.0.0.1", 9944)
response = client.request(frame)
client.close()
```

### `server.py`
**Purpose**: Server-side connection handling and frame processing

**Contents**:
- `Server`: Main server class for accepting connections
- `_ClientHandler`: Internal class for per-client handling

**Features**:
- Multi-client support (thread-per-client)
- Custom frame handler callbacks
- Automatic HELLO/CAPS_ACK handshake
- Default echo handler

**Usage**:
```python
from xcp.server import Server

def my_handler(frame):
    # Process frame and return response
    return response_frame

server = Server("127.0.0.1", 9944, on_frame=my_handler)
server.serve_forever()
```

### `legacy.py`
**Purpose**: Backward compatibility with original API

**Contents**:
- `XCPConnection`: Legacy client connection class
- `open()`: Legacy connection function
- `EchoServer`: Legacy server class
- `run_echo_server()`: Legacy context manager

**Usage**:
```python
from xcp.legacy import open, run_echo_server

# Legacy API
conn = open("127.0.0.1", 9944)
response = conn.send(b"Hello")
conn.close()
```

### `demo.py`
**Purpose**: Demonstration functionality

**Contents**:
- `run_demo()`: Complete client-server demo
- `main()`: Demo entry point

**Usage**:
```python
from xcp.demo import run_demo
run_demo()
```

### `__init__.py`
**Purpose**: Public API exports and package documentation

**Contents**:
- Clean imports from all modules
- Comprehensive `__all__` list
- Package documentation

**Exports**:
```python
# Core classes
Frame, FrameHeader, Client, Server

# Constants and enums
MAGIC, VERSION_BYTE, Flag, CodecID, MsgType

# Frame utilities
pack_frame, parse_frame

# Legacy API
open, run_echo_server, XCPConnection, EchoServer
```

## Import Patterns

### Recommended (New API)
```python
from xcp import Client, Server, Frame, FrameHeader, MsgType, CodecID

# Or for specific modules
from xcp.client import Client
from xcp.server import Server
from xcp.frames import Frame, FrameHeader
from xcp.constants import MsgType, CodecID
```

### Legacy Compatibility
```python
from xcp import open, run_echo_server, XCPConnection
```

## Benefits of New Structure

### 1. **Separation of Concerns**
- Each module has a single, well-defined responsibility
- Constants are isolated from implementation
- Frame handling is separate from network logic

### 2. **Maintainability**
- Easier to locate and modify specific functionality
- Clear dependencies between modules
- Reduced coupling between components

### 3. **Readability**
- Smaller, focused files are easier to understand
- Clear module names indicate purpose
- Logical organization of related functionality

### 4. **Testability**
- Individual modules can be tested in isolation
- Mock dependencies are easier to create
- Unit tests can focus on specific functionality

### 5. **Extensibility**
- New features can be added to appropriate modules
- Protocol extensions can be isolated
- Backward compatibility is maintained separately

## Migration Guide

### For Existing Code
Most existing code will continue to work without changes:

```python
# This still works
from xcp import Client, Server, Frame, FrameHeader

# As does this
from xcp import open, run_echo_server
```

### For New Code
Use the new modular imports for better organization:

```python
# For client code
from xcp.client import Client
from xcp.frames import Frame, FrameHeader
from xcp.constants import MsgType, CodecID

# For server code
from xcp.server import Server
from xcp.frames import Frame, FrameHeader
```

## Internal Dependencies

```
constants.py
    ↓ (imported by)
frames.py
    ↓ (imported by)
client.py, server.py
    ↓ (imported by)
legacy.py
    ↓ (imported by)
__init__.py
```

This structure ensures that:
- Constants are available to all modules
- Frame utilities are available to client/server
- Legacy code can access all functionality
- Public API is cleanly exported

## Future Extensions

The modular structure makes it easy to add new features:

- **New codecs**: Add to `constants.py` and implement in `frames.py`
- **New message types**: Add to `constants.py` and handle in `client.py`/`server.py`
- **New transport**: Create new `transport.py` module
- **Authentication**: Create new `auth.py` module
- **Compression**: Create new `compression.py` module

This structure provides a solid foundation for the XCP protocol implementation while maintaining clean separation of concerns and backward compatibility.
