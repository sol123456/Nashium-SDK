from nashium import RoundState


class MyBot:
    def __init__(self, seed: int):
        self._mode = None

    def move(self, state: RoundState) -> int:
        if state.round_index == 0:
            return 0
        if state.round_index == 1:
            return 1
        if state.round_index == 2:
            return 1

        if self._mode is None and len(state.opponent_history) >= 3:
            o0, o1, o2 = state.opponent_history[0], state.opponent_history[1], state.opponent_history[2]
            if o0 == 0 and o1 == 0 and o2 == 0:
                self._mode = "const0"
            elif o0 == 1 and o1 == 1 and o2 == 1:
                self._mode = "const1"
            elif (o0, o1, o2) == (1, 0, 1):
                self._mode = "alternator"
            elif (o0, o1, o2) == (1, 1, 0):
                self._mode = "mirror"
            else:
                self._mode = "fallback"

        if self._mode == "const0":
            return 0
        if self._mode == "const1":
            return 1
        if self._mode == "alternator":
            return 1 if (state.round_index % 2 == 0) else 0
        if self._mode == "mirror":
            return 1 - state.my_history[-1]

        return 0


def create_bot(seed: int):
    return MyBot(seed)
