# ci_shared.mk - Shared CI checks for kalshi/zeus
#
# This file contains the common CI pipeline checks used by both repositories.
# Include this in your Makefile with: include ci_shared.mk

# Locate ci_tools config - prefer shared checkout (CI_SHARED_ROOT), then legacy submodule, then installed package
CI_TOOLS_CONFIG_PATH := $(shell \
	if [ -n "$(CI_SHARED_ROOT)" ] && [ -d "$(CI_SHARED_ROOT)/ci_tools/config" ]; then \
		echo "$(CI_SHARED_ROOT)/ci_tools/config"; \
	elif [ -d "ci_shared/ci_tools/config" ]; then \
		echo "ci_shared/ci_tools/config"; \
	else \
		$(PYTHON) -c "import ci_tools; from pathlib import Path; print((Path(ci_tools.__file__).parent / 'config').as_posix())" 2>/dev/null || echo ""; \
	fi \
)
GITLEAKS_CONFIG_PATH := $(shell if [ -f ".gitleaks.toml" ]; then echo ".gitleaks.toml"; elif [ -f "ci_shared/.gitleaks.toml" ]; then echo "ci_shared/.gitleaks.toml"; else echo ""; fi)

# ============================================================================
# STRICT CI STANDARDS - NO OVERRIDES ALLOWED
# ============================================================================
# These values enforce industry best practices and CANNOT be overridden
# by consuming repositories. If your code doesn't meet these standards,
# FIX THE CODE, don't weaken the standards.

# Code Quality Thresholds (MANDATORY - cannot be overridden)
SHARED_PYTEST_THRESHOLD := 80
COVERAGE_GUARD_THRESHOLD := 80
ENABLE_PYLINT := 1
COMPLEXITY_MAX_CYCLOMATIC := 10
COMPLEXITY_MAX_COGNITIVE := 15
STRUCTURE_MAX_CLASS_LINES ?= 100
MODULE_MAX_LINES := 400
FUNCTION_MAX_LINES := 80
METHOD_MAX_PUBLIC := 15
METHOD_MAX_TOTAL := 25
INHERITANCE_MAX_DEPTH := 2
DEPENDENCY_MAX_INSTANTIATIONS := 5

# ============================================================================
# REPOSITORY STRUCTURE (can be overridden for flat layouts)
# ============================================================================
SHARED_SOURCE_ROOT ?= src
SHARED_TEST_ROOT ?= tests
SHARED_DOC_ROOT ?= .

# ============================================================================
# CHECK TARGETS - ALL CODE MUST PASS (source + tests)
# ============================================================================
FORMAT_TARGETS ?= $(SHARED_SOURCE_ROOT) $(SHARED_TEST_ROOT)
SHARED_PYRIGHT_TARGETS := $(SHARED_SOURCE_ROOT)
SHARED_PYLINT_TARGETS := $(SHARED_SOURCE_ROOT) $(SHARED_TEST_ROOT)
SHARED_PYTEST_TARGET := $(SHARED_TEST_ROOT)
SHARED_PYTEST_COV_TARGET := $(SHARED_SOURCE_ROOT)

# ============================================================================
# GUARD ARGUMENTS (strict standards applied - uses constants above)
# ============================================================================
STRUCTURE_GUARD_ARGS := --root $(SHARED_SOURCE_ROOT) --max-class-lines $(STRUCTURE_MAX_CLASS_LINES)
COMPLEXITY_GUARD_ARGS := --root $(SHARED_SOURCE_ROOT) --max-cyclomatic $(COMPLEXITY_MAX_CYCLOMATIC) --max-cognitive $(COMPLEXITY_MAX_COGNITIVE)
MODULE_GUARD_ARGS := --root $(SHARED_SOURCE_ROOT) --max-module-lines $(MODULE_MAX_LINES)
FUNCTION_GUARD_ARGS := --root $(SHARED_SOURCE_ROOT) --max-function-lines $(FUNCTION_MAX_LINES)
METHOD_COUNT_GUARD_ARGS := --root $(SHARED_SOURCE_ROOT) --max-public-methods $(METHOD_MAX_PUBLIC) --max-total-methods $(METHOD_MAX_TOTAL)
UNUSED_MODULE_GUARD_ARGS := --root $(SHARED_SOURCE_ROOT) --exclude tests conftest.py __init__.py

