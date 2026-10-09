# The server shares the contract comparison with the scanner package
# (`pip install ./scanner`). Running from a source checkout works without
# installing it.
import importlib.util
import os
import sys

if importlib.util.find_spec("mergeclear") is None:
    sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scanner"))
