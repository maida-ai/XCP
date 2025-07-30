#!/usr/bin/env python3
"""Simple test script for the XCP v0.2 implementation."""

import threading
import time

from xcp import Client, Ether, MsgType, Server


def test_basic_echo() -> None:
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

        # Send a test Ether
        test_ether = Ether.create_text("Hello, XCP!")
        response = client.send_ether(test_ether)

        # Verify echo
        assert response.header.msg_type == MsgType.DATA
        print("✓ Basic echo test passed")

        client.close()

    finally:
        server.stop()


def test_json_payload() -> None:
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

        # Send JSON payload using Ether
        test_ether = Ether(kind="test", schema_version=1, payload={"message": "Hello", "number": 42})
        response = client.send_ether(test_ether)

        # Verify echo
        assert response.header.msg_type == MsgType.DATA
        print("✓ JSON payload test passed")

        client.close()

    finally:
        server.stop()


if __name__ == "__main__":
    test_basic_echo()
    test_json_payload()
    print("All tests passed!")
