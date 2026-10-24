import time

from src.continuous_double_auction.cda_types import Agent, ExperimentParams
from src.continuous_double_auction.market.market import Market


def params():
    return ExperimentParams(seller_valuations=[80.0] * 2, buyer_valuations=[100.0] * 2, seller_models=["m"] * 2,
                            buyer_models=["m"] * 2, rounds=3)


class FastSeller(Agent):
    def generate_bid_response(self, **kwargs):
        return {"ask": 96.0}


class FastBuyer(Agent):
    def generate_bid_response(self, **kwargs):
        return {"bid": 84.0}


class SlowSeller(Agent):
    """Reads its inputs only after the fast agents' new orders have been written to the live queues."""
    market: object = None
    seen: dict = {}

    def generate_bid_response(self, **kwargs):
        deadline = time.time() + 5
        while time.time() < deadline:
            asks, bids = list(self.market.seller_ask_queue), list(self.market.buyer_bid_queue)
            if (96.0, "Seller F") in asks and (84.0, "Buyer F") in bids:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("fast agents' orders never reached the live queues")
        self.seen["asks"], self.seen["bids"] = list(kwargs["ask_queue"]), list(kwargs["bid_queue"])
        return {"ask": 91.0}


def test_slow_agent_does_not_see_orders_placed_earlier_in_the_same_round():
    # Agents run in parallel threads, and run_round writes each finished agent's new order to the live
    # queues while slower agents may still be reading their inputs. They must get a start-of-round snapshot.
    p = params()
    slow = SlowSeller(id="Seller S", company="C", valuation=80.0, expt_params=p)
    fast_seller = FastSeller(id="Seller F", company="C", valuation=80.0, expt_params=p)
    fast_buyer = FastBuyer(id="Buyer F", company="C", valuation=100.0, expt_params=p)
    other_buyer = FastBuyer(id="Buyer G", company="C", valuation=100.0, expt_params=p)
    market = Market(sellers=[slow, fast_seller], buyers=[fast_buyer, other_buyer])
    slow.market = market
    market.set_initial_orders([(95.0, "Seller S"), (97.0, "Seller F")], [(82.0, "Buyer F"), (81.0, "Buyer G")])
    asks_before, bids_before = list(market.seller_ask_queue), list(market.buyer_bid_queue)
    market.run_round()
    assert slow.seen["asks"] == asks_before
    assert slow.seen["bids"] == bids_before
