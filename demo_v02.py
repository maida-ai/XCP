#!/usr/bin/env python3
# type: ignore
"""XCP v0.2 Demo - Showcase the new protocol features."""

import threading
import time

from xcp import Client, CodecID, Ether, Server


def demo_ether_envelopes() -> None:
    """Demo Ether envelope functionality."""
    print("=== XCP v0.2 Demo: Ether Envelopes ===")

    # Start server
    server = Server("127.0.0.1", 9950)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    try:
        # Create client
        client = Client("127.0.0.1", 9950)

        # Demo different Ether types
        print("\n1. Text Ether:")
        text_ether = Ether.create_text("Hello, XCP v0.2!")
        response = client.send_ether(text_ether)
        print(f"   Sent: {text_ether.kind} - {text_ether.payload['text']}")
        print(f"   Response: {response.header.msg_type}")

        print("\n2. Embedding Ether:")
        embedding_ether = Ether.create_embedding([0.1, 0.2, 0.3, 0.4], 4)
        response = client.send_ether(embedding_ether)
        print(f"   Sent: {embedding_ether.kind} - {len(embedding_ether.payload['values'])} values")
        print(f"   Response: {response.header.msg_type}")

        print("\n3. Tokens Ether:")
        tokens_ether = Ether.create_tokens([1, 2, 3, 4, 5], [True, True, False, True, True])
        response = client.send_ether(tokens_ether)
        print(f"   Sent: {tokens_ether.kind} - {len(tokens_ether.payload['token_ids'])} tokens")
        print(f"   Response: {response.header.msg_type}")

        client.close()

    finally:
        server.stop()


def demo_capability_negotiation() -> None:
    """Demo capability negotiation."""
    print("\n=== XCP v0.2 Demo: Capability Negotiation ===")

    # Start server
    server = Server("127.0.0.1", 9951)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    try:
        # Create client
        client = Client("127.0.0.1", 9951)

        # Show negotiated capabilities
        print(f"\nSupported codecs: {client.supported_codecs}")
        print(f"Server capabilities: {client.server_capabilities}")

        # Test different codecs
        print("\nTesting different codecs:")

        # JSON codec (small message)
        small_ether = Ether.create_text("Small message")
        response = client.send_ether(small_ether, CodecID.JSON)
        print(f"   JSON codec: {response.header.body_codec}")

        # Protobuf codec (larger message)
        large_ether = Ether(
            kind="large_test",
            schema_version=1,
            payload={"data": "x" * 1000},  # Large payload
            metadata={"source": "demo", "timestamp": time.time()},
        )
        response = client.send_ether(large_ether, CodecID.PROTOBUF)
        print(f"   Protobuf codec: {response.header.body_codec}")

        client.close()

    finally:
        server.stop()


def demo_ping_pong() -> None:
    """Demo PING/PONG functionality."""
    print("\n=== XCP v0.2 Demo: PING/PONG ===")

    # Start server
    server = Server("127.0.0.1", 9952)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    try:
        # Create client
        client = Client("127.0.0.1", 9952)

        # Send multiple PINGs
        for i in range(3):
            response = client.ping()
            print(f"   PING {i+1}: {response.header.msg_type}")
            time.sleep(0.1)

        client.close()

    finally:
        server.stop()


def main():
    """Run all demos."""
    print("XCP v0.2 Implementation Demo")
    print("=" * 40)

    try:
        demo_ether_envelopes()
        demo_capability_negotiation()
        demo_ping_pong()

        print("\n" + "=" * 40)
        print("✓ All demos completed successfully!")
        print("\nKey XCP v0.2 Features Demonstrated:")
        print("- Ether envelope self-describing data")
        print("- Capability negotiation (HELLO/CAPS)")
        print("- Multiple codec support (JSON/Protobuf)")
        print("- CRC32C validation")
        print("- PING/PONG keep-alive")

    except Exception as e:
        print(f"Demo failed: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
