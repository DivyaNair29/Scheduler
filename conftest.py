"""Makes `pytest` work from the project root by putting app/ on the path.
With this, both `python tests/test_end_to_end.py` and `pytest` work.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))
