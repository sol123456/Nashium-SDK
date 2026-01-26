from __future__ import annotations

import argparse
import random
import re
from pathlib import Path

from .backend import get_backend
from .formatting import (
    Colors,
    format_result,
    format_memory_warning,
    format_time_warning,
    print_dim,
    print_failure,
    print_header,
    print_info,
    print_success,
    print_warning,
)
from .loader import load_bot_from_file
from .sample_bots import determinism_test_bot_sources, sample_leaderboard_bot_sources
from ..core import InteractionResult, MatchConfig, run_match, run_match_trace


def _generate_random_seed() -> int:
    """Generate a random seed for match execution."""
    return random.randint(0, (1 << 63) - 1)


def _safe_filename_component(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*]", "_", value)
    cleaned = cleaned.strip().rstrip(".")
    return cleaned or "match"


def _parse_memory_limit_bytes(value: str | None) -> int | None:
    if value is None:
        return None

    s = str(value).strip().lower()
    if not s:
        return None

    try:
        return int(s)
    except ValueError:
        pass

    units = {
        "k": 1024,
        "kb": 1024,
        "m": 1024 * 1024,
        "mb": 1024 * 1024,
        "g": 1024 * 1024 * 1024,
        "gb": 1024 * 1024 * 1024,
    }
    for suffix, mult in units.items():
        if s.endswith(suffix):
            num = s[: -len(suffix)].strip()
            try:
                return int(float(num) * mult)
            except ValueError:
                raise ValueError(f"Invalid memory limit: {value!r}")

    raise ValueError(f"Invalid memory limit: {value!r}")


