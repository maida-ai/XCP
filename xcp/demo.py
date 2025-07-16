#!/usr/bin/env python3
"""Demo module for XCP functionality."""

import threading
import time
import json
from . import Server, Client, Frame, FrameHeader, MsgType, CodecID

def run_demo():
    """Run a complete XCP demo."""
    print("XCP Demo - Client-Server Communication")
    print("=" * 40)

    # Start server in background thread
    server = Server("127.0.0.1", 9944)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Wait for server to start
    time.sleep(0.5)

    try:
        # Create client
        client = Client("127.0.0.1", 9944)

        # Send a simple text message
        print("Sending text message...")
        text_frame = Frame(
            header=FrameHeader(
                channelId=0,
                msgType=MsgType.DATA,
                bodyCodec=CodecID.JSON,
                schemaId=0,
            ),
            payload=b"Hello from XCP client!"
        )
        response = client.request(text_frame)
        print(f"Received: {response.payload.decode()}")

        # Send a JSON message
        print("Sending JSON message...")
        json_data = {
            "type": "greeting",
            "message": "Hello from XCP!",
            "timestamp": time.time(),
            "data": [1, 2, 3, 4, 5]
        }
        json_frame = Frame(
            header=FrameHeader(
                channelId=0,
                msgType=MsgType.DATA,
                bodyCodec=CodecID.JSON,
                schemaId=0,
            ),
            payload=json.dumps(json_data).encode()
        )
        response = client.request(json_frame)
        print(f"Received: {response.payload.decode()}")

        # Send binary data
        print("Sending binary data...")
        binary_data = b"Binary payload with \x00\x01\x02\x03 bytes"
        binary_frame = Frame(
            header=FrameHeader(
                channelId=0,
                msgType=MsgType.DATA,
                bodyCodec=CodecID.BINARY,
                schemaId=0,
            ),
            payload=binary_data
        )
        response = client.request(binary_frame)
        print(f"Received binary data: {len(response.payload)} bytes")

        client.close()
        print("Client finished.")

    finally:
        server.stop()

    print("\nDemo completed!")

def main():
    """Main entry point for the demo."""
    run_demo()

if __name__ == "__main__":
    main()
