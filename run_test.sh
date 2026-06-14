#!/bin/bash
cd /home/lenovo/workspace/goat2
python3 -c "
import sys
sys.path.insert(0, '.')
from memory.episodic.chromadb_base import ChromaBase
print('Import OK')
cb = ChromaBase(persist_dir='/tmp/goat2_test_chroma')
print(f'Instantiation OK: persist_dir={cb._persist_dir}')
"
