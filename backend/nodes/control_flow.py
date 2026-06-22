"""
Control Flow Evaluators — If/Else, For Loop, While Loop.
Provides safe condition evaluation against execution context.
"""

import logging
from typing import Any
from core.node_registry import register_action

logger = logging.getLogger(__name__)

import re

import ast
import operator

SAFE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
    ast.In: lambda a, b: a in b if b is not None else False,
    ast.NotIn: lambda a, b: a not in b if b is not None else False,
    ast.And: lambda a, b: a and b,
    ast.Or: lambda a, b: a or b,
    ast.Not: operator.not_,
}

def _eval_ast(node, context, input_data, _depth: int = 0):
    if _depth > 50:
        raise ValueError(
            "Condition expression is too deeply nested (max depth 50). "
            "Simplify your condition."
        )
    if isinstance(node, ast.Expression):
        return _eval_ast(node.body, context, input_data, _depth + 1)
    elif isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.Name):
        if node.id == "context":
            return context
        elif node.id == "input_data":
            return input_data
        elif node.id == "True":
            return True
        elif node.id == "False":
            return False
        elif node.id == "None":
            return None
        raise ValueError(f"Unsupported name: {node.id}")
    elif isinstance(node, ast.Subscript):
        col = _eval_ast(node.value, context, input_data, _depth + 1)
        key = _eval_ast(node.slice, context, input_data, _depth + 1)
        if isinstance(col, dict):
            return col.get(key)
        elif isinstance(col, (list, tuple)) and isinstance(key, int):
            try:
                return col[key]
            except IndexError:
                return None
        return None
    elif isinstance(node, ast.Compare):
        left = _eval_ast(node.left, context, input_data, _depth + 1)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval_ast(comp, context, input_data, _depth + 1)
            if type(op) not in SAFE_OPS:
                raise ValueError(f"Unsupported comparison operator: {type(op)}")
            if not SAFE_OPS[type(op)](left, right):
                return False
            left = right
        return True
    elif isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for value in node.values:
                if not _eval_ast(value, context, input_data, _depth + 1):
                    return False
            return True
        elif isinstance(node.op, ast.Or):
            for value in node.values:
                if _eval_ast(value, context, input_data, _depth + 1):
                    return True
            return False
        raise ValueError(f"Unsupported bool operator: {type(node.op)}")
    elif isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _eval_ast(node.operand, context, input_data, _depth + 1)
        raise ValueError(f"Unsupported unary operator: {type(node.op)}")
    else:
        raise ValueError(f"Unsupported AST node type: {type(node)}")

def _sanitize_condition(raw: str) -> str:
    """Strip common LLM-generated artifacts from condition strings.

    LLM nodes frequently produce conditions with trailing periods,
    surrounding quotes, markdown backticks, or mixed-case boolean
    literals.  Cleaning these before AST parsing prevents spurious
    SyntaxError failures.
    """
    s = raw.strip()
    # Strip surrounding quotes: '"True"' → 'True'
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'", '`'):
        s = s[1:-1].strip()
    # Strip markdown inline-code backticks: '`True`' → 'True'
    s = s.strip('`').strip()
    # Strip trailing period/comma/semicolon (natural-language punctuation)
    s = s.rstrip('.,;').strip()
    return s


# Literal boolean strings that can be resolved without AST parsing.
_BOOL_LITERALS = {
    "true": True, "false": False,
    "yes": True, "no": False,
    "1": True, "0": False,
}


def eval_condition(condition: str, context: dict[str, Any], input_data: dict[str, Any]) -> bool:
    """
    Safely evaluate a Python-like condition string against the execution context.
    Example: "context['n1']['prediction'] == 'churn'"
    """
    if not condition or not condition.strip():
        logger.warning("Empty condition — defaulting to False")
        return False

    condition = _sanitize_condition(condition)

    # Fast-path for literal boolean strings (case-insensitive)
    lower = condition.lower()
    if lower in _BOOL_LITERALS:
        return _BOOL_LITERALS[lower]

    try:
        parsed = ast.parse(condition, mode='eval')
        result = _eval_ast(parsed, context, input_data)
        return bool(result)
    except Exception as e:
        logger.error(f"Condition evaluation failed: '{condition}' — {e}")
        return False


