from __future__ import annotations

import os

from generate import generate_all_customers, load_generated_customers


def main() -> None:
    count = int(os.environ.get("CUSTOMER_COUNT", "500"))
    existing = load_generated_customers(count)

    if len(existing) >= count:
        print(f"Using existing generated dataset ({len(existing)} customers).")
        return

    print(f"Generating {count} customers for build output...")
    generate_all_customers(count)
    print("Build assets generated.")


if __name__ == "__main__":
    main()
