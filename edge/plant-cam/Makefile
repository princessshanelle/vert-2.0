# Common plant-cam tasks. Run from edge/plant-cam/.
#   make dev            create .venv (on top of the system picamera2/numpy)
#   make check          lint + typecheck + unit and integration tests
#   make e2e            end-to-end against the real VERT stack on this machine
#   make bench          per-stage latency/CPU/memory/temperature on fixture images
#   sudo make install   install/upgrade the systemd service (deploy/install.sh)

PY      ?= .venv/bin/python
CONFIG  ?= config.yaml
IMAGES  ?= tests/fixtures/images

.PHONY: dev lint fmt typecheck test test-unit test-integration e2e check bench run install deploy-model fixtures

dev:
	python3 -m venv --system-site-packages .venv
	$(PY) -m pip install -q "numpy==$$(python3 -c 'import numpy; print(numpy.__version__)')" -e ".[dev,ml]"

lint:
	$(PY) -m ruff check plant_cam profiles tests tools
	$(PY) -m ruff format --check plant_cam profiles tests tools

fmt:
	$(PY) -m ruff format plant_cam profiles tests tools
	$(PY) -m ruff check --fix plant_cam profiles tests tools

typecheck:
	$(PY) -m mypy

test-unit:
	$(PY) -m pytest -q -m "not integration and not e2e and not camera"

test-integration:
	$(PY) -m pytest -q -m integration

test:
	$(PY) -m pytest -q

e2e:
	$(PY) -m pytest -q -m e2e -p no:cacheprovider

check: lint typecheck test

fixtures:
	$(PY) -m tests.fixtures.synth $(IMAGES)

bench:
	$(PY) -m plant_cam benchmark --config $(CONFIG) --images $(IMAGES)

run:
	$(PY) -m plant_cam run --config $(CONFIG)

install:
	./deploy/install.sh

deploy-model:
	@test -n "$(MODEL)" || (echo "usage: sudo make deploy-model MODEL=<exported folder>"; exit 1)
	/opt/plant-cam/deploy/deploy-model.sh "$(MODEL)"
