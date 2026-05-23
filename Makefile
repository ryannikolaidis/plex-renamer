.PHONY: install tidy check test build-mac build-win clean

install:
	uv sync

tidy:
	uv run ruff format .
	uv run ruff check --fix .

check:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest

# Per-OS packaging targets. These are placeholders until the packaging
# slice ships PyInstaller specs and installer scripts.
build-mac:
	@echo "build-mac: not yet wired; filled in by the packaging slice."
	@exit 1

build-win:
	@echo "build-win: not yet wired; filled in by the packaging slice."
	@exit 1

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
