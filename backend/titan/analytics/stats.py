# -*- coding: utf-8 -*-
"""
Математика для MES-аналитики (специф §9.2 «Аналитика»).

Все функции — чистые (без БД и сети), на входе список чисел, на выходе
структуры для графиков. Требования ТЗ:
- для каждой переменной: Макс/Мин/среднее;
- гистограммы: интервал макс. частоты, мода, медиана, среднее, σ/3σ;
- контрольные карты Шухарта (CL ± 3σ);
- контрольные карты кумулятивных сумм (CUSUM).
"""
import math


def basic_stats(data: list[float]) -> dict:
    """Базовая статистика выборки: count, min, max, mean, std (σ выборки).

    Требование ТЗ 4 «Для каждой переменной указываются Макс/Мин и среднее значения».
    Пустая выборка -> нули (без деления на ноль — график просто не покажет значений).
    """
    n = len(data)
    if n == 0:
        return {"count": 0, "min": 0.0, "max": 0.0, "mean": 0.0, "std": 0.0}
    mean = sum(data) / n
    if n > 1:
        # Выборочная σ (n-1) — стандарт для контрольных карт
        var = sum((x - mean) ** 2 for x in data) / (n - 1)
        std = math.sqrt(var)
    else:
        std = 0.0
    return {"count": n, "min": min(data), "max": max(data), "mean": mean, "std": std}


def moving_average(data: list[float], window: int = 5) -> list[float | None]:
    """Скользящее среднее (окно window). Первые window-1 точек — None
    (график начнётся с первой полной точки — усреднение ТЗ 0,5–15 мин)."""
    if window <= 1:
        return list(data)
    out: list[float | None] = [None] * len(data)
    for i in range(window - 1, len(data)):
        out[i] = sum(data[i - window + 1 : i + 1]) / window
    return out


def histogram_stats(data: list[float], bins: int = 20,
                    lo: float | None = None, hi: float | None = None) -> dict:
    """Статистика гистограммы (ТЗ: мода, медиана, среднее, σ, 3σ, интервал
    максимальной частоты) + бины для отрисовки.

    Args:
        bins: количество бинов
        lo/hi: границы диапазона (None -> min/max данных)
    """
    if not data:
        return {"count": 0, "bin_counts": [], "bin_edges": [], "mode": 0.0,
                "median": 0.0, "mean": 0.0, "std": 0.0, "max_bin_count": 0}

    s = basic_stats(data)
    lo = lo if lo is not None else min(data)
    hi = hi if hi is not None else max(data)
    if hi <= lo:                      # одна константа — единственный бин
        hi = lo + 1.0

    width = (hi - lo) / bins
    counts = [0] * bins
    for x in data:
        idx = int((x - lo) / width)
        idx = min(max(idx, 0), bins - 1)   # крайние значения в последний бин
        counts[idx] += 1

    edges = [lo + i * width for i in range(bins + 1)]

    # Мода: центр бина с максимальной частотой (ТЗ: «интервал, имеющий
    # максимальную частоту»)
    max_idx = counts.index(max(counts))
    mode = edges[max_idx] + width / 2

    # Медиана: середина отсортированной выборки
    sorted_data = sorted(data)
    n = len(sorted_data)
    mid = n // 2
    median = sorted_data[mid] if n % 2 else (sorted_data[mid - 1] + sorted_data[mid]) / 2

    return {
        "count": n,
        "bin_counts": counts,
        "bin_edges": edges,
        "mode": mode,
        "median": median,
        "mean": s["mean"],
        "std": s["std"],
        "std3": s["std"] * 3,          # 3σ для отображения границ (ТЗ)
        "max_bin_count": max(counts),
    }


def shewhart_zones(data: list[float]) -> dict:
    """Контрольная карта Шухарта (ТЗ): CL = среднее, UCL/LCL = CL ± 3σ.

    Точки за пределами ±3σ — процесс вышел из-под контроля
    (возвращаются индексы для подсветки на графике).
    """
    s = basic_stats(data)
    cl, std = s["mean"], s["std"]
    ucl, lcl = cl + 3 * std, cl - 3 * std
    # Индексы точек вне контрольных границ (выход процесса из-под контроля)
    out_of_control = [i for i, x in enumerate(data) if x > ucl or x < lcl]
    return {
        "cl": cl, "ucl": ucl, "lcl": lcl,
        "points": list(data),
        "out_of_control": out_of_control,
    }


def cusum(data: list[float], target: float) -> list[float]:
    """Контрольная карта кумулятивных сумм (ТЗ).

    CUSUM[i] = сумма отклонений (x - target) от начала выборки до i.
    Линейный дрейф виден как линейный рост/спад кривой.
    """
    out: list[float] = []
    total = 0.0
    for x in data:
        total += x - target
        out.append(total)
    return out
