"""Measure Python-tracked memory of warmed randomization and coverage sampling.

Point PYTHONPATH at the Python directory and root of the version to measure.
Results exclude native allocations and should be compared across fresh processes.
"""

import json
import tracemalloc
import svtypes
from benchmarks.randomization import ScalarPacket as RandomPacket
from benchmarks.coverage import ScalarPacket as CoveragePacket

random = RandomPacket()
coverage = CoveragePacket()
for i in range(5):
    assert random.randomize()
    coverage.opcode.value = i
    coverage.cg.sample()
result = {"svtypes_source": svtypes.__file__, "random_calls": 30, "samples": 2000}
for name in ('random', 'coverage'):
    tracemalloc.start()
    if name == 'random':
        for i in range(30):
            assert random.randomize()
    else:
        for i in range(2000):
            coverage.opcode.value = i % 256
            coverage.cg.sample()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result[name] = {'retained_python_bytes': current, 'peak_python_bytes': peak}
print(json.dumps(result))
