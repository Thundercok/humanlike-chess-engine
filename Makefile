# Makefile for Chess Data Pipeline

.PHONY: setup download run clean

setup:
	# Create a virtual environment and install Python dependencies
	python3 -m venv venv
	. venv/bin/activate && pip install -r requirements.txt
	# Ensure Stockfish is installed (Homebrew)
	brew install stockfish

download:
	python3 download_pgn.py

run:
	python3 pipeline.py

clean:
	rm -rf venv __pycache__ data/*.zst data/*.jsonl
