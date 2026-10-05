import sys
from pathlib import Path
from app.services.automatic_workflow import execute

if __name__ == '__main__':
    execute(Path(sys.argv[1]), sys.argv[2])
