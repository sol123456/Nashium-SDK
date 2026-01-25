# Nashium Python SDK

Create and test your matching pennies bot for the Nashium competition.

Your challenge: **guess whether your opponent will output a `1` or a `0`** each round.

In Nashium, your bot's `move(...)` is your *prediction* of your opponent's next move:
- If you match your opponent's move, you win the round.
- If you don't match, you lose the round.

---

## Installation

```bash
pip install -e .
```

---

## Usage Guide

This section walks through the full workflow and documents all available commands.

---

### Create a Bot

Create a new bot from the template:

```bash
nashium scaffold my_bot.py
```

This creates a template bot file with documentation and a simple strategy to get you started.

What you code:
- A `create_bot(seed: int)` function that returns your bot object.
- A `move(state: RoundState) -> int` method that returns `0` or `1`.

What you get access to each round:
- `state.round_index`
- `state.my_history` (your previous guesses)
- `state.opponent_history` (the opponent's previous moves)

---

### Test Your Bot (Qualification)

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

Additional examples:

```bash
nashium qualify my_bot.py --seed 12345
nashium qualify my_bot.py --rounds 5000
```

Save logs for every match the qualifier runs:

```bash
nashium qualify my_bot.py --save-output
nashium qualify my_bot.py --save-output --save-output-dir my_logs
```

---

### Check Determinism

Verify that your bot behaves deterministically:

```bash
nashium check my_bot.py
nashium check my_bot.py --seed 12345
```

---

### Run Head-to-Head Matches

Run a match between two bots:

```bash
nashium run bot_a.py bot_b.py
nashium run bot_a.py bot_b.py --seed 12345
```

Save match logs to files:

```bash
nashium run bot_a.py bot_b.py --save-output
nashium run bot_a.py bot_b.py --save-output --save-output-dir my_logs
```

When `--save-output` is enabled, Nashium writes **two files per match**:

- **`(...)(output).log`**
  The submitted bot's outputs for each round (one `0`/`1` per line).
- **`(...)(score).txt`**
  The per-round score as `1`/`0` (one per line), where:
  - `1` means the submitted bot correctly guessed the opponent (won the round)
  - `0` means it guessed incorrectly (lost the round)

Filenames include bot names and seed, for example:

`my_bot.py vs always_heads (seed 12345) (output).log`

`my_bot.py vs always_heads (seed 12345) (score).txt`

---

## Common Options

The following options are shared by **check**, **run**, and **qualify**:

- `--rounds <int>`  
  Number of rounds per match.  
  Defaults:
  - `check`: 2,000  
  - `run`, `qualify`: 10,000 (server default)

- `--seed <int>`  
  Use a specific random seed for reproducibility.

- `--time-budget <float>`  
  Total time limit in seconds.  
  Default (and server value): **100.0 seconds**

- `--sandbox`  
  Run the bot in a sandboxed subprocess.

---

## Sandbox vs Local Execution

By default, bots are executed locally (unsandboxed):

- Fast execution
- Easy debugging
- Unsafe or hanging code may crash or block the process

When `--sandbox` is enabled:

- Execution matches the server environment exactly
- Handles unsafe code, infinite loops, and hangs safely
- Slower due to process isolation
- Recommended for final testing before submission

---

## Bot Requirements

Your bot must:

- Define `create_bot(seed: int)`
- Implement `move(state: RoundState) -> int`
- Return `0` or `1`
- Be deterministic
- Complete within the time budget

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

Each bot has a **100 second total** time budget.

If exceeded:

- The match continues
- The bot defaults to returning `0`
- This almost always results in a loss

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
