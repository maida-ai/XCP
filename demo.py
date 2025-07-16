#!/usr/bin/env python3
"""Demo script showing XCP client-server communication."""

import threading
import time
import json
from xcp import Server, Client, Frame, FrameHeader, MsgType, CodecID

def demo_server():
    """Run a simple XCP server that echoes messages."""
    print("Starting XCP server on 127.0.0.1:9944...")

    def custom_handler(frame: Frame) -> Frame:
        """Custom frame handler that logs and echoes messages."""
        if frame.header.msgType == MsgType.DATA:
            print(f"Server received: {frame.payload[:50]}...")

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

    server = Server("127.0.0.1", 9944, on_frame=custom_handler)
    server.serve_forever()

def demo_client():
    """Run a simple XCP client that sends messages."""
    print("Starting XCP client...")
    time.sleep(0.5)  # Wait for server to start

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

def main():
    """Run the demo."""
    print("XCP Demo - Client-Server Communication")
    print("=" * 40)

    # Start server in background thread
    server_thread = threading.Thread(target=demo_server, daemon=True)
    server_thread.start()

    # Run client
    demo_client()

    print("\nDemo completed!")

if __name__ == "__main__":
    main()
