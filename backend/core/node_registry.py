"""
Node Registry — Self-registering node system with filesystem scan.

Nodes register themselves at import time via the @register_action decorator.
The registry can be reloaded at runtime via the /internal/registry/reload HTTP endpoint.
"""

import importlib
import logging
import os
import pkgutil
import pathlib
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class NodeMeta:
    type: str
    label: str
    icon: str
    category: str
    description: str
    config_schema: Any = field(default_factory=dict)
    example_output: Dict[str, Any] = field(default_factory=dict)
    is_llm_type: bool = False
    is_loop_type: bool = False
    is_ai_generated: bool = False

    def dict(self) -> Dict[str, Any]:
        # Generate default_config for frontend backward compatibility.
        # config_schema can be either a dict (legacy) or a list (new nodes).
        default_config = {}

        if isinstance(self.config_schema, list):
            # List-based schema: [{key, type, default, ...}, ...]
            for item in self.config_schema:
                if not isinstance(item, dict):
                    continue
                key = item.get("key")
                if not key:
                    continue
                if "default" in item:
                    default_config[key] = item["default"]
                elif item.get("type") == "string":
                    default_config[key] = ""
                elif item.get("type") == "number":
                    default_config[key] = 0
                elif item.get("type") == "boolean":
                    default_config[key] = False
                elif item.get("type") == "multiselect":
                    default_config[key] = []
        elif isinstance(self.config_schema, dict):
            # Dict-based schema: {field_key: {type, default, ...}, ...}
            for k, v in self.config_schema.items():
                if isinstance(v, dict) and "default" in v:
                    default_config[k] = v["default"]
                elif isinstance(v, dict) and v.get("type") == "string":
                    default_config[k] = ""
                elif isinstance(v, dict) and v.get("type") == "number":
                    default_config[k] = 0

        return {
            "type": self.type,
            "label": self.label,
            "icon": self.icon,
            "category": self.category,
            "description": self.description,
            "config_schema": self.config_schema,
            "example_output": self.example_output,
            "default_config": default_config,
            "is_llm_type": self.is_llm_type,
            "is_loop_type": self.is_loop_type,
            "is_ai_generated": self.is_ai_generated,
        }


class NodeRegistry:
    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._catalog: Dict[str, NodeMeta] = {}
        self._lock = threading.Lock()

    def get_handler(self, type: str) -> Optional[Callable]:
        return self._handlers.get(type)

    def get_catalog(self) -> List[NodeMeta]:
        return list(self._catalog.values())

    def list_types(self) -> List[str]:
        return list(self._handlers.keys())

    def get_llm_types(self) -> Set[str]:
        return {m.type for m in self._catalog.values() if m.is_llm_type}

    def get_loop_types(self) -> Set[str]:
        return {m.type for m in self._catalog.values() if m.is_loop_type}

    def get_meta(self, type: str) -> Optional[NodeMeta]:
        return self._catalog.get(type)

    def get_all_types(self) -> Set[str]:
        """Return all registered node type strings."""
        return set(self._handlers.keys())

    def get_config_schema(self, type: str) -> Optional[Dict[str, Any]]:
        """Return the config_schema for a given node type."""
        meta = self._catalog.get(type)
        return meta.config_schema if meta else None

    def register(self, meta: NodeMeta, handler: Callable):
        if meta.type in self._handlers:
            # Allow re-registration on SIGHUP reload
            logger.debug(f"Re-registering node type: {meta.type}")
        self._handlers[meta.type] = handler
        self._catalog[meta.type] = meta

    def clear(self):
        """Clear all registrations (used before re-scan)."""
        self._handlers.clear()
        self._catalog.clear()

    def _scan_dir(self, nodes_dir: str, is_ai_generated: bool):
        """Scan a single directory and import new modules only (never reload)."""
        if not os.path.isdir(nodes_dir):
            logger.warning(f"Nodes directory not found: {nodes_dir}")
            return

        for mod_info in pkgutil.iter_modules([nodes_dir]):
            prefix = "ai_nodes" if is_ai_generated else "nodes"
            module_name = f"{prefix}.{mod_info.name}"

            if module_name in importlib.sys.modules:
                # Never reload — leave live handlers untouched
                logger.debug(f"Skipping already-loaded module: {module_name}")
                continue
            try:
                importlib.import_module(module_name)
                logger.debug(f"Loaded node module: {module_name}")
            except Exception as e:
                logger.error(f"Failed to load {module_name}: {e}")

    def scan_and_load(self, nodes_dir: str | None = None):
        """
        Filesystem scan: import all Python modules in nodes/ and ai_nodes/.
        Each module's @register_action decorators fire on import.
        Thread-safe via lock. Never reloads already-loaded modules.
        """
        with self._lock:
            self._scan_dir(
                nodes_dir or str(pathlib.Path(__file__).parent.parent / "nodes"),
                is_ai_generated=False,
            )
            ai_dir = str(pathlib.Path(__file__).parent.parent / "ai_nodes")
            if os.path.isdir(ai_dir):
                self._scan_dir(ai_dir, is_ai_generated=True)

            logger.info(f"Registry scan complete: {len(self._handlers)} node types registered")


# ── Singleton ────────────────────────────────────────────────────────────────
registry = NodeRegistry()



def register_action(
    type: str,
    label: str,
    icon: str,
    category: str,
    description: str = "",
    config_schema: Any = None,
    example_output: Dict[str, Any] = None,
    is_llm_type: bool = False,
    is_loop_type: bool = False,
    is_ai_generated: bool = False,
):
    if config_schema is None:
        config_schema = {}

    def decorator(func: Callable):
        meta = NodeMeta(
            type=type,
            label=label,
            icon=icon,
            category=category,
            description=description,
            config_schema=config_schema,
            example_output=example_output or {},
            is_llm_type=is_llm_type,
            is_loop_type=is_loop_type,
            is_ai_generated=is_ai_generated,
        )
        registry.register(meta, func)
        return func
    return decorator
