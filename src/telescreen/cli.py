from telescreen import greet


def main() -> int:
    """Run the default command-line entrypoint."""
    print(greet("World"))
    return 0