async def execute_if_else(
    node_config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate If/Else condition and return which branch to take.
    Returns: {"condition_result": bool, "branch_taken": "true"|"false", "next_node": str}
    """
    condition = node_config.get("condition", "")
    if not condition:
        return {"error": "missing field: condition", "__input_error__": True}
        
    true_branch = node_config.get("true_branch", "")
    false_branch = node_config.get("false_branch", "")

    result = eval_condition(condition, context, input_data)
    next_node = true_branch if result else false_branch

    return {
        "condition_result": result,
        "branch_taken": "true" if result else "false"
    }


async def execute_for_loop(
    node_config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    from core.expressions import resolve_dotpath

    iterable_key = node_config.get("iterable_key", "")
    if not iterable_key:
        return {"error": "missing field: iterable_key", "__input_error__": True}
        
    item_variable = node_config.get("item_variable", "item")

    # Use dotpath resolution
    iterable = resolve_dotpath(iterable_key, context)
    if iterable is None:
        iterable = resolve_dotpath(iterable_key, input_data)
        
    if not isinstance(iterable, (list, tuple)):
        # try string split
        if isinstance(iterable_key, str) and "," in iterable_key:
            iterable = [item.strip() for item in iterable_key.split(",") if item.strip()]
        else:
            return {"error": f"Iterable '{iterable_key}' could not be resolved to a list.", "__input_error__": True}

    iterable = list(iterable)
    return {
        "iterable": iterable,
        "item_variable": item_variable,
        "total": len(iterable)
    }


async def execute_while_loop(
    node_config: dict[str, Any],
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate a While Loop condition. Returns whether to continue.
    """
    condition = node_config.get("condition", "")
    if not condition:
        return {"error": "missing field: condition", "__input_error__": True}
        
    max_iterations = node_config.get("max_iterations", 50)

    should_continue = eval_condition(condition, context, input_data)
    return {
        "continue": should_continue,
        "max_iterations": max_iterations
    }

from core.expressions import apply_output_mapping

@register_action(
    type="if_else",
    label="If / Else",
    icon="🔀",
    description="Conditional branching based on a condition",
    category="Logic",
    config_schema={
        "condition": {
            "type": "string",
            "label": "Condition",
            "required": True,
            "multiline": True,
            "placeholder": "context['n1']['prediction'] == 'churn'",
            "description": "Python-like expression evaluated against the execution context.",
        },
        "true_branch": {
            "type": "string",
            "label": "True Branch Node ID",
            "description": "Auto-set from canvas connections. The node to execute when condition is True.",
        },
        "false_branch": {
            "type": "string",
            "label": "False Branch Node ID",
            "description": "Auto-set from canvas connections. The node to execute when condition is False.",
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": 'Rename or extract output keys. Example: {"summary": "response"}',
        },
    },
)
async def handle_if_else(config: dict[str, Any], input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    res = await execute_if_else(config, input_data, context)
    return apply_output_mapping(res, config.get("output_mapping", ""))

@register_action(
    type="for_loop",
    label="For Loop",
    icon="🔁",
    description="Iterate over a list from the execution context",
    category="Logic",
    is_loop_type=True,
    config_schema={
        "iterable_key": {
            "type": "string",
            "label": "Iterable Key",
            "required": True,
            "dotpath": True,
            "placeholder": "n1.items",
            "description": "Dot-path to a list in the execution context (e.g. n1.results).",
        },
        "item_variable": {
            "type": "string",
            "label": "Item Variable Name",
            "default": "item",
            "placeholder": "item",
            "description": "Variable name to reference the current item inside the loop body.",
        },
        "max_iterations": {
            "type": "number",
            "label": "Max Iterations",
            "default": 100,
            "min": 1,
            "max": 10000,
            "description": "Safety limit to prevent infinite loops.",
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": 'Rename or extract output keys. Example: {"summary": "response"}',
        },
    },
)
async def handle_for_loop(config: dict[str, Any], input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    res = await execute_for_loop(config, input_data, context)
    return apply_output_mapping(res, config.get("output_mapping", ""))

@register_action(
    type="while_loop",
    label="While Loop",
    icon="🔄",
    description="Repeat while a condition remains true",
    category="Logic",
    is_loop_type=True,
    config_schema={
        "condition": {
            "type": "string",
            "label": "Continue Condition",
            "required": True,
            "multiline": True,
            "placeholder": "context['counter'] < 10",
            "description": "Loop continues while this condition evaluates to True.",
        },
        "max_iterations": {
            "type": "number",
            "label": "Max Iterations",
            "default": 50,
            "min": 1,
            "max": 10000,
            "description": "Safety limit to prevent infinite loops.",
        },
        "output_mapping": {
            "type": "code",
            "label": "Output mapping",
            "required": False,
            "description": 'Rename or extract output keys. Example: {"summary": "response"}',
        },
    },
)
async def handle_while_loop(config: dict[str, Any], input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    res = await execute_while_loop(config, input_data, context)
    return apply_output_mapping(res, config.get("output_mapping", ""))
