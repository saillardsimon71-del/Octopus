"""Autonomous Business Engine core runtime.

This package contains the reusable infrastructure shared across all business
instances. Business-specific logic lives under the businesses/ tree.
"""

from .config import AppSettings

__all__ = ["AppSettings"]
