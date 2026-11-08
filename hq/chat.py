"""Optional Chat companion launcher: python -m hq.chat --help."""
import sys
from pathlib import Path


def main(argv=None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'chat' / 'server'))
    from chat.__main__ import main as chat_main
    chat_main(argv)


if __name__ == '__main__':
    main()
