# HVAC Stability Project Tasks
# Run `just` to see all available commands

# List all available tasks
default:
    @just --list

# Install project dependencies
install:
    uv sync

# Run the application with help
run *ARGS:
    uv run hvac-stability {{ ARGS }}

# Check code syntax and compilation
check:
    uv run python -m py_compile src/hvac_stability/cli.py

# Format code (if ruff is available)
format:
    @echo "Formatting code..."
    uv run ruff format src/

# Lint code (matches CI expectations)
lint:
    @echo "Linting code..."
    uv run ruff check src/
    @echo "Checking syntax..."
    uv run python -m py_compile src/hvac_stability/cli.py

# Run type checking (if mypy is available)  
typecheck:
    @echo "Type checking..."
    -uv run mypy src/ || echo "mypy not available, skipping typecheck"

# Run tests (matches CI expectations)
test:
    @echo "No tests defined yet"
    @echo "Project uses direct CLI testing"

# Build the package
build:
    uv build

# Clean build artifacts
clean:
    rm -rf dist/
    rm -rf .ruff_cache/
    rm -rf src/hvac_stability/__pycache__/
    find . -name "*.pyc" -delete
    find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# Show device list (requires credentials)
devices:
    uv run hvac-stability list --verbose

# Check all devices (requires credentials)
check-all:
    uv run hvac-stability check-device-settings all

# Fix all devices (requires credentials)
fix-all:
    uv run hvac-stability fix-device-settings all --dry-run

# Show version
version:
    @echo "Version: $(grep '^version' pyproject.toml | cut -d'=' -f2 | tr -d ' \"')"

# Development setup - install with dev dependencies
dev-install:
    uv sync --dev

# Update dependencies
update:
    uv lock --upgrade

# Create a release commit
release version:
    @echo "Updating version to {{ version }}"
    sed -i '' 's/^version = .*/version = "{{ version }}"/' pyproject.toml
    jj describe -m "Release {{ version }}"