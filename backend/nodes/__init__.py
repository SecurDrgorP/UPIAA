import importlib
import pkgutil
import pathlib

_nodes_path = pathlib.Path(__file__).parent
for _mod in pkgutil.iter_modules([str(_nodes_path)]):
    importlib.import_module(f"nodes.{_mod.name}")
