"""Allow running Nova as: python -m nova <file.nova>"""
import sys
from .cli import main

sys.exit(main())
