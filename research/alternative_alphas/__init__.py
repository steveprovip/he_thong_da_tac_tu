"""Alternative alpha adapters outside crypto HFT. Pure math + read-only scanners.

Every adapter prices an opportunity against its own venue's cost model and
reports `executable` only when gross > 3 x all-in cost. None of them sends an
order: execution needs an account, a jurisdiction check and a human gate.
"""
