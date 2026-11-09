PY ?= python

.PHONY: run stability dashboard test all

run:
	PYTHONPATH=src $(PY) -m resort_ranking.pipeline

stability:
	PYTHONPATH=src $(PY) -m resort_ranking.stability

dashboard:
	PYTHONPATH=src $(PY) -m resort_ranking.dashboard_export

test:
	$(PY) -m pytest -q

all: run stability dashboard test
