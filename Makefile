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

# Per-OS packaging targets. These are intentional placeholders until the
# packaging slice ships PyInstaller specs and installer scripts. Exit 0
# so a fresh contributor running `make build-mac` does not see a non-zero
# exit and assume the project is broken.
build-mac:
	@echo "build-mac: not yet wired. The packaging slice ships PyInstaller specs."

build-win:
	@echo "build-win: not yet wired. The packaging slice ships PyInstaller specs."

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