# ============================================================================
# ALLOWED OVERRIDES (minimal - only for special cases)
# ============================================================================
SHARED_CODESPELL_IGNORE ?= $(if $(CI_TOOLS_CONFIG_PATH),$(CI_TOOLS_CONFIG_PATH)/codespell_ignore_words.txt)
SHARED_PYTEST_EXTRA ?=
SHARED_PYTEST_LOG_OPTIONS ?= --log-level=WARNING --log-cli-level=WARNING
PYLINT_ARGS ?=
BANDIT_BASELINE ?=
BANDIT_EXCLUDE ?= artifacts,trash,models,logs,htmlcov
GITLEAKS_SOURCE_DIRS ?= $(strip $(SHARED_SOURCE_ROOT) $(SHARED_TEST_ROOT) scripts docs ci_tools ci_tools_proxy ci_shared.mk shared-tool-config.toml pyproject.toml Makefile README.md SECURITY.md)
SHARED_CLEANUP_ROOTS ?= $(strip $(SHARED_SOURCE_ROOT) $(SHARED_TEST_ROOT) scripts docs ci_tools ci_tools_proxy)

PYTEST_NODES ?= $(shell $(PYTHON) -c "import os; print(max(1, os.cpu_count() - 1))")
PYTHON ?= python

export PYTHONDONTWRITEBYTECODE=1

