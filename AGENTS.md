# Agent Guidelines for hvac-stability

## Build/Lint/Test Commands
- **Install dependencies**: `uv sync`
- **Run application**: `uv run hvac-stability <command>`
- **Run single script**: `uv run python src/hvac_stability/cli.py`
- **Check types**: `uv run mypy src/` (if mypy is added)
- **Format code**: `uv run ruff format src/` (if ruff is added)
- **Lint code**: `uv run ruff check src/` (if ruff is added)

## Code Style Guidelines
- **Python version**: 3.13+ (per pyproject.toml)
- **Package management**: Use `uv` and `hatchling` (already configured)
- **Imports**: Use `pathlib` over `os`, prefer modern libraries
- **Type hints**: Use modern Python typing with `from typing import` for annotations
- **CLI framework**: Uses `typer` for command-line interface
- **Configuration**: Uses `environ` with dataclass-style config patterns
- **Error handling**: Use typer.Exit(1) for CLI errors, secho for colored output
- **Data classes**: Use `@define` from `attrs` for data structures
- **File I/O**: Use `pathlib.Path` for file operations, JSON for data persistence

## Project Structure
- Main CLI entry point: `src/hvac_stability/cli.py`
- This is a Python CLI tool for HVAC device management using the Kumo cloud API
- Uses jujutsu (jj) for version control - commit changes with `jj commit -m "message"`
- .venv/ contains all the local copies of the dependencies; use that when determining what a library is capable of.

## Behaviour

- When starting a new change, make sure to run `jj commit` to describe the working copy changes, if there are any.
- Don't use `jj describe` unless you are intending to amend an old piece of work
- make *terse* commits; none of those massive bullet point pull request missive things.
