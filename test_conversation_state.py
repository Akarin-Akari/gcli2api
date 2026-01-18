#!/usr/bin/env python3
"""
Test script for Conversation State functionality
测试 Conversation State 功能的脚本
"""

import json
import os
import sys
from datetime import datetime

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.cache.signature_database import SignatureDatabase
from src.cache.cache_interface import CacheConfig


def test_conversation_state():
    """Test conversation state CRUD operations"""
    print("=" * 60)
    print("Testing Conversation State Functionality")
    print("=" * 60)

    # Initialize database with test config
    config = CacheConfig(
        db_path="data/test_conversation_state.db",
        ttl_seconds=3600,
        wal_mode=True
    )
    db = SignatureDatabase(config)

    # Test data
    scid = "test_scid_12345"
    client_type = "cursor"
    history = json.dumps([
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"}
    ])
    signature = "test_signature_abc123"

    print("\n1. Testing store_conversation_state...")
    result = db.store_conversation_state(
        scid=scid,
        client_type=client_type,
        history=history,
        signature=signature
    )
    print(f"   [OK] Store result: {result}")
    assert result is True, "Failed to store conversation state"

    print("\n2. Testing get_conversation_state...")
    state = db.get_conversation_state(scid)
    print(f"   [OK] Retrieved state: {state}")
    assert state is not None, "Failed to retrieve conversation state"
    assert state["scid"] == scid
    assert state["client_type"] == client_type
    assert state["last_signature"] == signature
    assert len(state["authoritative_history"]) == 2

    print("\n3. Testing update_conversation_state...")
    updated_history = json.dumps([
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "How are you?"}
    ])
    updated_signature = "updated_signature_xyz789"
    result = db.update_conversation_state(
        scid=scid,
        history=updated_history,
        signature=updated_signature
    )
    print(f"   [OK] Update result: {result}")
    assert result is True, "Failed to update conversation state"

    # Verify update
    state = db.get_conversation_state(scid)
    assert state["last_signature"] == updated_signature
    assert len(state["authoritative_history"]) == 3
    print(f"   [OK] Verified update: signature={state['last_signature'][:20]}...")

    print("\n4. Testing access count increment...")
    initial_count = state["access_count"]
    state = db.get_conversation_state(scid)
    new_count = state["access_count"]
    print(f"   [OK] Access count: {initial_count} -> {new_count}")
    assert new_count > initial_count, "Access count not incremented"

    print("\n5. Testing delete_conversation_state...")
    result = db.delete_conversation_state(scid)
    print(f"   [OK] Delete result: {result}")
    assert result is True, "Failed to delete conversation state"

    # Verify deletion
    state = db.get_conversation_state(scid)
    assert state is None, "State still exists after deletion"
    print("   [OK] Verified deletion")

    print("\n6. Testing cleanup_expired_states...")
    # Store with short TTL
    db.store_conversation_state(
        scid="expired_scid",
        client_type="test",
        history=json.dumps([]),
        ttl_seconds=-1  # Already expired
    )
    count = db.cleanup_expired_states()
    print(f"   [OK] Cleaned up {count} expired states")

    print("\n7. Testing edge cases...")
    # Empty scid
    result = db.store_conversation_state("", "test", "{}")
    assert result is False, "Should reject empty scid"
    print("   [OK] Rejected empty scid")

    # Empty history
    result = db.store_conversation_state("test", "test", "")
    assert result is False, "Should reject empty history"
    print("   [OK] Rejected empty history")

    # Non-existent scid
    state = db.get_conversation_state("non_existent_scid")
    assert state is None, "Should return None for non-existent scid"
    print("   [OK] Returned None for non-existent scid")

    print("\n" + "=" * 60)
    print("[SUCCESS] All tests passed!")
    print("=" * 60)

    # Cleanup
    db.close()
    if os.path.exists(config.db_path):
        os.remove(config.db_path)
        print(f"\n[OK] Cleaned up test database: {config.db_path}")


if __name__ == "__main__":
    try:
        test_conversation_state()
    except Exception as e:
        print(f"\n[FAILED] Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
