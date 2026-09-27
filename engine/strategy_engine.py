"""05. The strategy engine: CRT, AMD, FVG, order block, liquidity sweep, trend pullback, EMA reversal.

Not one line of strategy logic is written here. Every one of those families is already implemented and
already backtested in `src/`, and each registers itself into `strategy_lab.ORDER_BUILDERS` on import:

    crt_displacement, crt_htf_lab, crt_mss_lab   -> CRT and its variants
    crt_fvg_lab                                   -> FVG inside the CRT/AMD model
    smart_entry_arch                              -> FVG, POC, CISD, displacement, volume stack
    poi_liquidity                                 -> points of interest and order-block style zones
    sweep_reversal                                -> liquidity sweep and the reclaim rule
    cisd_lab, candle_pattern_lab                  -> change-in-state-of-delivery, candle families

This module's whole job is to make that registry USABLE as an engine: load every family, list what is
available, build one family's declared variants, and produce the (side, stop, target) arrays for a bar
range. Rewriting any of them would mean re-earning the trust `tests/test_engine_truth.py` already gives
the shared simulator underneath.

A registry key is not always an engine family. `sweep_reclaim` is a separate GRID whose specs declare
`family: sweep_reversal`, because that is the builder that runs them — it is listed apart only so the
trial count charged against it belongs to that rule and not to the whole family's history. `builder_for`
resolves that, because looking a builder up by grid name alone silently skips it.
"""
from __future__ import annotations

import importlib
from typing import Callable, Dict, List, Optional

from src import strategy_lab as lab

# grid name -> (module, the function returning its declared variants)
FAMILIES: Dict[str, tuple] = {
    "candle_pattern": ("candle_pattern_lab", "pattern_variants"),
    "cisd": ("cisd_lab", "cisd_variants"),
    "crt_displacement": ("crt_displacement", "displacement_variants"),
    "crt_htf": ("crt_htf_lab", "htf_variants"),
    "crt_mss": ("crt_mss_lab", "mss_variants"),
    "poi_liquidity": ("poi_liquidity", "poi_variants"),
    "smart_entry_arch": ("smart_entry_arch", "arch_variants"),
    "sweep_reversal": ("sweep_reversal", "sweep_variants"),
    "sweep_reclaim": ("sweep_reversal", "reclaim_variants"),
}


def load_all() -> Dict[str, object]:
    """Import every lab so its builder registers, and return each grid's variant generator.

    A family that will not import is reported as the exception rather than dropped, so a broken module is
    visible instead of quietly reducing the strategy set.
    """
    found: Dict[str, object] = {}
    for grid, (module_name, function_name) in FAMILIES.items():
        try:
            module = importlib.import_module(f"src.{module_name}")
            if hasattr(module, "_register"):
                module._register()
            found[grid] = getattr(module, function_name)
        except Exception as exc:                       # noqa: BLE001 — a bad family must not hide
            found[grid] = exc
    return found


def available() -> List[str]:
    """Grids that loaded AND have a runnable builder. The honest list of what can actually be tested."""
    loaded = load_all()
    out = []
    for grid, generator in loaded.items():
        if not callable(generator):
            continue
        if builder_for(grid, generator) is not None:
            out.append(grid)
    return sorted(out)


def builder_for(grid: str, generator: Callable, symbol: str = "XAUUSD",
                timeframe: str = "4h") -> Optional[Callable]:
    """The order builder for a grid, falling back to the family its own specs declare.

    This fallback is the difference between `sweep_reclaim` being tested and being silently skipped.
    """
    direct = lab.ORDER_BUILDERS.get(grid)
    if direct is not None:
        return direct
    try:
        specs = generator(symbol, timeframe)
    except Exception:                                  # noqa: BLE001
        return None
    engine_family = str((specs[0].get("family") if specs else "") or "")
    return lab.ORDER_BUILDERS.get(engine_family)


def variants_for(grid: str, symbol: str, timeframe: str) -> List[dict]:
    """One grid's declared variants — the full, pre-stated list, before any result is seen.

    Named `variants_for`, not `variants`: `crt_fvg_lab.variants` already exists and builds that lab's own.
    """
    loaded = load_all()
    generator = loaded.get(grid)
    if not callable(generator):
        raise RuntimeError(f"grid {grid!r} did not load: {generator!r}")
    return generator(symbol, timeframe)


def orders(indicators, spec: dict):
    """(side, stop, target) for a spec, through the shared simulator's own resolution path."""
    return lab.strategy_orders(indicators, spec)


def catalogue(symbol: str = "XAUUSD", timeframe: str = "4h") -> Dict[str, dict]:
    """Every grid, whether it loaded, whether it has a builder, and how many variants it declares.

    Read this before a sweep: a grid showing `runnable: False` is one the sweep will report and skip, and
    knowing that in advance is the difference between "tested everything" and believing it did.
    """
    loaded = load_all()
    out: Dict[str, dict] = {}
    for grid, generator in loaded.items():
        if not callable(generator):
            out[grid] = {"loaded": False, "runnable": False, "variants": 0, "why": str(generator)[:120]}
            continue
        builder = builder_for(grid, generator, symbol, timeframe)
        try:
            count = len(generator(symbol, timeframe))
        except Exception as exc:                       # noqa: BLE001
            out[grid] = {"loaded": True, "runnable": False, "variants": 0, "why": f"variants failed: {exc}"[:120]}
            continue
        out[grid] = {"loaded": True, "runnable": builder is not None, "variants": count, "why": ""}
    return out
