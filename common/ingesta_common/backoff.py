"""Espera exponencial con jitter para reintentos de conexión.

"Equal jitter": la espera queda entre la mitad y el total del techo exponencial.
Nunca se reintenta de inmediato en bucle (Binance limita a 300 intentos de
conexión cada 5 minutos por IP) y varias réplicas no reconectan a la vez.
"""

from __future__ import annotations

import random


class Backoff:
    def __init__(self, base: float = 1.0, tope: float = 60.0, rng: random.Random | None = None):
        if base <= 0 or tope < base:
            raise ValueError("Se requiere 0 < base <= tope")
        self.base = base
        self.tope = tope
        self.intentos = 0
        self._rng = rng or random.Random()

    def techo(self) -> float:
        return min(self.tope, self.base * (2 ** min(self.intentos, 30)))

    def siguiente(self) -> float:
        techo = self.techo()
        self.intentos += 1
        return techo / 2 + self._rng.uniform(0, techo / 2)

    def reiniciar(self) -> None:
        self.intentos = 0
