#!/usr/bin/env python3
"""Redis Memory Check — connect to local Redis and report findings."""

import asyncio
import json
import time

try:
    import redis.asyncio as aioredis
except ImportError:
    print("ERROR: redis package not installed. Install with: pip install redis[hiredis]")
    raise SystemExit(1)


async def main():
    print("=" * 70)
    print("REDIS MEMORY CHECK REPORT")
    print("=" * 70)

    # Connect to local Redis (default host/port)
    r = aioredis.from_url(
        "redis://localhost:6379/0",
        decode_responses=True,
        socket_timeout=5.0,
    )

    # 1. PING
    print("\n[1] PING — Connection Test")
    print("-" * 40)
    try:
        pong = await r.ping()
        print(f"  PING -> {pong}")
    except Exception as e:
        print(f"  ERROR: Could not connect to Redis: {e}")
        await r.aclose()
        return

    # 2. INFO server
    print("\n[2] Redis INFO (basic)")
    print("-" * 40)
    try:
        info = await r.info()
        print(f"  redis_version:       {info.get('redis_version', 'N/A')}")
        print(f"  uptime_in_seconds:   {info.get('uptime_in_seconds', 'N/A')}")
        print(f"  connected_clients:   {info.get('connected_clients', 'N/A')}")
        print(f"  used_memory_human:   {info.get('used_memory_human', 'N/A')}")
        print(f"  total_keys:          {info.get('db0', {}).get('keys', 'N/A')}")
        print(f"  os:                  {info.get('os', 'N/A')}")
        print(f"  arch_bits:           {info.get('arch_bits', 'N/A')}")
        print(f"  process_id:          {info.get('process_id', 'N/A')}")
        print(f"  tcp_port:            {info.get('tcp_port', 'N/A')}")
    except Exception as e:
        print(f"  ERROR fetching INFO: {e}")

    # 3. DBSIZE
    print("\n[3] DBSIZE — Total Keys in DB 0")
    print("-" * 40)
    try:
        dbsize = await r.dbsize()
        print(f"  Total keys: {dbsize}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 4. SCAN all keys
    print("\n[4] SCAN — All Keys")
    print("-" * 40)
    all_keys = []
    cursor = 0
    try:
        while True:
            cursor, batch = await r.scan(cursor, count=500)
            all_keys.extend(batch)
            if cursor == 0:
                break
        print(f"  Found {len(all_keys)} key(s) total:")
        if all_keys:
            for k in sorted(all_keys):
                # Get TTL and type for each key
                try:
                    ktype = await r.type(k)
                    ttl = await r.ttl(k)
                    size = await r.memory_usage(k) or 0
                    print(f"    KEY: {k}")
                    print(f"      type: {ktype}, TTL: {ttl}s, memory: {size} bytes")
                except Exception:
                    print(f"    KEY: {k}  (metadata unavailable)")
        else:
            print("    (no keys found)")
    except Exception as e:
        print(f"  ERROR scanning keys: {e}")

    # 5. Search for timestamp/time-related keys
    print("\n[5] TIMESTAMP / TIME-RELATED KEYS")
    print("-" * 40)
    time_patterns = ["*timestamp*", "*time*", "*date*", "*clock*", "*epoch*", "*ts*"]
    time_keys = set()
    for pattern in time_patterns:
        cursor = 0
        try:
            while True:
                cursor, batch = await r.scan(cursor, match=pattern, count=500)
                time_keys.update(batch)
                if cursor == 0:
                    break
        except Exception:
            pass

    if time_keys:
        print(f"  Found {len(time_keys)} time-related key(s):")
        for k in sorted(time_keys):
            try:
                val = await r.get(k)
                ttl = await r.ttl(k)
                print(f"    KEY:   {k}")
                print(f"    VALUE: {val}")
                print(f"    TTL:   {ttl}s")
                # Try to parse as JSON if it looks like one
                if val and val.startswith("{"):
                    try:
                        parsed = json.loads(val)
                        print(f"    JSON:  {json.dumps(parsed, indent=6)}")
                    except json.JSONDecodeError:
                        pass
                print()
            except Exception as e:
                print(f"    KEY: {k}  (error: {e})\n")
    else:
        print("  No time-related keys found.")

    # 6. Check for goat2:working:* keys specifically
    print("\n[6] GOAT2 WORKING MEMORY KEYS (goat2:working:*)")
    print("-" * 40)
    goat_keys = []
    cursor = 0
    try:
        while True:
            cursor, batch = await r.scan(cursor, match="goat2:working:*", count=500)
            goat_keys.extend(batch)
            if cursor == 0:
                break
        if goat_keys:
            print(f"  Found {len(goat_keys)} goat2:working key(s):")
            for k in sorted(goat_keys):
                try:
                    val = await r.get(k)
                    ttl = await r.ttl(k)
                    print(f"    KEY:   {k}")
                    print(f"    TTL:   {ttl}s")
                    if val:
                        # Truncate long values for display
                        display = val[:300] + "..." if len(val) > 300 else val
                        print(f"    VALUE: {display}")
                    print()
                except Exception as e:
                    print(f"    KEY: {k}  (error: {e})\n")
        else:
            print("  No goat2:working keys found.")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 7. Check for any keys with "timestamp" in their value (scan values)
    print("\n[7] KEYS CONTAINING 'timestamp' IN THEIR VALUES")
    print("-" * 40)
    ts_val_keys = []
    for k in all_keys:
        try:
            val = await r.get(k)
            if val and ("timestamp" in val.lower() or "time" in val.lower()):
                ts_val_keys.append(k)
        except Exception:
            pass
    if ts_val_keys:
        print(f"  Found {len(ts_val_keys)} key(s) with timestamp/time in value:")
        for k in sorted(ts_val_keys):
            val = await r.get(k)
            display = val[:200] + "..." if val and len(val) > 200 else val
            print(f"    {k} -> {display}")
    else:
        print("  No keys with 'timestamp' in their values found.")

    # 8. Memory stats
    print("\n[8] MEMORY STATS")
    print("-" * 40)
    try:
        mem_stats = await r.memory_stats()
        print(f"  peak.allocated:       {mem_stats.get('peak.allocated', 'N/A')}")
        print(f"  total.allocated:      {mem_stats.get('total.allocated', 'N/A')}")
        print(f"  startup.allocated:    {mem_stats.get('startup.allocated', 'N/A')}")
        print(f"  replication.backlog:  {mem_stats.get('replication.backlog', 'N/A')}")
        print(f"  keys.count:           {mem_stats.get('keys.count', 'N/A')}")
        print(f"  dataset.bytes:        {mem_stats.get('dataset.bytes', 'N/A')}")
        print(f"  dataset.percentage:   {mem_stats.get('dataset.percentage', 'N/A')}")
        print(f"  peak.percentage:      {mem_stats.get('peak.percentage', 'N/A')}")
        print(f"  fragmentation:        {mem_stats.get('fragmentation', 'N/A')}")
    except Exception as e:
        print(f"  MEMORY STATS not available (older Redis?): {e}")
        # Fallback: INFO memory
        try:
            info = await r.info("memory")
            for k, v in info.items():
                print(f"  {k}: {v}")
        except Exception as e2:
            print(f"  Fallback also failed: {e2}")

    # 9. Check for expires / TTL stats
    print("\n[9] KEYS WITH EXPIRY (TTL > 0)")
    print("-" * 40)
    expiring = []
    for k in all_keys:
        try:
            ttl = await r.ttl(k)
            if ttl > 0:
                expiring.append((k, ttl))
        except Exception:
            pass
    if expiring:
        print(f"  Found {len(expiring)} key(s) with expiry:")
        for k, ttl in sorted(expiring, key=lambda x: x[1]):
            print(f"    {k}  TTL: {ttl}s ({ttl/60:.1f} min)")
    else:
        print("  No keys with expiry (all persistent or already expired).")

    # 10. Keys without expiry
    print("\n[10] KEYS WITHOUT EXPIRY (persistent)")
    print("-" * 40)
    persistent = []
    for k in all_keys:
        try:
            ttl = await r.ttl(k)
            if ttl == -1:
                persistent.append(k)
        except Exception:
            pass
    if persistent:
        print(f"  Found {len(persistent)} persistent key(s):")
        for k in sorted(persistent):
            print(f"    {k}")
    else:
        print("  No persistent keys (all have expiry or none exist).")

    # Cleanup
    await r.aclose()

    print("\n" + "=" * 70)
    print("END OF REDIS MEMORY CHECK REPORT")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
