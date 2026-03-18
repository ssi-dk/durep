import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="A hello-world CLI.")
    parser.add_argument("--name", required=True, help="Name to greet.")
    args = parser.parse_args()
    print(f"Hello, {args.name}!")
