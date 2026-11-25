"""
Raport zgodności wygenerowanych danych z profilami ról (kryterium zakończenia Fazy 3)
oraz kontrola założeń D3, D15, D18, D19.
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd

from anomaly.domain import DELETE, INSERT, PERMISSIONS, ROLES, SELECT, UPDATE, is_permitted
from anomaly.generator.dataset import SplitData
from anomaly.generator.profiles import ROLE_PROFILES

WINDOW = "30min"


def _pct(x: float) -> str:
    return f"{100 * x:5.1f}%"


def window_prevalence(events: pd.DataFrame) -> float:
    """Udział okien (użytkownik × 30 min z ≥1 operacją) zawierających zdarzenie incydentu (D2, D18)."""
    w = events.assign(window=pd.to_datetime(events["timestamp"]).dt.floor(WINDOW))
    per_window = w.groupby(["user", "window"])["is_anomaly"].max()
    return float(per_window.mean())


def _full_weeks(events: pd.DataFrame) -> pd.DataFrame:
    """Tygodnie pn–pt w całości wewnątrz okresu zbioru (do porównania wolumenu tygodniowego)."""
    ts = pd.to_datetime(events["timestamp"])
    days = pd.Series(pd.date_range(ts.min().normalize(), ts.max().normalize(), freq="D"))
    monday = days - pd.to_timedelta(days.dt.weekday, unit="D")
    full = days[days.dt.weekday < 5].groupby(monday).size()
    full_mondays = set(full[full == 5].index)
    wk = ts.dt.normalize() - pd.to_timedelta(ts.dt.weekday, unit="D")
    return events.assign(week=wk)[wk.isin(full_mondays)]


def build_report(splits: Dict[str, SplitData]) -> str:
    lines: List[str] = []
    train_all = splits["train"].events
    # Porównanie z dokumentem dotyczy pracy regularnej: bez dodatkowych sesji skoku SELECT (osobny przypadek D15).
    train = train_all[~train_all["hard_normal"].fillna("").str.contains("SELECT_SPIKE")]
    lines.append("=" * 100)
    lines.append("RAPORT GENERATORA (Faza 3) – zgodność z profilami ról i METODOLOGIA.md")
    lines.append("=" * 100)
    for split, data in splits.items():
        e = data.events
        lines.append(f"{split:10} zdarzeń {len(e):6}  sesji {e['session_id'].nunique():5}  "
                     f"użytkowników {e['user'].nunique():2}  incydentów {len(data.incidents):3}  "
                     f"rekordów startowych {len(data.seeds):5}  okres {e['timestamp'].min()} – {e['timestamp'].max()}")

    # --- D3: uprawnienia -------------------------------------------------------------
    viol = sum(int((~d.events.apply(lambda r: is_permitted(r["role"], r["operation_type"], r["target_table"]), axis=1)).sum())
               for d in splits.values())
    lines.append(f"\n[D3] Operacje niezgodne z uprawnieniami (wszystkie zbiory): {viol}  {'OK' if viol == 0 else 'BŁĄD'}")

    # --- profile ról (TRAIN, norma) ----------------------------------------------------
    lines.append("\nPROFILE RÓL – TRAIN (dokument: wolumen tygodniowy i udziały operacji)")
    lines.append(f"{'rola':10} {'tydz. P10–P90':>14} {'dokument':>10} {'SELECT':>7} {'INSERT':>7} {'UPDATE':>7} {'DELETE':>7}  weekend*  po godz.  alt.IP  rzadka tab.")
    weekly = _full_weeks(train)
    for role in ROLES:
        prof = ROLE_PROFILES[role]
        r = train[train["role"] == role]
        not_month_end = ~r["hard_normal"].fillna("").str.contains("MONTH_END")
        wr = weekly[(weekly["role"] == role)]
        if prof.month_end:
            me_weeks = set(wr.loc[wr["hard_normal"].fillna("").str.contains("MONTH_END"), "week"])
            wr = wr[~wr["week"].isin(me_weeks)]
        per_user_week = wr.groupby(["user", "week"]).size()
        p10, p90 = (per_user_week.quantile(0.1), per_user_week.quantile(0.9)) if len(per_user_week) else (0, 0)
        shares = r[not_month_end]["operation_type"].value_counts(normalize=True)
        hn = r["hard_normal"].fillna("")
        wknd_illegal = int(((pd.to_datetime(r["timestamp"]).dt.weekday >= 5) & ~hn.str.contains("WEEKEND")).sum())
        lo, hi = prof.normal.weekly_volume
        lines.append(
            f"{role:10} {p10:6.0f}–{p90:<6.0f} {f'{lo:.0f}–{hi:.0f}':>10} "
            + " ".join(f"{_pct(shares.get(op, 0.0)):>7}" for op in (SELECT, INSERT, UPDATE, DELETE))
            + f"  {wknd_illegal:>7}  {_pct(hn.str.contains('AFTER_HOURS').mean()):>7} "
              f"{_pct(hn.str.contains('ALT_IP').mean()):>6}  {_pct(hn.str.contains('RARE_TABLE').mean()):>6}")
    lines.append("  * weekend: zdarzenia normy w weekend BEZ tagu HARD_NORMAL_WEEKEND (musi być 0)")
    lines.append("  Wolumen: per użytkownik i pełny tydzień; zawiera indywidualny czynnik ×0.85–1.15 (D15) oraz")
    lines.append("  operacje sekwencji (Sales: zmiana adresu), więc może lekko wychodzić poza przedział z dokumentu.")
    lines.append(f"  Bez sesji skoku SELECT (osobny trudny przypadek D15): stanowią {_pct(1 - len(train) / len(train_all))} operacji TRAIN.")

    cfo = train[train["role"] == "CFO"]
    me = cfo["hard_normal"].fillna("").str.contains("MONTH_END")
    starts = cfo.assign(me=me).groupby("session_id").agg(start=("timestamp", "min"), me=("me", "any"))
    late = int((~starts["me"] & (pd.to_datetime(starts["start"]).dt.hour >= 17)).sum())
    lines.append(f"\nCFO koniec miesiąca: SELECT {_pct((cfo[me]['operation_type'] == SELECT).mean())} (dokument 71–89%), "
                 f"poza końcem: {_pct((cfo[~me]['operation_type'] == SELECT).mean())} (dokument 20–30%); "
                 f"sesje rozpoczęte po 17:00 poza końcem miesiąca: {late} (musi być 0)")
    ceo = train[train["role"] == "CEO"]
    lines.append("CEO urządzenia: " + ", ".join(f"{k} {_pct(v)}" for k, v in
                 ceo.groupby("session_id")["device_context"].first().value_counts(normalize=True).items())
                 + " (dokument: biuro ~75%, dom ~15%, laptop ~10%)")
    office = ceo[ceo["device_context"] == "Office_PC"]
    lines.append(f"CEO biuro: dni {sorted(pd.to_datetime(office['timestamp']).dt.weekday.unique().tolist())} "
                 f"(dokument: wt–czw = 1,2,3), godziny {pd.to_datetime(office['timestamp']).dt.hour.min()}–"
                 f"{pd.to_datetime(office['timestamp']).dt.hour.max()} (dokument 10–15, ± przesunięcie użytkownika ≤1 h)")

    # --- incydenty -------------------------------------------------------------------
    for split in ("validation", "test"):
        inc = splits[split].incidents
        e = splits[split].events
        lines.append(f"\nINCYDENTY – {split}: {len(inc)} (D18: VAL 72 = 6 ról × 4 klasy × 3; TEST 3 ± 1 na klasę i rolę)")
        lines.append(inc.pivot_table(index="anomaly_class", columns="intensity", values="incident_id",
                                     aggfunc="count", fill_value=0).to_string())
        lines.append("severity (D19): " + ", ".join(f"{k}: {v}" for k, v in
                     inc["severity_level"].value_counts().sort_index().items()))
        lines.append(f"udział okien anomalnych (D18, cel 5–10%): {_pct(window_prevalence(e))}")
    return "\n".join(lines)
