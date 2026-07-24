#!/usr/bin/env python3
"""Generate SHA-256 hash for access_password config.

Usage:
    python generate_access_password.py

Then copy the output hash to etc/conf/server.conf:
    access_password=<hash>
"""

import hashlib


def main():
    print("Access Password Generator")
    print("=" * 40)
    password = input("Enter password: ").strip()
    if not password:
        print("Error: password cannot be empty")
        return
    hash_value = hashlib.sha256(password.encode()).hexdigest()
    print()
    print(f"SHA-256 hash: {hash_value}")
    print()
    print("Add this to etc/conf/server.conf:")
    print(f"  access_password={hash_value}")


if __name__ == "__main__":
    main()