# Shared CI check pipeline
.PHONY: shared-checks
shared-checks:
	@echo "Running shared CI checks..."
	@FAILED_CHECKS=0; \
	\
	echo "→ Running isort..."; \
	isort --profile black $(FORMAT_TARGETS) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running black..."; \
	black $(FORMAT_TARGETS) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running codespell..."; \
	if [ -n "$(SHARED_CODESPELL_IGNORE)" ] && [ -f "$(SHARED_CODESPELL_IGNORE)" ]; then \
		IGNORE_FLAG="--ignore-words=$(SHARED_CODESPELL_IGNORE)"; \
	else \
		IGNORE_FLAG=""; \
	fi; \
	CODESPELL_SKIP=".git,artifacts,artifacts/*,trash,trash/*,models,node_modules,logs,htmlcov,*.json,*.csv"; \
	codespell --skip="$$CODESPELL_SKIP" --quiet-level=2 $$IGNORE_FLAG || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running vulture..."; \
	VULTURE_WHITELIST=""; \
	if [ -f ".vulture_whitelist.py" ]; then \
		VULTURE_WHITELIST=".vulture_whitelist.py"; \
	fi; \
	vulture $(FORMAT_TARGETS) $$VULTURE_WHITELIST --min-confidence 80 || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running deptry..."; \
	deptry --config pyproject.toml . || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running gitleaks..."; \
	if command -v gitleaks >/dev/null 2>&1; then \
		if [ -n "$(GITLEAKS_CONFIG_PATH)" ]; then \
			CONFIG_FLAG="--config $(GITLEAKS_CONFIG_PATH)"; \
		else \
			CONFIG_FLAG=""; \
		fi; \
		SCAN_TARGETS="$(GITLEAKS_SOURCE_DIRS)"; \
		if [ -z "$$SCAN_TARGETS" ]; then \
			SCAN_TARGETS="."; \
		 fi; \
		GITLEAKS_FAILED=0; \
		for TARGET in $$SCAN_TARGETS; do \
			if [ -e "$$TARGET" ]; then \
				echo "  → scanning $$TARGET"; \
				gitleaks detect $$CONFIG_FLAG --no-git --no-banner --log-level=warn --source "$$TARGET" || GITLEAKS_FAILED=1; \
			fi; \
		done; \
		if [ $$GITLEAKS_FAILED -eq 1 ]; then \
			FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
		fi; \
	else \
		echo "⚠️  gitleaks not installed - skipping secret scan"; \
		echo "   Install: brew install gitleaks (macOS) or see https://github.com/gitleaks/gitleaks#installing"; \
	fi; \
	\
	echo "→ Running bandit..."; \
	BANDIT_BASELINE_FLAG=""; \
	if [ -n "$(BANDIT_BASELINE)" ] && [ -f "$(BANDIT_BASELINE)" ]; then \
		BANDIT_BASELINE_FLAG="-b $(BANDIT_BASELINE)"; \
	fi; \
	$(PYTHON) -m ci_tools.ci_runtime.bandit_wrapper -c pyproject.toml -r $(FORMAT_TARGETS) -q --exclude $(BANDIT_EXCLUDE) $$BANDIT_BASELINE_FLAG || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	if [ -z "$(CI_AUTOMATION)" ]; then \
		echo "→ Running safety..."; \
		$(PYTHON) -m safety scan --json --cache tail || echo "⚠️  safety scan failed or rate limited"; \
	fi; \
	\
	echo "→ Running policy_guard..."; \
	$(PYTHON) -m ci_tools.scripts.policy_guard || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running data_guard..."; \
	$(PYTHON) -m ci_tools.scripts.data_guard || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running structure_guard..."; \
	$(PYTHON) -m ci_tools.scripts.structure_guard $(STRUCTURE_GUARD_ARGS) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running complexity_guard..."; \
	$(PYTHON) -m ci_tools.scripts.complexity_guard $(COMPLEXITY_GUARD_ARGS) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running module_guard..."; \
	$(PYTHON) -m ci_tools.scripts.module_guard $(MODULE_GUARD_ARGS) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running function_size_guard..."; \
	$(PYTHON) -m ci_tools.scripts.function_size_guard $(FUNCTION_GUARD_ARGS) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running inheritance_guard..."; \
	$(PYTHON) -m ci_tools.scripts.inheritance_guard --root $(SHARED_SOURCE_ROOT) --max-depth $(INHERITANCE_MAX_DEPTH) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running method_count_guard..."; \
	$(PYTHON) -m ci_tools.scripts.method_count_guard $(METHOD_COUNT_GUARD_ARGS) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running dependency_guard..."; \
	$(PYTHON) -m ci_tools.scripts.dependency_guard --root $(SHARED_SOURCE_ROOT) --max-instantiations $(DEPENDENCY_MAX_INSTANTIATIONS) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running unused_module_guard..."; \
	$(PYTHON) -m ci_tools.scripts.unused_module_guard $(UNUSED_MODULE_GUARD_ARGS) --strict || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running documentation_guard..."; \
	$(PYTHON) -m ci_tools.scripts.documentation_guard --root $(SHARED_DOC_ROOT) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running ruff..."; \
	ruff check --target-version=py310 --fix $(SHARED_SOURCE_ROOT) $(SHARED_TEST_ROOT) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running pyright..."; \
	pyright --warnings $(SHARED_PYRIGHT_TARGETS) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running pylint..."; \
	pylint -j 1 $(PYLINT_ARGS) $(SHARED_PYLINT_TARGETS) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Cleaning bytecode..."; \
	for DIR in $(SHARED_CLEANUP_ROOTS); do \
		if [ -d "$$DIR" ]; then \
			find "$$DIR" -name "*.pyc" -delete 2>/dev/null || true; \
		fi; \
	done; \
	for DIR in $(SHARED_CLEANUP_ROOTS); do \
		if [ -d "$$DIR" ]; then \
			find "$$DIR" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true; \
		fi; \
	done; \
	\
	echo "→ Running pytest..."; \
	pytest $(SHARED_PYTEST_TARGET) --cov=$(SHARED_PYTEST_COV_TARGET) --cov-fail-under=$(SHARED_PYTEST_THRESHOLD) --cov-report=term --strict-markers -W error $(SHARED_PYTEST_LOG_OPTIONS) $(SHARED_PYTEST_EXTRA) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running coverage_guard..."; \
	$(PYTHON) -m ci_tools.scripts.coverage_guard --threshold $(COVERAGE_GUARD_THRESHOLD) --data-file "$(CURDIR)/.coverage" || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo "→ Running compileall..."; \
	$(PYTHON) -m compileall -q $(SHARED_SOURCE_ROOT) $(SHARED_TEST_ROOT) || FAILED_CHECKS=$$((FAILED_CHECKS + 1)); \
	\
	echo ""; \
	if [ $$FAILED_CHECKS -eq 0 ]; then \
		echo "✅ All shared CI checks passed!"; \
		exit 0; \
	else \
		echo "❌ $$FAILED_CHECKS check(s) failed"; \
		exit 1; \
	fi
