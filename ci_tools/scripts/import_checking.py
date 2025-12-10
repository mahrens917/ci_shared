"""Module import checking utilities."""

from pathlib import Path
from typing import Set


def check_exact_match(module_name: str, file_stem: str, all_imports: Set[str], root: Path) -> bool:
    """Check for exact module name matches."""
    if module_name in all_imports:
        return True
    if f"src.{module_name}" in all_imports:
        return True
    if file_stem in all_imports:
        return True
    if f"{root.name}.{module_name}" in all_imports:
        return True
    return False


def check_child_imported(module_name: str, all_imports: Set[str]) -> bool:
    """Check if any child module is imported."""
    for imported in all_imports:
        if imported.startswith(module_name + "."):
            return True
        if imported.startswith(f"src.{module_name}."):
            return True
    return False


def has_specific_child_imports(parent: str, module_name: str, all_imports: Set[str]) -> bool:
    """Check if parent has specific child imports that exclude this module."""
    return any(imp.startswith(parent + ".") and imp != module_name for imp in all_imports)


def check_parent_imported(module_name: str, all_imports: Set[str]) -> bool:
    """Check if a parent module is imported wholesale."""
    module_parts = module_name.split(".")
    for i in range(len(module_parts) - 1):
        parent = ".".join(module_parts[: i + 1])
        if parent in all_imports or f"src.{parent}" in all_imports:
            if not has_specific_child_imports(parent, module_name, all_imports):
                return True
    return False


def module_is_imported(
    module_name: str,
    file_stem: str,
    all_imports: Set[str],
    root: Path,
) -> bool:
    """Check if a module is imported anywhere in the codebase."""
    if not module_name:
        return True

    if check_exact_match(module_name, file_stem, all_imports, root):
        return True

    if check_child_imported(module_name, all_imports):
        return True

    if check_parent_imported(module_name, all_imports):
        return True

    return False
