"""Extractors turn a PDF into InvoiceFields. Each one is registered by name so the
evaluation can compare them on the same benchmark."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Protocol

from vouch.schemas import Extraction


class Extractor(Protocol):
    name: str

    def extract(self, doc_id: str, pdf_path: Path) -> Extraction: ...


REGISTRY: dict[str, Callable[..., Extractor]] = {}


def register(name: str):
    def deco(factory):
        REGISTRY[name] = factory
        return factory
    return deco


def get(name: str, **kwargs) -> Extractor:
    # import side-effect registrations
    from vouch.extract import llm, local, rules  # noqa: F401
    if name not in REGISTRY:
        raise KeyError(f"Unknown extractor '{name}'. Available: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[name](**kwargs)


def run(extractor: Extractor, docs_dir: Path, cache: Path | None = None,
        progress: bool = True) -> dict[str, Extraction]:
    """Extract every PDF in docs_dir. Results are cached as JSONL so re-running the
    evaluation never pays for the same API call twice."""
    done: dict[str, Extraction] = {}
    if cache and cache.exists():
        for line in cache.read_text().splitlines():
            e = Extraction.model_validate_json(line)
            done[e.doc_id] = e
    pdfs = sorted(docs_dir.glob("*.pdf"))
    todo = [p for p in pdfs if p.stem not in done]
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
    with open(cache, "a") if cache else _Null() as out:
        for i, p in enumerate(todo, 1):
            t0 = time.perf_counter()
            try:
                e = extractor.extract(p.stem, p)
            except Exception as err:  # noqa: BLE001 — record and continue; one bad doc shouldn't stop a run
                from vouch.schemas import InvoiceFields
                e = Extraction(doc_id=p.stem, fields=InvoiceFields(), confidence=0.0, error=str(err))
            e.seconds = e.seconds or time.perf_counter() - t0
            done[p.stem] = e
            out.write(e.model_dump_json() + "\n")
            if progress and (i % 25 == 0 or i == len(todo)):
                print(f"  {extractor.name}: {i}/{len(todo)}", flush=True)
    return {p.stem: done[p.stem] for p in pdfs if p.stem in done}


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def write(self, _):
        pass


def dump(results: dict[str, Extraction]) -> str:
    return json.dumps({k: v.model_dump(mode="json") for k, v in results.items()}, indent=1)
