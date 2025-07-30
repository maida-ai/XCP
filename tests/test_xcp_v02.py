"""Tests for XCP v0.2 implementation."""

import threading
import time

import pytest

from xcp import Client, CodecID, Ether, MsgType, Server, get_codec, list_codecs


def test_basic_ether_echo() -> None:
    """Test basic Ether echo functionality."""
    print("Testing basic Ether echo functionality...")

    # Start server
    server = Server("127.0.0.1", 9946)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Wait for server to start
    time.sleep(0.1)

    try:
        # Create client
        client = Client("127.0.0.1", 9946)

        # Create test Ether
        test_ether = Ether.create_text("Hello, XCP v0.2!")

        # Send Ether and receive response
        response = client.send_ether(test_ether)

        # Verify echo
        assert response.header.msg_type == MsgType.DATA
        assert response.header.body_codec == CodecID.JSON

        # Decode response
        json_codec = get_codec(CodecID.JSON)
        response_ether = json_codec.decode(response.payload)

        assert isinstance(response_ether, Ether)
        assert response_ether.kind == "text"
        assert response_ether.payload["text"] == "Hello, XCP v0.2!"
        print("✓ Basic Ether echo test passed")

        client.close()

    finally:
        server.stop()


def test_codec_negotiation() -> None:
    """Test codec capability negotiation."""
    print("Testing codec negotiation...")

    # Start server
    server = Server("127.0.0.1", 9947)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Wait for server to start
    time.sleep(0.1)

    try:
        # Create client
        client = Client("127.0.0.1", 9947)

        # Check supported codecs
        supported = client.supported_codecs
        assert CodecID.JSON in supported
        assert CodecID.PROTOBUF in supported
        print(f"✓ Supported codecs: {supported}")

        # Check server capabilities
        capabilities = client.server_capabilities
        assert "codecs" in capabilities
        assert "max_frame_bytes" in capabilities
        print(f"✓ Server capabilities: {capabilities}")

        client.close()

    finally:
        server.stop()


def test_ping_pong() -> None:
    """Test PING/PONG functionality."""
    print("Testing PING/PONG functionality...")

    # Start server
    server = Server("127.0.0.1", 9948)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Wait for server to start
    time.sleep(0.1)

    try:
        # Create client
        client = Client("127.0.0.1", 9948)

        # Send PING
        response = client.ping()

        # Verify PONG response
        assert response.header.msg_type == MsgType.PONG
        assert response.header.body_codec == CodecID.JSON

        # Decode response
        json_codec = get_codec(CodecID.JSON)
        pong_data = json_codec.decode(response.payload)
        assert "nonce" in pong_data
        print("✓ PING/PONG test passed")

        client.close()

    finally:
        server.stop()


def test_ether_creation() -> None:
    """Test Ether envelope creation methods."""
    print("Testing Ether creation methods...")

    # Test text Ether
    text_ether = Ether.create_text("Hello world")
    assert text_ether.kind == "text"
    assert text_ether.schema_version == 1
    assert text_ether.payload["text"] == "Hello world"

    # Test embedding Ether
    embedding_ether = Ether.create_embedding([0.1, 0.2, 0.3], 3)
    assert embedding_ether.kind == "embedding"
    assert embedding_ether.schema_version == 1
    assert embedding_ether.payload["values"] == [0.1, 0.2, 0.3]
    assert embedding_ether.payload["dim"] == 3

    # Test tokens Ether
    tokens_ether = Ether.create_tokens([1, 2, 3, 4], [True, True, False, True])
    assert tokens_ether.kind == "tokens"
    assert tokens_ether.schema_version == 1
    assert tokens_ether.payload["token_ids"] == [1, 2, 3, 4]
    assert tokens_ether.payload["mask"] == [True, True, False, True]

    # Test image Ether
    image_data = b"fake_image_data"
    image_ether = Ether.create_image(100, 200, 3, image_data)
    assert image_ether.kind == "image"
    assert image_ether.schema_version == 1
    assert image_ether.payload["height"] == 100
    assert image_ether.payload["width"] == 200
    assert image_ether.payload["channels"] == 3
    assert image_ether.payload["data"] == image_data

    print("✓ Ether creation test passed")


def test_codec_registry() -> None:
    """Test codec registry functionality."""
    print("Testing codec registry...")

    # Check available codecs
    codecs = list_codecs()
    assert CodecID.JSON in codecs
    assert CodecID.PROTOBUF in codecs
    print(f"✓ Available codecs: {codecs}")

    # Test getting codec
    json_codec = get_codec(CodecID.JSON)
    assert json_codec is not None

    # Test unsupported codec
    with pytest.raises(ValueError):
        get_codec(0x9999)

    print("✓ Codec registry test passed")


if __name__ == "__main__":
    test_basic_ether_echo()
    test_codec_negotiation()
    test_ping_pong()
    test_ether_creation()
    test_codec_registry()
    print("All XCP v0.2 tests passed!")
