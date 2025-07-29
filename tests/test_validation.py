#!/usr/bin/env python3
"""Test script to verify validation logic works correctly."""

import hashlib
import os
from benchmarks.poc_http2_vs_xcp import (
    generate_payload_with_checksum,
    validate_response
)

def test_validation_logic():
    """Test the validation logic with various scenarios."""
    print("Testing validation logic...")

    # Test 1: Valid payload
    print("\n1. Testing valid payload...")
    payload, checksum = generate_payload_with_checksum(1024)
    result = validate_response(payload, payload, checksum, 1)
    assert result == True, "Valid payload should pass validation"
    print("✅ Valid payload test passed")

    # Test 2: Corrupted payload
    print("\n2. Testing corrupted payload...")
    corrupted_payload = payload[:500] + b"corrupted" + payload[509:]
    result = validate_response(payload, corrupted_payload, checksum, 2)
    assert result == False, "Corrupted payload should fail validation"
    print("✅ Corrupted payload test passed")

    # Test 3: Wrong length payload
    print("\n3. Testing wrong length payload...")
    short_payload = payload[:500]
    result = validate_response(payload, short_payload, checksum, 3)
    assert result == False, "Wrong length payload should fail validation"
    print("✅ Wrong length test passed")

    # Test 4: Empty payload
    print("\n4. Testing empty payload...")
    result = validate_response(payload, b"", checksum, 4)
    assert result == False, "Empty payload should fail validation"
    print("✅ Empty payload test passed")

    # Test 5: Different payload with same length
    print("\n5. Testing different payload with same length...")
    different_payload = os.urandom(len(payload))
    result = validate_response(payload, different_payload, checksum, 5)
    assert result == False, "Different payload should fail validation"
    print("✅ Different payload test passed")

    print("\n🎉 All validation tests passed!")

def test_checksum_generation():
    """Test checksum generation consistency."""
    print("\nTesting checksum generation...")

    # Test that same payload generates same checksum
    payload1 = b"test payload"
    payload2 = b"test payload"

    checksum1 = hashlib.sha256(payload1).hexdigest()
    checksum2 = hashlib.sha256(payload2).hexdigest()

    assert checksum1 == checksum2, "Same payload should generate same checksum"
    print("✅ Checksum consistency test passed")

    # Test that different payloads generate different checksums
    payload3 = b"different payload"
    checksum3 = hashlib.sha256(payload3).hexdigest()

    assert checksum1 != checksum3, "Different payloads should generate different checksums"
    print("✅ Checksum uniqueness test passed")

if __name__ == "__main__":
    test_validation_logic()
    test_checksum_generation()
    print("\n✅ All tests completed successfully!")
