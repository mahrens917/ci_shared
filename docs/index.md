# claude-ci-tools Documentation

claude-ci-tools is a shared toolkit that automates CI repair loops and enforces
quality gates for multiple repositories. This documentation explains how to
install and operate the toolkit, how the automation loop works, and how to
extend the guard suite.

## Contents
1. [Getting Started](getting-started.md) – installation, configuration, and basic usage
2. [Automation Workflow](automation.md) – how `ci_tools.ci` orchestrates Claude-assisted CI runs
3. [Guard Suite](guard-suite.md) – reference guide for the shipped guard scripts and Makefile helpers
4. [Development Guide](development.md) – contributing, testing, and maintenance practices

### Additional References
- Root [`README.md`](../README.md) – quick synopsis and install snippet
- [`CLAUDE.md`](../CLAUDE.md) – AI-specific guardrails for Claude Code
- [`SECURITY.md`](../SECURITY.md) – secure usage and secret handling guidance
