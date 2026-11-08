from pokafer.research.kuhn_cfr import KuhnCFR


def test_cfr_converges_to_equilibrium():
    solver = KuhnCFR(iterations=30000, seed=17)
    expl = solver.exploitability()
    assert 0.0 <= expl < 0.05, f"exploitability слишком велик: {expl}"


def test_nash_has_bluff_and_value_structure():
    solver = KuhnCFR(iterations=30000, seed=17)
    p1 = solver.average_strategy(0)
    # равновесие: с K ставить чаще, чем с Q; с J — блеф с положительной частотой
    bet = {info: s["b"] for info, s in p1.items() if info.endswith("|root")}
    assert bet["K|root"] > bet["Q|root"]
    assert 0.05 < bet["J|root"] < 0.5
