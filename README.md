# Nashium Python SDK

Create and test your matching pennies bot for the Nashium competition.

---

## Installation

```bash
pip install -e .
```

---

## Quick Start

### 1. Create a Bot

```bash
nashium scaffold my_bot.py
```

This creates a template bot file with documentation and a simple strategy to get you started.

---

### 2. Test Your Bot

Run the qualifier to test against sample opponents:

```bash
nashium qualify my_bot.py
```

This will:

- Check that your bot is deterministic  
- Run matches against sample opponents:
  - always_heads
  - always_tails
  - alternator
  - mirror
  - frequency_counter
- Report whether you qualify (must exceed **51.55% win rate**)

---

### 3. Check Determinism

```bash
nashium check my_bot.py
```

---

### 4. Run Head-to-Head Matches

```bash
nashium run bot_a.py bot_b.py
```

---

## Commands

### nashium scaffold <path>

```bash
nashium scaffold my_bot.py
```

---

### nashium qualify <bot>

```bash
nashium qualify my_bot.py
nashium qualify my_bot.py --seed 12345
nashium qualify my_bot.py --rounds 5000
```

Options:

- `--seed <int>` – Use a specific seed
- `--rounds <int>` – Rounds per match (default, server value: 10,000)
- `--time-budget <float>` – Time limit in seconds (default, server value: 100)

---

### nashium check <bot>

```bash
nashium check my_bot.py
nashium check my_bot.py --seed 12345
```

Options:

- `--seed <int>`
- `--rounds <int>`
- `--time-budget <float>`

---

### nashium run <bot_a> <bot_b>

```bash
nashium run bot_a.py bot_b.py
nashium run bot_a.py bot_b.py --seed 12345
nashium run bot_a.py bot_b.py --invert-opponent
```

Options:

- `--seed <int>`
- `--rounds <int>`
- `--invert-opponent`
- `--time-budget <float>`

---

## Bot Requirements

Your bot must:

- Define `create_bot(seed: int)`
- Implement `move(state: RoundState) -> int`
- Return `0` or `1`
- Be deterministic
- Complete within 100 seconds total

---

## Example Bot

```python
import random
from nashium import RoundState

class MyBot:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)

    def move(self, state: RoundState) -> int:
        if state.round_index == 0:
            return self.rng.choice([0, 1])
        return state.opponent_history[-1]

def create_bot(seed: int):
    return MyBot(seed)
```

---

## RoundState Fields

- `state.round_index`
- `state.my_history`
- `state.opponent_history`

---

## Time Limit Behavior

Each bot has **100 seconds total**.

If exceeded:
- Match continues
- Bot defaults to `0`
- Almost always loses

---

## Reproducibility

```bash
nashium run bot_a.py bot_b.py
# ℹ Using random seed: 1234567890

nashium run bot_a.py bot_b.py --seed 1234567890
```

---

## Disclaimer

Local qualification is indicative only.

Server results may differ due to:
- Different seeds
- Hardware differences
- Execution timing

Run multiple tests for confidence.
