#!/usr/bin/env python3
"""Simple test script for the XCP implementation."""

import threading
import time
from xcp import Server, Client, Frame, FrameHeader, MsgType, CodecID

def test_basic_echo():
    """Test basic echo functionality."""
    print("Testing basic echo functionality...")

    # Start server
    server = Server("127.0.0.1", 9944)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Wait for server to start
    time.sleep(0.1)

    try:
        # Create client
        client = Client("127.0.0.1", 9944)

        # Send a test frame
        test_payload = b"Hello, XCP!"
        frame = Frame(
            header=FrameHeader(
                channelId=0,
                msgType=MsgType.DATA,
                bodyCodec=CodecID.JSON,
                schemaId=0,
            ),
            payload=test_payload
        )

        # Send and receive
        response = client.request(frame)

        # Verify echo
        assert response.payload == test_payload, f"Expected {test_payload}, got {response.payload}"
        print("✓ Basic echo test passed")

        client.close()

    finally:
        server.stop()

def test_json_payload():
    """Test JSON payload handling."""
    print("Testing JSON payload handling...")

    # Start server
    server = Server("127.0.0.1", 9945)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Wait for server to start
    time.sleep(0.1)

    try:
        # Create client
        client = Client("127.0.0.1", 9945)

        # Send JSON payload
        import json
        test_data = {"message": "Hello", "number": 42}
        test_payload = json.dumps(test_data).encode()

        frame = Frame(
            header=FrameHeader(
                channelId=0,
                msgType=MsgType.DATA,
                bodyCodec=CodecID.JSON,
                schemaId=0,
            ),
            payload=test_payload
        )

        # Send and receive
        response = client.request(frame)

        # Verify echo
        assert response.payload == test_payload, f"Expected {test_payload}, got {response.payload}"
        print("✓ JSON payload test passed")

        client.close()

    finally:
        server.stop()

if __name__ == "__main__":
    test_basic_echo()
    test_json_payload()
    print("All tests passed!")
