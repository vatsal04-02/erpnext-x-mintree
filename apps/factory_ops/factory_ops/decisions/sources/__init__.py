# Copyright (c) 2026, FlowHQ and contributors
# License: GNU General Public License v3. See license.txt
"""The source registry. Each department module registers its sources with ``@source``.

A source is ``fetch(ctx) -> list[Entry]``. It declares the DocTypes the viewer must be able to read;
when they cannot, the source is skipped (the viewer simply has no such section), not failed."""

from dataclasses import dataclass
from typing import Callable

REGISTRY: list["Source"] = []
KIND_LABELS: dict[str, str] = {}


@dataclass
class Source:
	name: str
	department: str
	requires: tuple
	fetch: Callable
	kinds: dict


def source(name: str, department: str, requires: tuple, kinds: dict):
	def register(fn):
		REGISTRY.append(Source(name, department, tuple(requires), fn, dict(kinds)))
		KIND_LABELS.update(kinds)
		return fn

	return register


def all_kinds() -> dict[str, str]:
	_load()
	return dict(KIND_LABELS)


def _load():
	from factory_ops.decisions.sources import (  # noqa: F401
		accounts,
		dispatch,
		inventory,
		maintenance,
		procurement,
		production,
		quality,
	)
