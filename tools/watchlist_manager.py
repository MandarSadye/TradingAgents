"""Watchlist manager — read, build, and write watchlist JSON files.

Usage:

    # Create a new watchlist and add tickers
    wl = Watchlist("my_watchlist.json")
    wl.trade_date = "2026-04-28"
    wl.max_workers = 5
    wl.add("nasdaq_2026-04-28_large-cap", "AAPL MSFT GOOGL")
    wl.add("nasdaq_2026-04-28_mega-cap", ["NVDA", "AMZN"])
    wl.save()

    # Load an existing watchlist
    wl = Watchlist.load("watchlist.json")
    print(wl.trade_date)
    print(wl.tickers())                          # all tickers, de-duped
    print(wl.tickers("nasdaq_2026-04-28_large-cap"))  # one category
    for cat, syms in wl.categories.items():
        ...
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path


class Watchlist:
    """Interface for creating, reading, and writing watchlist JSON files."""

    def __init__(self, path: str | Path | None = None, *,
                 trade_date: str | None = None,
                 max_workers: int = 3):
        self.path = Path(path) if path else None
        self.trade_date = trade_date or date.today().isoformat()
        self.max_workers = max_workers
        self._categories: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Factory: load from an existing JSON file
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> Watchlist:
        """Load a watchlist from a JSON file."""
        p = Path(path)
        data = json.loads(p.read_text())
        wl = cls(
            path=p,
            trade_date=data.get("trade_date", date.today().isoformat()),
            max_workers=data.get("max_workers", 3),
        )
        for cat, syms in data.get("categories", {}).items():
            wl._categories[cat] = set(syms.split()) if isinstance(syms, str) else set(syms)
        return wl

    # ------------------------------------------------------------------
    # Add / remove tickers
    # ------------------------------------------------------------------

    def add(self, category: str, symbols: str | list[str]) -> Watchlist:
        """Add symbols to a category. Accepts a space-separated string or a list."""
        if isinstance(symbols, str):
            symbols = symbols.split()
        self._categories.setdefault(category, set()).update(symbols)
        return self

    def remove(self, category: str, symbols: str | list[str]) -> Watchlist:
        """Remove symbols from a category."""
        if isinstance(symbols, str):
            symbols = symbols.split()
        bucket = self._categories.get(category)
        if bucket:
            bucket -= set(symbols)
            if not bucket:
                del self._categories[category]
        return self

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    @property
    def categories(self) -> dict[str, str]:
        """Return categories as {name: 'SYM1 SYM2 ...'} (sorted)."""
        return {cat: " ".join(sorted(syms))
                for cat, syms in sorted(self._categories.items()) if syms}

    def tickers(self, category: str | None = None, filter: str | None = None) -> list[str]:
        """Return de-duped ticker list.

        Args:
            category: exact category name (returns only that category).
            filter:   regex or substring matched against category names.
                      All matching categories are included.
        """
        if category:
            return sorted(self._categories.get(category, set()))
        cats = self._categories
        if filter:
            try:
                pat = re.compile(filter, re.IGNORECASE)
            except re.error:
                pat = re.compile(re.escape(filter), re.IGNORECASE)
            cats = {k: v for k, v in cats.items() if pat.search(k)}
        seen: set[str] = set()
        out: list[str] = []
        for syms in cats.values():
            for s in sorted(syms):
                if s not in seen:
                    seen.add(s)
                    out.append(s)
        return out

    def filter_categories(self, pattern: str) -> dict[str, str]:
        """Return categories whose names match a regex or substring."""
        try:
            pat = re.compile(pattern, re.IGNORECASE)
        except re.error:
            pat = re.compile(re.escape(pattern), re.IGNORECASE)
        return {cat: " ".join(sorted(syms))
                for cat, syms in sorted(self._categories.items())
                if pat.search(cat) and syms}

    def category_names(self) -> list[str]:
        """Return sorted list of category names."""
        return sorted(self._categories.keys())

    def __len__(self) -> int:
        """Total unique tickers across all categories."""
        return len({s for syms in self._categories.values() for s in syms})

    def __contains__(self, symbol: str) -> bool:
        return any(symbol in syms for syms in self._categories.values())

    # ------------------------------------------------------------------
    # Serialise
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return the watchlist as a plain dict (JSON-ready)."""
        return {
            "trade_date": self.trade_date,
            "max_workers": self.max_workers,
            "categories": self.categories,
        }

    def save(self, path: str | Path | None = None) -> Path:
        """Write to JSON. Uses self.path if no override given."""
        p = Path(path) if path else self.path
        if p is None:
            raise ValueError("No path specified — pass one to save() or set self.path")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=4))
        return p

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def print_summary(self) -> None:
        """Pretty-print the watchlist to stdout."""
        print(f"\n{'=' * 60}")
        print(f"  Watchlist — {self.trade_date}  ({len(self)} tickers)")
        print(f"{'=' * 60}")
        for cat, syms_str in self.categories.items():
            words = syms_str.split()
            print(f"\n  [{cat.upper()}]  ({len(words)} tickers)")
            line = "    "
            for w in words:
                if len(line) + len(w) + 1 > 80:
                    print(line)
                    line = "    "
                line += w + " "
            if line.strip():
                print(line)
        print()

    def __repr__(self) -> str:
        return (f"Watchlist(path={self.path!r}, trade_date={self.trade_date!r}, "
                f"categories={len(self._categories)}, tickers={len(self)})")
