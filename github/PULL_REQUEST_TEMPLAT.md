## Short summary
One-line summary of what changed (what, where).

## What changed
- Bullet the main code changes (files / functions / endpoints / DB tables)
- Keep it readable for a reviewer skimming the diff

## Why this change
Explain the motivation and the problem this PR solves.
If this fixes a ticket/issue, reference it: `Fixes #1234` or `Refs #1234`.

## Compatibility & Public API
- Does this change public interfaces (APIs, DB schema, message formats)? If yes, list them.
- Migration needed? (yes/no). If yes, describe the migration steps.

## How I tested
Automated:
- Unit tests added / updated? [y/n]  
- Integration tests added? [y/n]

Manual steps (if any):
1. `git checkout <branch>`
2. `pip install -r requirements-dev.txt`
3. `pre-commit run --all-files`
4. `pytest -q`
5. Start local server: `uvicorn main:app --reload` and exercise the UI at `http://localhost:8000/ui/`

If this touches DB schema:
- Run migrations (dev): `alembic upgrade head`
- To generate migration: `alembic revision --autogenerate -m "describe"`

## Security / Privacy / Data considerations
- Does this change handle secrets, PII, or external credentials? [yes/no]
- If yes, describe what was considered and how data is protected.

## Check list
- [ ] My code follows the style guidelines (Black / Ruff / Isort)
- [ ] I added tests for new behavior
- [ ] I ran `pre-commit` locally
- [ ] CI (tests + linters) passes
- [ ] Documentation updated (README, docs, or inline comments) if needed
- [ ] DB migrations added (if schema changed)

## Release notes / changelog entry
Provide a short blurb suitable for a changelog or release notes (1-2 lines).

## Rollback plan
If this causes issues, revert quickly:
```bash
# fast revert (creates a revert commit)
git revert <merge-commit-sha>

# or reset branch (if safe and you control it)
git checkout main
git pull
git branch -D <feature-branch>








# 1. Short summary

# A single sentence describing what changed.
# Think of it like the title of a newspaper article.

# “Refactored API rate-limit logic to prevent request flooding.”

# 2. What changed

# This is the diff in words, not in code.
# List the important modifications: files touched, functions updated, new endpoints added, bugs fixed.

# Why?
# Reviewers skim this to understand the impact quickly.

# 3. Why this change

# Explain the motivation: what problem existed before?
# Why was this work necessary?

# This section helps reviewers understand the context.

# 4. Compatibility & Public API

# Here you answer:

# Did we change API endpoints?

# Did we modify the database schema?

# Did we change message formats?

# Do we need a migration?
# If yes → write the exact migration step.

# This section protects teams from breaking production.

# 5. How I tested

# You describe exactly how you validated that the change works.

# Includes:

# Unit tests added? y/n

# Integration tests? y/n

# Manual steps someone else can follow to reproduce your checks

# This section ensures the PR is safe to merge.

# 6. Security / Privacy / Data considerations

# If your PR touches:

# credentials

# tokens

# user data

# conversation history

# personal information

# …you document it here.

# This is critical for compliance and safety.

# 7. Check list

# This is your final self-audit before merging:

# Code formatted?

# Linters pass?

# Tests updated?

# Docs updated?

# Migrations added?

# This ensures quality and consistency.

# 8. Release notes / changelog entry

# A tiny, user-facing line summarizing the change as it will appear in the changelog.

# “Improved embedding retrieval performance by 35%.”

# 9. Rollback plan

# Instructions to revert the PR fast if production breaks.

# Two options:

# git revert → clean rollback with a revert commit

# git reset → force restore the branch (use with caution)

# This is a safety net.