def _get_effective_memory_limit_bytes(*, sandbox: bool, docker: bool, arg_value: str | None) -> int | None:
    if not (sandbox or docker):
        return None

    if arg_value is None:
        return 200 * 1024 * 1024

    return _parse_memory_limit_bytes(arg_value)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    i = 2
    while True:
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _write_match_logs(
        *,
        out_dir: Path,
        match_label: str,
        seed: int,
        trace,
        captured_output: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    base = _safe_filename_component(f"{match_label} (seed {seed})")
    output_path = _unique_path(out_dir / f"{base} (output).log")
    score_path = _unique_path(out_dir / f"{base} (score).txt")

    output_path.write_text(captured_output, encoding="utf-8")
    scores = [
        "1" if s == o else "0"
        for s, o in zip(trace.submitted_moves, trace.leaderboard_moves_effective)
    ]
    score_path.write_text("\n".join(scores) + ("\n" if scores else ""), encoding="utf-8")


def _get_execution_mode(sandbox: bool, docker: bool) -> str:
    """Get human-readable execution mode string."""
    if docker:
        return "docker"
    elif sandbox:
        return "sandbox"
    return "local"


def _get_execution_flags(sandbox: bool, docker: bool) -> str:
    """Get CLI flags string for reproducing the run."""
    if docker:
        return " --docker"
    elif sandbox:
        return " --sandbox"
    return ""


# ============================================================================
# SCAFFOLD COMMAND
# ============================================================================

SCAFFOLD_TEMPLATE = '''"""
Nashium Bot Template
====================

Welcome! This is your bot file. Your goal is to PREDICT what your opponent
will play (Heads=0 or Tails=1) in a matching pennies game.

HOW THE GAME WORKS:
-------------------
- Each round, both you and your opponent choose either 0 (Heads) or 1 (Tails)
- If you MATCH your opponent's choice, YOU WIN that round
- If you DON'T match, your opponent wins
- You play 10,000 rounds per match
- To qualify, you need to win >51.55% of rounds (statistically significant)

WHAT YOU HAVE ACCESS TO:
------------------------
In the `move()` method, you receive a `state` object with:

    state.round_index       - Current round number (0 to 9,999)
    state.my_history        - Tuple of YOUR past moves, e.g., (0, 1, 1, 0, ...)
    state.opponent_history  - Tuple of OPPONENT'S past moves

RULES:
------
1. Your bot MUST be deterministic given the same seed
2. You have 100 seconds TOTAL for all 10,000 moves (not per move!)
3. You must return either 0 or 1 from move()
4. Don't use external resources (network, files, etc.)

TIPS:
-----
- Look for patterns in state.opponent_history
- Simple strategies often beat complex ones
- Test locally with: nashium qualify my_bot.py
- Check determinism with: nashium check my_bot.py

EXAMPLE PATTERNS TO DETECT:
---------------------------
- Does opponent always play the same thing?
- Does opponent alternate?
- Does opponent copy your last move?
- Does opponent play the opposite of your last move?
"""

import random
from nashium import RoundState


class Bot:
    def __init__(self, seed: int = None):
        """
        Called once when your bot is created.

        Args:
            seed: A random seed for reproducibility. Use this to initialize
                  any random number generators so your bot is deterministic.
        """
        # Always use the provided seed for randomness!
        self.rng = random.Random(seed)

        # You can store any state you need here
        self.opponent_patterns = {}

    def move(self, state: RoundState) -> int:
        """
        Called each round to get your move.

        Args:
            state: Contains round_index, my_history, and opponent_history

        Returns:
            0 for Heads, 1 for Tails (your PREDICTION of what opponent will play)
        """
        # Round 0: No history yet, just guess
        if state.round_index == 0:
            return self.rng.choice([0, 1])

        # Simple strategy: Predict opponent will repeat their last move
        # This beats "always same" and "repeat last" opponents
        last_opponent_move = state.opponent_history[-1]

        # TODO: Replace this with your own strategy!
        # Ideas:
        #   - Track opponent move frequencies
        #   - Look for alternating patterns
        #   - Detect if opponent is mirroring you
        #   - Use more sophisticated pattern matching

        return last_opponent_move


def create_bot(seed: int):
    """
    Factory function - must return an instance of your bot.
    Don't change the function signature!
    """
    return Bot(seed)
'''


def cmd_scaffold(args: argparse.Namespace) -> int:
    target = Path(args.path)

    if target.exists():
        print_failure(f"File already exists: {target}")
        print_info("Choose a different filename or delete the existing file.")
        return 1

    target.write_text(SCAFFOLD_TEMPLATE, encoding="utf-8")

    print_header("Bot Template Created!")
    print_success(f"Created: {target}")
    print()
    print("  Next steps:")
    print(f"    1. Edit {Colors.CYAN}{target}{Colors.RESET} with your strategy")
    print(f"    2. Test it: {Colors.CYAN}nashium qualify {target}{Colors.RESET}")
    print(f"    3. Check determinism: {Colors.CYAN}nashium check {target}{Colors.RESET}")
    print()
    return 0


# ============================================================================
# DETERMINISM CHECK
# ============================================================================

def run_determinism_check(
        bot_path: Path,
        seed: int,
        config: MatchConfig,
        sandbox: bool = False,
        docker: bool = False,
        verbose: bool = True,
        save_output: bool = False,
        save_output_dir: Path | None = None,
) -> tuple[bool, list[str]]:
    """
    Run determinism check against test bots.

    Returns:
        (is_deterministic, list of failed opponent names)

    Raises:
        RuntimeError: If Docker backend requested but unavailable.
    """
    backend = get_backend(sandbox=sandbox, docker=docker)
    opponent_sources = determinism_test_bot_sources()
    failed_opponents = []

    if verbose:
        print_info("Running your bot twice with the same seed to verify identical behavior...")
        print()

    for name, opponent_source in opponent_sources:
        trace_1 = backend.run_match_trace_file_vs_source(bot_path, opponent_source, seed, config)
        trace_2 = backend.run_match_trace_file_vs_source(bot_path, opponent_source, seed, config)

        if save_output and save_output_dir is not None:
            out_1 = "\n".join(str(x) for x in trace_1.submitted_moves)
            if out_1:
                out_1 += "\n"
            _write_match_logs(
                out_dir=save_output_dir,
                match_label=f"{bot_path.name} vs {name} (determinism run 1)",
                seed=seed,
                trace=trace_1,
                captured_output=out_1,
            )

            out_2 = "\n".join(str(x) for x in trace_2.submitted_moves)
            if out_2:
                out_2 += "\n"
            _write_match_logs(
                out_dir=save_output_dir,
                match_label=f"{bot_path.name} vs {name} (determinism run 2)",
                seed=seed,
                trace=trace_2,
                captured_output=out_2,
            )

        same = trace_1.submitted_moves == trace_2.submitted_moves

        if verbose:
            if same:
                print_success(f"vs {name}: Deterministic ✓")
            else:
                print_failure(f"vs {name}: NOT deterministic!")
                for i, (m1, m2) in enumerate(zip(trace_1.submitted_moves, trace_2.submitted_moves)):
                    if m1 != m2:
                        print_dim(f"First difference at round {i}: got {m1} then {m2}")
                        break

        if not same:
            failed_opponents.append(name)

    return (len(failed_opponents) == 0, failed_opponents)


# ============================================================================
# QUALIFY COMMAND
# ============================================================================

def cmd_qualify(args: argparse.Namespace) -> int:
    submitted_path = Path(args.bot)

    if not submitted_path.exists():
        print_failure(f"Bot file not found: {submitted_path}")
        return 1

    sandbox = getattr(args, 'sandbox', False)
    docker = getattr(args, 'docker', False)
    mode_str = _get_execution_mode(sandbox, docker)
    mode_flags = _get_execution_flags(sandbox, docker)

    # Validate backend early (catches Docker issues before we start)
    try:
        backend = get_backend(sandbox=sandbox, docker=docker)
    except RuntimeError as e:
        print_failure(str(e))
        return 1

    print_header(f"Qualifying: {submitted_path.name}")

    if docker:
        print_success("✓ Docker backend active")
        print_dim("  Running in isolated containers (matches server environment)")
    elif sandbox:
        print_dim(f"Execution mode: {mode_str}")
    else:
        print_warning("RAM usage is not measured in local mode (both bots share one Python process).")
        print_dim("Use --sandbox for host-measured RAM usage and enforcement.")

    # Determine seed
    if args.seed is not None:
        seed = args.seed
        print_info(f"Using provided seed: {seed}")
    else:
        seed = _generate_random_seed()
        print_info(f"Using random seed: {seed}")

    print_dim(f"To reproduce this exact run: nashium qualify {submitted_path.name} --seed {seed}{mode_flags}")
    print()

    try:
        memory_limit_bytes = _get_effective_memory_limit_bytes(
            sandbox=sandbox,
            docker=docker,
            arg_value=getattr(args, "memory", None),
        )
    except ValueError as e:
        print_failure(str(e))
        return 1

    config = MatchConfig(
        rounds=args.rounds,
        max_total_time_seconds_per_bot=args.time_budget,
        max_total_memory_bytes_per_bot=memory_limit_bytes,
    )

    save_output = bool(getattr(args, "save_output", False))

    # Create output directory structure if saving
    output_base_dir = None
    if save_output:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        bot_stem = submitted_path.stem
        output_base_dir = Path(getattr(args, "save_output_dir", "nashium_match_logs")) / f"{bot_stem}_qualifier_{timestamp}"
        output_base_dir.mkdir(parents=True, exist_ok=True)
        print_info(f"Saving match logs to: {output_base_dir}/")

    # =========== STEP 1: Determinism Check ===========
    determinism_config = config

    # Note: run_determinism_check creates its own backend, but we already validated above
    is_deterministic, failed_det_opponents = run_determinism_check(
        submitted_path,
        seed,
        determinism_config,
        sandbox=sandbox,
        docker=docker,
        verbose=False,
        save_output=False,
        save_output_dir=None,
    )

    if is_deterministic:
        print_success("Determinism check passed")
    else:
        print_failure(f"Determinism check FAILED (vs: {', '.join(failed_det_opponents)})")
    print()

    # =========== STEP 2: Performance Tests ===========
    opponents = sample_leaderboard_bot_sources()

    results = []
    max_time_used = 0.0
    max_mem_used: int | None = None
    all_pass = True
    any_timed_out = False
    any_mem_exceeded = False

    for name, opponent_source in opponents:
        if save_output and output_base_dir:
            trace = backend.run_match_trace_file_vs_source(submitted_path, opponent_source, seed, config)
            summary = trace.summary

            # Create subfolder: "my_bot3.py vs always_heads (seed 123456789)"
            match_folder_name = f"{submitted_path.name} vs {name} (seed {seed})"
            match_dir = output_base_dir / match_folder_name
            match_dir.mkdir(parents=True, exist_ok=True)

            bot_stem = submitted_path.stem

            # Write output file (bot's moves)
            output_file = match_dir / f"{bot_stem}_output.txt"
            submitted_outputs = "\n".join(str(x) for x in trace.submitted_moves)
            if submitted_outputs:
                submitted_outputs += "\n"
            output_file.write_text(submitted_outputs)

            # Calculate per-round wins (1 = submitted bot won, 0 = lost)
            per_round_wins = [
                1 if s_move != o_move else 0
                for s_move, o_move in zip(trace.submitted_moves, trace.leaderboard_moves_effective)
            ]

            # Write score file
            score_file = match_dir / f"{bot_stem}_score.txt"
            scores = "\n".join(str(x) for x in per_round_wins)
            if scores:
                scores += "\n"
            score_file.write_text(scores)

            if sandbox or docker:
                usage_dir = match_dir / "usage"
                usage_dir.mkdir(parents=True, exist_ok=True)
                (usage_dir / "cpu_usage.txt").write_text(
                    "\n".join(str(x) for x in trace.submitted_cpu_usage_samples)
                    + ("\n" if trace.submitted_cpu_usage_samples else ""),
                    encoding="utf-8",
                )
                (usage_dir / "ram_usage.txt").write_text(
                    "\n".join(str(x) for x in trace.submitted_ram_usage_samples)
                    + ("\n" if trace.submitted_ram_usage_samples else ""),
                    encoding="utf-8",
                )
        else:
            summary = backend.run_match_file_vs_source(submitted_path, opponent_source, seed, config)

        max_time_used = max(max_time_used, summary.submitted_time_seconds)
        if summary.submitted_memory_bytes_peak is not None:
            if max_mem_used is None or summary.submitted_memory_bytes_peak > max_mem_used:
                max_mem_used = summary.submitted_memory_bytes_peak
        passed = summary.result == InteractionResult.S_WIN and summary.stat_sig

        if summary.submitted_timed_out:
            any_timed_out = True

        if summary.submitted_memory_exceeded:
            any_mem_exceeded = True

        status, explanation = format_result(
            summary.result, summary.stat_sig, summary.submitted_wins, summary.rounds
        )

        results.append({
            'name': name,
            'passed': passed,
            'status': status,
            'explanation': explanation,
            'wins': summary.submitted_wins,
            'rounds': summary.rounds,
            'win_rate': summary.submitted_win_rate,
            'time': summary.submitted_time_seconds,
            'timed_out': summary.submitted_timed_out,
            'mem_peak': summary.submitted_memory_bytes_peak,
            'mem_exceeded': summary.submitted_memory_exceeded,
        })

        if not passed:
            all_pass = False

    # Print results table
    print(f"  {'Opponent':<20} {'Result':<25} {'Win Rate':<12} {'Time':<10} {'Mem':<10}")
    print(f"  {'-' * 20} {'-' * 25} {'-' * 12} {'-' * 10} {'-' * 10}")

    for r in results:
        win_rate_str = f"{r['win_rate'] * 100:.1f}%"
        time_str = f"{r['time']:.2f}s"
        if r['timed_out']:
            time_str += " ⏱"

        mem_peak = r.get('mem_peak')
        if mem_peak is None:
            mem_str = "n/a"
        else:
            mem_str = f"{mem_peak / (1024 * 1024):.1f}MB"
        if r.get('mem_exceeded'):
            mem_str += " !"

        if r['passed']:
            icon = f"{Colors.GREEN}✓{Colors.RESET}"
        else:
            icon = f"{Colors.RED}✗{Colors.RESET}"

        print(f"  {icon} {r['name']:<18} {r['status']:<35} {win_rate_str:<12} {time_str:<10} {mem_str:<10}")

    print()

    if any_timed_out:
        print_warning("Your bot exceeded the 100 second time limit in one or more matches!")
        print_dim("When a bot times out, it defaults to playing 0 for all remaining rounds.")
        print()

    if any_mem_exceeded:
        print_warning("Your bot exceeded the memory limit in one or more matches!")
        print_dim("When a bot exceeds memory, it defaults to playing 0 for all remaining rounds.")
        print()

    print(f"  {Colors.BOLD}Time Analysis:{Colors.RESET}")
    print(f"    {format_time_warning(max_time_used, args.time_budget)}")
    print()

    if config.max_total_memory_bytes_per_bot is not None and max_mem_used is not None:
        print(f"  {Colors.BOLD}Memory Analysis:{Colors.RESET}")
        print(f"    {format_memory_warning(max_mem_used, config.max_total_memory_bytes_per_bot)}")
        print_dim("    (Limit includes Python/runtime overhead.)")
        print()

    # =========== FINAL VERDICT ===========
    qualified = all_pass and is_deterministic

    if qualified:
        print_header("🎉 QUALIFIED!")
        print_success("Your bot beat all test opponents with statistical significance!")
        print_success("Your bot is deterministic!")
        print()
        print(f"  {Colors.YELLOW}⚠ DISCLAIMER:{Colors.RESET}")
        print("    This result is only an indication. On the server, your bot will run")
        print("    with a different seed and on different hardware, which may cause")
        print("    different results. Consider running this qualifier multiple times")
        print("    to ensure consistent performance.")
        print()
        return 0
    else:
        print_header("❌ DID NOT QUALIFY")

        failure_reasons = []

        if not is_deterministic:
            failure_reasons.append(f"  {Colors.RED}•{Colors.RESET} Your bot is NOT deterministic")

        failed_opponents = [r['name'] for r in results if not r['passed']]
        if failed_opponents:
            failure_reasons.append(f"  {Colors.RED}•{Colors.RESET} Failed to beat: {', '.join(failed_opponents)}")

        print()
        print("  Reasons for failure:")
        for reason in failure_reasons:
            print(reason)
        print()

        if not is_deterministic:
            print(f"  {Colors.BOLD}Determinism:{Colors.RESET}")
            print("    Your bot must produce identical moves when given the same seed.")
            print(f"    Run {Colors.CYAN}nashium check {submitted_path.name}{Colors.RESET} for detailed diagnostics.")
            print()

        if failed_opponents:
            print(f"  {Colors.BOLD}Winning requirement:{Colors.RESET}")
            print(
                f"    Win more than {Colors.BOLD}51.55%{Colors.RESET} of rounds against {Colors.BOLD}each{Colors.RESET} opponent")
            print("    (at least 5,155 out of 10,000 rounds)")
            print()
            print("  Tips:")
            print("    'always_heads' always plays 0, 'always_tails' always plays 1")
            print("    'mirror' copies your last move")
            print("    'alternator' switches between 0 and 1 each round")
            print()

        print(f"  {Colors.YELLOW}⚠ DISCLAIMER:{Colors.RESET}")
        print("    Results vary with different seeds and hardware. Consider running")
        print("    the qualifier multiple times to test consistency.")
        print()

        return 2


# ============================================================================
# CHECK COMMAND
# ============================================================================

def cmd_check(args: argparse.Namespace) -> int:
    submitted_path = Path(args.bot)

    if not submitted_path.exists():
        print_failure(f"Bot file not found: {submitted_path}")
        return 1

    sandbox = getattr(args, 'sandbox', False)
    docker = getattr(args, 'docker', False)
    mode_str = _get_execution_mode(sandbox, docker)
    mode_flags = _get_execution_flags(sandbox, docker)

    # Validate backend early
    try:
        # Just validate - run_determinism_check creates its own backend
        _ = get_backend(sandbox=sandbox, docker=docker)
    except RuntimeError as e:
        print_failure(str(e))
        return 1

    print_header(f"Checking Determinism: {submitted_path.name}")

    if docker:
        print_success("✓ Docker backend active")
        print_dim("  Running in isolated containers (matches server environment)")
    elif sandbox:
        print_dim(f"Execution mode: {mode_str}")
    else:
        print_warning("RAM usage is not measured in local mode (both bots share one Python process).")
        print_dim("Use --sandbox for host-measured RAM usage and enforcement.")

    # Determine seed
    if args.seed is not None:
        seed = args.seed
        print_info(f"Using provided seed: {seed}")
    else:
        seed = _generate_random_seed()
        print_info(f"Using random seed: {seed}")

    print_dim(f"To reproduce: nashium check {submitted_path.name} --seed {seed}{mode_flags}")
    print()

    try:
        memory_limit_bytes = _get_effective_memory_limit_bytes(
            sandbox=sandbox,
            docker=docker,
            arg_value=getattr(args, "memory", None),
        )
    except ValueError as e:
        print_failure(str(e))
        return 1

    config = MatchConfig(
        rounds=args.rounds,
        max_total_time_seconds_per_bot=args.time_budget,
        max_total_memory_bytes_per_bot=memory_limit_bytes,
    )

    is_deterministic, failed_opponents = run_determinism_check(
        submitted_path, seed, config, sandbox=sandbox, docker=docker, verbose=True
    )

    print()

    if is_deterministic:
        print_header("")
        print_success("Your bot produces identical moves when given the same seed.")
        print()
        print("  This is required for fair competition. Your bot is ready!")
        print()
        return 0
    else:
        print_header("")
        print_failure("Your bot produces DIFFERENT moves when run twice with the same seed!")
        print()
        print("  This is not allowed. Common causes:")
        print(
            f"    Using {Colors.YELLOW}random.random(){Colors.RESET} instead of {Colors.GREEN}self.rng.random(){Colors.RESET}")
        print(f"    Using {Colors.YELLOW}time.time(){Colors.RESET} or other external state")
        print(f"    Using {Colors.YELLOW}dict{Colors.RESET} iteration (order can vary in older Python)")
        print()
        print("  Fix: Use the seed provided in __init__ for ALL randomness:")
        print(f"    {Colors.CYAN}self.rng = random.Random(seed){Colors.RESET}")
        print(f"    {Colors.CYAN}choice = self.rng.choice([0, 1]){Colors.RESET}")
        print()
        return 3


# ============================================================================
# RUN COMMAND
# ============================================================================

def cmd_run(args: argparse.Namespace) -> int:
    a_path = Path(args.bot_a)
    b_path = Path(args.bot_b)

    if not a_path.exists():
        print_failure(f"Bot file not found: {a_path}")
        return 1
    if not b_path.exists():
        print_failure(f"Bot file not found: {b_path}")
        return 1

    sandbox = getattr(args, 'sandbox', False)
    docker = getattr(args, 'docker', False)
    mode_str = _get_execution_mode(sandbox, docker)
    mode_flags = _get_execution_flags(sandbox, docker)

    # Validate and create backend
    try:
        backend = get_backend(sandbox=sandbox, docker=docker)
    except RuntimeError as e:
        print_failure(str(e))
        return 1

    print_header(f"Match: {a_path.name} vs {b_path.name}")

    if docker:
        print_success("✓ Docker backend active")
        print_dim("  Running in isolated containers (matches server environment)")
    elif sandbox:
        print_dim(f"Execution mode: {mode_str}")
    else:
        print_warning("RAM usage is not measured in local mode (both bots share one Python process).")
        print_dim("Use --sandbox for host-measured RAM usage and enforcement.")

    # Determine seed
    if args.seed is not None:
        seed = args.seed
        print_info(f"Using provided seed: {seed}")
    else:
        seed = _generate_random_seed()
        print_info(f"Using random seed: {seed}")

    print_dim(f"To reproduce: nashium run {a_path.name} {b_path.name} --seed {seed}{mode_flags}")
    print()

    try:
        memory_limit_bytes = _get_effective_memory_limit_bytes(
            sandbox=sandbox,
            docker=docker,
            arg_value=getattr(args, "memory", None),
        )
    except ValueError as e:
        print_failure(str(e))
        return 1

    config = MatchConfig(
        rounds=args.rounds,
        max_total_time_seconds_per_bot=args.time_budget,
        max_total_memory_bytes_per_bot=memory_limit_bytes,
    )

    save_output = bool(getattr(args, "save_output", False))

    # Always use the backend now - it handles all three modes uniformly
    if save_output:
        trace = backend.run_match_trace_between_files(a_path, b_path, seed, config)
        summary = trace.summary

        # Create output folder
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        match_folder_name = f"{a_path.name} vs {b_path.name} (seed {seed}) {timestamp}"
        match_dir = Path(getattr(args, "save_output_dir", "nashium_match_logs")) / match_folder_name
        match_dir.mkdir(parents=True, exist_ok=True)

        a_stem = a_path.stem

        # Write output file (bot_a's moves)
        output_file = match_dir / f"{a_stem}_output.txt"
        submitted_outputs = "\n".join(str(x) for x in trace.submitted_moves)
        if submitted_outputs:
            submitted_outputs += "\n"
        output_file.write_text(submitted_outputs)

        # Calculate per-round wins (1 = bot_a won, 0 = lost)
        per_round_wins = [
            1 if s_move != o_move else 0
            for s_move, o_move in zip(trace.submitted_moves, trace.leaderboard_moves_effective)
        ]

        # Write score file
        score_file = match_dir / f"{a_stem}_score.txt"
        scores = "\n".join(str(x) for x in per_round_wins)
        if scores:
            scores += "\n"
        score_file.write_text(scores)

        if sandbox or docker:
            usage_dir = match_dir / "usage"
            usage_dir.mkdir(parents=True, exist_ok=True)
            (usage_dir / "cpu_usage.txt").write_text(
                "\n".join(str(x) for x in trace.submitted_cpu_usage_samples)
                + ("\n" if trace.submitted_cpu_usage_samples else ""),
                encoding="utf-8",
            )
            (usage_dir / "ram_usage.txt").write_text(
                "\n".join(str(x) for x in trace.submitted_ram_usage_samples)
                + ("\n" if trace.submitted_ram_usage_samples else ""),
                encoding="utf-8",
            )

        print_info(f"Saved match logs to: {match_dir}/")
    else:
        summary = backend.run_match_between_files(a_path, b_path, seed, config)

    status, explanation = format_result(
        summary.result, summary.stat_sig, summary.submitted_wins, summary.rounds
    )

    print(f"  {Colors.BOLD}Results:{Colors.RESET}")
    print(f"    Rounds played:     {summary.rounds:,}")
    print(f"    {a_path.name} wins: {summary.submitted_wins:,} ({summary.submitted_win_rate * 100:.2f}%)")
    print(
        f"    {b_path.name} wins: {summary.rounds - summary.submitted_wins:,} ({(1 - summary.submitted_win_rate) * 100:.2f}%)")
    print()
    print(f"    Result:            {status}")
    print(f"    {explanation}")
    print()

    if summary.submitted_timed_out or summary.leaderboard_timed_out:
        print(f"  {Colors.BOLD}Timeouts:{Colors.RESET}")
        if summary.submitted_timed_out:
            print_warning(f"{a_path.name} exceeded time limit and defaulted to 0 for remaining moves")
        if summary.leaderboard_timed_out:
            print_warning(f"{b_path.name} exceeded time limit and defaulted to 0 for remaining moves")
        print()

    if summary.submitted_memory_exceeded or summary.leaderboard_memory_exceeded:
        print(f"  {Colors.BOLD}Memory Limits:{Colors.RESET}")
        if summary.submitted_memory_exceeded:
            print_warning(f"{a_path.name} exceeded memory limit and defaulted to 0 for remaining moves")
        if summary.leaderboard_memory_exceeded:
            print_warning(f"{b_path.name} exceeded memory limit and defaulted to 0 for remaining moves")
        print()

    print(f"  {Colors.BOLD}Performance:{Colors.RESET}")
    print(f"    {a_path.name} time: {summary.submitted_time_seconds:.3f}s")
    print(f"    {b_path.name} time: {summary.leaderboard_time_seconds:.3f}s")
    print(f"    Total wall time:   {summary.wall_time_seconds:.3f}s")
    if summary.submitted_memory_bytes_peak is not None:
        print(f"    {a_path.name} peak RAM: {summary.submitted_memory_bytes_peak / (1024 * 1024):.1f}MB")
    else:
        print(f"    {a_path.name} peak RAM: n/a")
    if summary.leaderboard_memory_bytes_peak is not None:
        print(f"    {b_path.name} peak RAM: {summary.leaderboard_memory_bytes_peak / (1024 * 1024):.1f}MB")
    else:
        print(f"    {b_path.name} peak RAM: n/a")

    if config.max_total_memory_bytes_per_bot is not None and summary.submitted_memory_bytes_peak is not None:
        print(f"    {a_path.name} memory: {format_memory_warning(summary.submitted_memory_bytes_peak, config.max_total_memory_bytes_per_bot)}")
        print_dim("    (Limit includes Python/runtime overhead.)")
    print()

    return 0