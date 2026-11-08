from typing import Dict, Tuple
from dataclasses import dataclass, field

"""
Klasa to odpowiada za konfigurowanie anomalii
"""
@dataclass
class AnomalyConfig:
    """
    Parametry kontrolujące wstrzykiwanie anomalii do zbiorów Val/Test
    Każda anomalia jest niezależnym incydentem nakładanym na normalny ruch
    """
    """
    Parametry określają ile incydentów każdej klasy wstrzyknąć w miesiąc
    Wartości dobrane tak, aby anomalie stanowiły ~10-20% całego ruchu
    """
    unusual_time_count: int = 2
    volume_spike_count: int = 1
    unusual_ratio_count: int = 2
    rare_resource_count: int = 2

    #Nietypowa pora: nocne godziny + weekendy
    unusual_time_hour_range: Tuple[int, int] = (1, 5)
    unusual_time_devices: Tuple[str, ...] = ("Home_PC", "Mobile_Laptop")

    #Gwałtowny wzrost: ile dodatkowych operacji w 1-godzinnym burście
    #Zakres ograniczony, aby pojedynczy spike nie zdominował zbioru
    volume_spike_ops_range: Tuple[int, int] = (15, 30)

    #Nietypowa proporcja: stosunek INSERT w serii anomalii
    unusual_ratio_insert_frac_range: Tuple[float, float] = (0.85, 0.98)
    unusual_ratio_ops_range: Tuple[int, int] = (8, 16)

    #Rzadki zasób: zmasowany dostęp do jednej nietypowej tabeli
    rare_resource_table: str = "Product"
    rare_resource_ops_range: Tuple[int, int] = (8, 18)

    #Poziomy severity losowane z dyskretnego rozkładu
    severity_weights: Dict[int, float] = field(default_factory=lambda: {
        1: 0.10, 2: 0.25, 3: 0.35, 4: 0.20, 5: 0.10,
    })
