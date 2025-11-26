# Security Guidelines

## Automated Security Scanning

This repository uses multiple automated security tools in CI:

### Secret Detection - Gitleaks
- **Tool**: [gitleaks](https://github.com/gitleaks/gitleaks) (Go-based secret scanner)
- **When**: Every CI run (`make check`)
- **Scans**: Current worktree for hardcoded secrets (API keys, tokens, passwords)
- **Config**: `.gitleaks.toml`
- **Install**: `brew install gitleaks` (macOS) or see [installation guide](https://github.com/gitleaks/gitleaks#installing)

To scan git history (run periodically or before releases):
```bash
gitleaks detect --log-opts="--all" --verbose
```

### Security Static Analysis - Bandit
- **Tool**: [bandit](https://bandit.readthedocs.io/) (Python security linter)
- **When**: Every CI run
- **Detects**: SQL injection, hardcoded passwords, shell injection, etc.
- **Config**: `pyproject.toml` `[tool.bandit]`
- **Install**: Included in `scripts/requirements.txt`

### Dependency Vulnerability Scanning - Safety
- **Tool**: [safety](https://pyup.io/safety/) (checks PyPI packages against CVE database)
- **When**: Interactive mode only (skipped in CI_AUTOMATION to avoid rate limits)
- **Install**: Included in `scripts/requirements.txt`
- **Run manually**: `python -m safety scan`

## Protected Files

This repository uses `.gitignore` to prevent committing sensitive files:

### Private Keys & Certificates
- `*.key`, `*.pem`, `*.p12`, `*.pfx`
- `*.crt`, `*.cer`, `*.der`
- SSH keys (`id_rsa`, `id_ed25519`, etc.)

### Secrets & Credentials
- `.env` files and variants
- Files containing `secret`, `credential`, `password` in name
- API tokens and keys

### Runtime & Temporary Files
- `.xci/` directory (logs and temp files)
- `.xci.log`
- Python cache files

## Before Committing

The CI pipeline automatically scans for secrets, but you can also manually verify:

```bash
# Run gitleaks locally
gitleaks detect --no-git --source . --verbose

# Check what will be staged
git add -n .

# Review changes
git diff --cached

# Check for accidentally staged secrets
git diff --cached | grep -i "password\|secret\|key\|token"
```

## Reporting Security Issues

If you discover sensitive data was accidentally committed:
1. Do NOT push to remote
2. Use `git reset` or `git rm --cached` to unstage
3. Consider using BFG Repo-Cleaner or git-filter-repo if already pushed
