"""Convenience entry point: python run_api.py"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent / "src"))
from fraudgraph.api.main import main
if __name__ == "__main__":
    main()
