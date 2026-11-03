"""Piezas compartidas por los microservicios de SecureChain.

- config: variables de entorno, secretos montados como archivo y parámetros de MySQL (con TLS).
- logs: logs en JSON por stdout (los recoge Docker o Container Apps / Log Analytics).
- backoff: espera exponencial con jitter para reconexiones.
- salud: endpoint HTTP /healthz para las sondas de Container Apps.
- mysql_sink: escritura por lotes en MySQL con reconexión.
"""
