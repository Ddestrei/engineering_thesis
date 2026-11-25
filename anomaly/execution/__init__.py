"""
Wykonanie operacji symulacji na SQL Server.

Pula połączeń ODBC jest wyłączana przy imporcie pakietu, czyli przed pierwszym połączeniem
w procesie (pyodbc czyta to ustawienie tylko raz, przy pierwszym połączeniu). Dzięki temu
każda sesja generatora to osobne logowanie i osobne połączenie w logu XE
(client_connection_id), bez ponownego użycia połączenia przez sp_reset_connection.
"""
try:
    import pyodbc

    pyodbc.pooling = False
except ImportError:          # testy bez sterownika ODBC
    pass
