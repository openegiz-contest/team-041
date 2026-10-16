"""Wording shared by the journal and scenario texts."""
from __future__ import annotations

MATERIALS = {"ore": "руда", "low-grade": "бедная руда", "waste": "вскрыша"}


def num(value: float, digits: int = 1) -> str:
    """A number the way Russian text writes it: with a decimal comma."""
    return f"{value:.{digits}f}".replace(".", ",")


def money(kzt: float, digits: int = 1) -> str:
    """Tenge in millions, the way Russian text writes it: '54,2' (followed by 'млн тг')."""
    return num(kzt / 1e6, digits)


def signed(value: float, digits: int = 1) -> str:
    """A difference with its sign: '+1,2', '−0,4' (a true minus sign), '0,0'."""
    text = num(abs(value), digits)
    if text.strip("0,") == "":
        return text
    return ("+" if value > 0 else "−") + text


def thousands(value: float) -> str:
    """A whole number with its thousands set apart by a space: '39 357'."""
    return f"{round(value):,}".replace(",", " ")
