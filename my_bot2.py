from nashium import RoundState


class MyBot:
    def __init__(self, seed: int):
        self.seed = seed

    def move(self, state: RoundState) -> int:
        if len(state.opponent_history) == 0:
            return 0
        return state.opponent_history[-1]  # Just predict opponent repeats


def create_bot(seed: int):
    return MyBot(seed)
