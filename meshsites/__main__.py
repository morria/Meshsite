"""python -m meshsites — same as the `meshsites` console script."""
import sys

from .cli import main

sys.exit(main())
