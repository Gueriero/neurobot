#!/usr/bin/env python3
"""
Database initialization script.
Run: python -m src.init_db
"""

from db import init_db

if __name__ == '__main__':
    print("Initializing database...")
    init_db()
    print("Database tables created successfully.")