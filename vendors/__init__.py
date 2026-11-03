"""Unified access to market/filings data vendors.

    from vendors import get_vendor
    deribit = get_vendor("deribit")
    deribit.collect("book_summary", currency="BTC")
"""

from .core import Dataset, Vendor, VendorStorage, available, dataset, get_vendor

__all__ = ["Dataset", "Vendor", "VendorStorage", "available", "dataset", "get_vendor"]
