"""Runtime CI must execute, rather than skip, the real media integration tests."""
import sys
import unittest

suite = unittest.defaultTestLoader.discover("/tests")
result = unittest.TextTestRunner(verbosity=2).run(suite)
if result.skipped:
    print(f"Unexpected skipped tests: {result.skipped}", file=sys.stderr)
sys.exit(not result.wasSuccessful() or bool(result.skipped))
