"""Lossless-in-meaning JSON document history, independent of Qt."""

import copy
import json
import os
from pathlib import Path


def clean_optional_collections(value):
    """Omit empty optional sections, retaining named empty memberships."""
    value = copy.deepcopy(value)
    if not isinstance(value, dict):
        return value
    for key in ("groups", "arrangements", "markers", "events"):
        collection = value.get(key)
        if isinstance(collection, (dict, list)) and not collection:
            del value[key]
            if key == "arrangements":
                value.pop("initial_arrangement", None)
    return value


def clean_document(value):
    value = copy.deepcopy(value)
    for category in ("songs", "sfx"):
        if isinstance(value.get(category), list):
            value[category] = [
                clean_optional_collections(item) for item in value[category]
            ]
    return value


class Document:
    def __init__(self, data=None, path=None):
        original = copy.deepcopy(
            data if data is not None else {"version": 1, "songs": [], "sfx": []}
        )
        self.data = clean_document(original)
        self.path = Path(path).resolve() if path else None
        self.base_dir = self.path.parent if self.path else Path.cwd()
        self.saved = original
        self.history = [copy.deepcopy(self.data)]
        self.cursor = self.revision = 0

    @property
    def dirty(self):
        return json.dumps(self.data, ensure_ascii=False) != json.dumps(
            self.saved, ensure_ascii=False
        )

    def replace(self, value):
        value = clean_document(value)
        if json.dumps(value, ensure_ascii=False) == json.dumps(
            self.data, ensure_ascii=False
        ):
            return
        self.data = copy.deepcopy(value)
        self.history[self.cursor + 1 :] = [copy.deepcopy(value)]
        self.cursor += 1
        self.revision += 1

    def travel(self, delta):
        cursor = self.cursor + delta
        if 0 <= cursor < len(self.history):
            self.cursor = cursor
            self.data = copy.deepcopy(self.history[cursor])
            self.revision += 1

    def save(self, path=None):
        from clpconvert import atomic

        target = Path(path or self.path).resolve()

        def rebased(value):
            data = copy.deepcopy(value)
            for cat in ("songs", "sfx"):
                for item in data.get(cat, []):
                    if isinstance(item.get("source"), str):
                        source = (self.base_dir / item["source"]).resolve()
                        try:
                            item["source"] = Path(
                                os.path.relpath(source, target.parent)
                            ).as_posix()
                        except ValueError:
                            item["source"] = source.as_posix()
                        if target == source:
                            raise ValueError(
                                "Manifest would overwrite a source resource"
                            )
            return data

        data = rebased(self.data)
        atomic(
            target,
            (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        self.history = [rebased(value) for value in self.history]
        self.data = data
        self.revision += 1
        self.path, self.base_dir = target, target.parent
        self.saved = copy.deepcopy(data)
