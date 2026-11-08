from typing import Dict
from dataclasses import dataclass, field

"""
Konfiguracja profilu urzytkownika CEO
Każdy parametr opisuje rozkład nigdy twardą regułę. dzięki temu
granice danych są „miękkie" i nie da się ich trywialnie wyuczyć.
"""
@dataclass
class CEOProfileConfig:

    """
    Rozkład truncated normal pozwala na naturalne "ogonki", sporadycznie mniej lub więcej operacji
    """
    weekly_ops_mean: float = 31.0           # μ wolumenu tygodniowego
    weekly_ops_std: float = 8.0             # σ – naturalny rozrzut
    weekly_ops_clip_low: float = 8.0        # dolne obcięcie (twarda ochrona)
    weekly_ops_clip_high: float = 60.0      # górne obcięcie

    """
    Rozkład Beta parametryzuje udział SELECT w całości operacji
    """
    select_ratio_alpha: float = 5.5
    select_ratio_beta: float = 4.5

    """
    Dla SELECT tabela jest losowana z rozkładu kategorycznego drobna szansa na niestandardowy wybór zapobiega hard-boundariom
    """
    select_table_weights: Dict[str, float] = field(default_factory=lambda: {
        "SalesOrderHeader": 0.48,
        "Customer": 0.35,
        "Product": 0.17,
    })

    """
    INSERT CEO pisze niemal wyłącznie do Customer
    Mikro-prawdopodobieństwo (0.01) pozwala na sporadyczne „pomyłki"
    w normalnym ruchu, dzięki czemu model nie zapamięta, że INSERT w inną tabelę to anomalia
    """
    insert_table_weights: Dict[str, float] = field(default_factory=lambda: {
        "Customer": 0.98,
        "SalesOrderHeader": 0.01,
        "Product": 0.01,
    })

    #Opis użytkownika
    user_name: str = "CEO"
    login_name: str = "CEO_User"
    user_role: str = "Executive"

    #Opis urządzeń CEO z przypisaną do nich wagą która opisuje wielomainy rozkład dzienny
    device_weights: Dict[str, float] = field(default_factory=lambda: {
        "Office_PC": 0.75,
        "Home_PC": 0.15,
        "Mobile_Laptop": 0.10,
    })

    #Konkretne metadane sprzętowo-sieciowe powiązane z urządzeniami
    device_metadata: Dict[str, dict] = field(default_factory=lambda: {
        "Office_PC": {
            "client_hostname": "CEO-DESKTOP-OFFICE",
            "simulated_ip": "10.0.4.15",
            "network_zone": "Corporate_LAN",
        },
        "Home_PC": {
            "client_hostname": "CEO-HOME-LAPTOP",
            "simulated_ip": "192.168.1.102",
            "network_zone": "Home_Broadband",
        },
        "Mobile_Laptop": {
            "client_hostname": "CEO-MACBOOK-AIR",
            "simulated_ip": "172.16.88.24",
            "network_zone": "VPN_Remote",
        },
    })

    
    """
    Każde okno to (start_hour, end_hour, peak_hour, spread_hours)
    Czas operacji losowany jest z rozkładu normalnego obciętego do okna to daje gęstsze centrum i rzadkie brzegi
    """
    device_time_windows: Dict[str, dict] = field(default_factory=lambda: {
        "Office_PC": {
            "preferred_days": [1, 2, 3],        
            "spillover_days": [0, 4],            
            "spillover_prob": 0.015,             
            "hour_mean": 12.5,
            "hour_std": 1.5,
            "hour_clip_low": 9.0,
            "hour_clip_high": 16.0,
        },
        "Home_PC": {
            "preferred_days": [0, 1, 2, 3, 4],
            "spillover_days": [],
            "spillover_prob": 0.0,
            "hour_mean": 19.0,
            "hour_std": 1.0,
            "hour_clip_low": 17.0,
            "hour_clip_high": 21.5,
        },
        "Mobile_Laptop": {
            "preferred_days": [0, 1, 2, 3, 4],
            "spillover_days": [],
            "spillover_prob": 0.0,
            "hour_mean": 13.0,
            "hour_std": 2.0,
            "hour_clip_low": 9.0,
            "hour_clip_high": 17.0,
        },
    })

    """
    Operacje CEO grupowane w krótkie sesje
    Liczba operacji w sesji liczna na podstawie: Poisson(λ) + 1, obcięte do [1, max]
    """
    session_ops_lambda: float = 3.0
    session_ops_max: int = 12
    # Odstęp sekund pomiędzy operacjami w jednej sesji: Exponential(β)
    intra_session_interval_beta: float = 45.0  # średni odstęp wewnątrz sesji