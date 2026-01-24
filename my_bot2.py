from nashium import RoundState


class MyBot:
    def __init__(self, seed: int):
        self.seed = seed

    def move(self, state: RoundState) -> int:

        if len(state.opponent_history) == 0:
            return 0
        if (state.opponent_history[-1]==state.my_history[-1]):
            return 0
        else:
            return 1


def create_bot(seed: int):
    return MyBot(seed)
