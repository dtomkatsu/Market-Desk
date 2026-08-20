"""Load and validate ``config/watchlist.yml``."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "watchlist.yml"


@dataclass(frozen=True)
class Tier:
    """One named group of symbols."""
    key: str
    label: str
    note: str
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    history_period: str = "5y"
    rvol_window: int = 20
    min_sector_peers: int = 4


@dataclass(frozen=True)
class Universe:
    tiers: tuple[Tier, ...]
    settings: Settings = field(default_factory=Settings)

    @property
    def symbols(self) -> tuple[str, ...]:
        """Every symbol, de-duplicated, in tier then declaration order."""
        seen: list[str] = []
        for tier in self.tiers:
            for sym in tier.symbols:
                if sym not in seen:
                    seen.append(sym)
        return tuple(seen)

    def tier_of(self, symbol: str) -> Optional[str]:
        for tier in self.tiers:
            if symbol in tier.symbols:
                return tier.key
        return None


def _normalize(symbol: str) -> str:
    """Canonical symbol form: upper case, no surrounding space.

    Yahoo uses ``-`` for share classes (``BRK-B``) where some exports use
    ``.`` (``BRK.B``); normalize to Yahoo's form since that is what the
    fetcher speaks.
    """
    return symbol.strip().upper().replace(".", "-")


def load_universe(path: Optional[Path] = None) -> Universe:
    """Parse the watchlist config.

    Raises ``ValueError`` rather than silently yielding an empty universe —
    a refresh that quietly tracks nothing is the failure mode most likely
    to go unnoticed for weeks.
    """
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.exists():
        raise FileNotFoundError(f"watchlist config not found: {path}")

    raw = yaml.safe_load(path.read_text()) or {}
    raw_tiers = raw.get("tiers") or {}
    if not raw_tiers:
        raise ValueError(f"{path} declares no tiers")

    claimed: set[str] = set()
    tiers: list[Tier] = []
    for key, body in raw_tiers.items():
        body = body or {}
        symbols: list[str] = []
        for sym in body.get("symbols") or []:
            norm = _normalize(str(sym))
            if not norm or norm in claimed:
                continue  # first tier to claim a symbol keeps it
            claimed.add(norm)
            symbols.append(norm)
        tiers.append(
            Tier(
                key=str(key),
                label=str(body.get("label") or key),
                note=str(body.get("note") or "").strip(),
                symbols=tuple(symbols),
            )
        )

    if not claimed:
        raise ValueError(f"{path} declares tiers but no symbols")

    s = raw.get("settings") or {}
    settings = Settings(
        history_period=str(s.get("history_period", "5y")),
        rvol_window=int(s.get("rvol_window", 20)),
        min_sector_peers=int(s.get("min_sector_peers", 4)),
    )
    return Universe(tiers=tuple(tiers), settings=settings)
