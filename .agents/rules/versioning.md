# Versioning Rules

- Tag format: `vX.Y.Z` (3 numbers, each is a single digit 0-9)
- When Z reaches 9, bump Y and reset Z to 0: `v3.1.9` → `v3.2.0`
- When Y reaches 9, bump X and reset Y and Z to 0: `v3.9.9` → `v4.0.0`
- Never go to double digits like `v3.1.10` — that is WRONG
