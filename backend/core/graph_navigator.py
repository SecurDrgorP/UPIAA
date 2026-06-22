"""
Graph Navigator — DAG traversal with topological sort, branch resolution,
loop handling, and max 100 iterations guard.
"""

from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Set, Optional, Union

from core.schemas import NodeData, EdgeData

MAX_ITERATIONS = 100


def topological_sort(nodes: List[NodeData], edges: List[EdgeData]) -> List[NodeData]:
    """Pure DAG walker — topological sort with zero side effects."""
    in_degree = {n.id: 0 for n in nodes}
    adj = {n.id: [] for n in nodes}
    for e in edges:
        if e.source in in_degree and e.target in in_degree:
            adj[e.source].append(e.target)
            in_degree[e.target] += 1

    queue: deque[str] = deque(n.id for n in nodes if in_degree[n.id] == 0)
    sorted_nodes = []

    node_map = {n.id: n for n in nodes}

    while queue:
        n_id = queue.popleft()
        sorted_nodes.append(node_map[n_id])
        for neighbor in adj[n_id]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(sorted_nodes) < len(nodes):
        missing = [n for n in nodes if n.id not in {sn.id for sn in sorted_nodes}]
        sorted_nodes.extend(missing)

    return sorted_nodes


@dataclass
class ExecuteNode:
    node: NodeData


@dataclass
class SkipNode:
    node_id: str
    label: str


@dataclass
class RunForLoop:
    loop_node: NodeData
    iterable: list
    item_variable: str
    body_nodes: List[NodeData]


@dataclass
class RunWhileLoop:
    loop_node: NodeData
    body_nodes: List[NodeData]
    max_iterations: int


Action = Union[ExecuteNode, SkipNode, RunForLoop, RunWhileLoop]


class GraphNavigator:
    def __init__(self, nodes: List[NodeData], edges: List[EdgeData]):
        # Exclude visual/organizational nodes
        self.nodes = [n for n in nodes if n.type not in ("group", "comment")]
        valid_ids = {n.id for n in self.nodes}
        self.edges = [e for e in edges if e.source in valid_ids and e.target in valid_ids]

        self.node_map = {n.id: n for n in self.nodes}
        self.exec_order = topological_sort(self.nodes, self.edges)
        self.context: Dict[str, Any] = {}
        self.skipped: Set[str] = set()
        self._cursor = 0
        self._iteration_count = 0

    def next(self) -> Optional[Action]:
        if self._cursor >= len(self.exec_order):
            return None

        # Max iterations guard — prevents infinite loops in malformed graphs
        self._iteration_count += 1
        if self._iteration_count > MAX_ITERATIONS:
            return None

        node = self.exec_order[self._cursor]
        self._cursor += 1

        if node.id in self.skipped:
            return SkipNode(node_id=node.id, label=node.label)

        return ExecuteNode(node=node)

    def record_result(self, node_id: str, result: dict):
        self.context[node_id] = result
        node = self.node_map.get(node_id)
        if node and node.type == "if_else":
            self._resolve_branch(node_id, result)

    def get_for_loop_action(self, node_id: str, result: dict) -> Optional[RunForLoop]:
        if result.get("error"):
            return None
        iterable = result.get("iterable", [])
        if not iterable:
            return None

        item_variable = result.get("item_variable", "item")
        loop_node = self.node_map[node_id]

        body_nodes = self._get_downstream_nodes(node_id)

        for n in body_nodes:
            self.skipped.add(n.id)

        return RunForLoop(
            loop_node=loop_node,
            iterable=iterable,
            item_variable=item_variable,
            body_nodes=body_nodes,
        )

    def get_while_loop_action(self, node_id: str, result: dict) -> Optional[RunWhileLoop]:
        if result.get("error"):
            return None

        should_continue = result.get("continue", False)
        if not should_continue:
            return None

        max_iterations = min(result.get("max_iterations", 100), MAX_ITERATIONS)
        loop_node = self.node_map[node_id]

        body_nodes = self._get_downstream_nodes(node_id)

        for n in body_nodes:
            self.skipped.add(n.id)

        return RunWhileLoop(
            loop_node=loop_node,
            body_nodes=body_nodes,
            max_iterations=max_iterations,
        )

    def _get_downstream_nodes(self, source_id: str) -> List[NodeData]:
        reachable = set()
        queue: deque[str] = deque([source_id])
        while queue:
            curr = queue.popleft()
            for e in self.edges:
                if e.source == curr and e.target not in reachable:
                    reachable.add(e.target)
                    queue.append(e.target)

        return [n for n in self.exec_order if n.id in reachable]

    def _resolve_branch(self, if_node_id: str, result: dict):
        branch_taken = result.get("branch_taken")
        if not branch_taken:
            return

        taken_targets = set()
        skipped_targets = set()

        for e in self.edges:
            if e.source == if_node_id:
                if e.source_handle == branch_taken:
                    taken_targets.add(e.target)
                else:
                    skipped_targets.add(e.target)

        skipped_targets -= taken_targets

        for target in skipped_targets:
            self.skipped.add(target)
            self._cascade_skip(target)

    def _cascade_skip(self, node_id: str):
        for e in self.edges:
            if e.source == node_id:
                target = e.target
                all_skipped = True
                for in_edge in self.edges:
                    if in_edge.target == target and in_edge.source not in self.skipped:
                        all_skipped = False
                        break

                if all_skipped:
                    self.skipped.add(target)
                    self._cascade_skip(target)
