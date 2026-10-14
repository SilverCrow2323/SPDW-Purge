import sys

from . import cli


def main():
    sys.exit(cli.main(sys.argv[1:]))


if __name__ == "__main__":
    main()
