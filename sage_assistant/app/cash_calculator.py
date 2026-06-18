from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from .models import InvoiceLine, normalize_spaces


ARTDIVERS_CODE = "ARTDIVERS"
ARTDIVERS_SAGE_CODE = "Article D"
DEFAULT_VAT_RATE = Decimal("20")
MONEY = Decimal("0.01")
UNIT_PRICE_STEP = Decimal("0.10")
UNIT_PRICE_FLEX = Decimal("0.50")
QUANTITY_FLEX = 5


@dataclass(frozen=True)
class CashVatResult:
    initial_ht: Decimal
    cash_amount: Decimal
    remaining_ht: Decimal
    vat_rate: Decimal
    vat_enabled: bool
    vat_amount: Decimal
    invoice_total: Decimal


@dataclass(frozen=True)
class QuantityOption:
    quantity: int
    unit_price_ht: Decimal
    line_total_ht: Decimal
    difference: Decimal
    mode: str

    @property
    def exact(self) -> bool:
        return self.difference == Decimal("0.00")


@dataclass(frozen=True)
class CashSuggestion:
    cash_amount: Decimal
    remaining_ht: Decimal
    quantity: int
    unit_price_ht: Decimal
    line_total_ht: Decimal
    difference: Decimal
    reason: str


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def calculate_cash_vat(
    initial_ht: Decimal,
    cash_amount: Decimal,
    vat_rate: Decimal = DEFAULT_VAT_RATE,
    vat_enabled: bool = True,
) -> CashVatResult:
    initial = money(initial_ht)
    cash = money(cash_amount)
    rate = Decimal(vat_rate)
    if initial < 0:
        raise ValueError("Le montant HT doit etre positif.")
    if cash < 0:
        raise ValueError("Le montant cash doit etre positif.")
    if cash > initial:
        raise ValueError("Le cash ne peut pas depasser le montant HT.")
    if rate < 0:
        raise ValueError("Le taux de TVA doit etre positif.")
    remaining = money(initial - cash)
    vat = money(remaining * rate / Decimal("100")) if vat_enabled else Decimal("0.00")
    return CashVatResult(
        initial_ht=initial,
        cash_amount=cash,
        remaining_ht=remaining,
        vat_rate=rate,
        vat_enabled=vat_enabled,
        vat_amount=vat,
        invoice_total=money(remaining + vat),
    )


def quantity_option(remaining_ht: Decimal, quantity: int, mode: str = "custom") -> QuantityOption:
    if quantity <= 0:
        raise ValueError("La quantite doit etre superieure a 0.")
    remaining = money(remaining_ht)
    unit_price = money(remaining / Decimal(quantity))
    line_total = money(unit_price * Decimal(quantity))
    return QuantityOption(
        quantity=quantity,
        unit_price_ht=unit_price,
        line_total_ht=line_total,
        difference=money(line_total - remaining),
        mode=mode,
    )


def simple_quantity_options(
    remaining_ht: Decimal,
    total_pieces: int = 0,
    limit: int = 8,
    keep_total_pieces: bool = True,
    min_unit_price_ht: Decimal = Decimal("0"),
    target_unit_price_ht: Decimal | None = None,
) -> list[QuantityOption]:
    remaining = money(remaining_ht)
    unit_target = target_unit_price_ht if target_unit_price_ht is not None else min_unit_price_ht
    if remaining == 0:
        return [QuantityOption(quantity=1, unit_price_ht=Decimal("0.00"), line_total_ht=Decimal("0.00"), difference=Decimal("0.00"), mode="custom")]

    candidates: list[int] = []
    if total_pieces > 0:
        candidates.append(total_pieces)
    candidates.extend([1, 2, 5, 10, 20, 25, 50, 70, 75, 100, 125, 150, 200, 250, 300, 400, 500])
    candidates.extend(range(1, 201))

    seen: set[int] = set()
    options: list[QuantityOption] = []
    for quantity in candidates:
        if quantity <= 0 or quantity in seen:
            continue
        seen.add(quantity)
        try:
            option = quantity_option(remaining, quantity, "total_pieces" if quantity == total_pieces else "custom")
        except ValueError:
            continue
        if not unit_price_matches_target(option.unit_price_ht, unit_target):
            continue
        options.append(option)

    def score(option: QuantityOption) -> tuple[Decimal, int, Decimal, int]:
        unit_cents = int((option.unit_price_ht * 100).to_integral_value())
        easy_unit = 0 if unit_cents % 100 == 0 else 1 if unit_cents % 50 == 0 else 2 if unit_cents % 10 == 0 else 3
        round_quantity = 0 if option.quantity in {1, 2, 5, 10, 20, 25, 50, 70, 75, 100, 125, 150, 200, 250, 300, 400, 500} else 1
        return (abs(option.difference), easy_unit, option.unit_price_ht, round_quantity)

    sorted_options = sorted(options, key=score)
    if keep_total_pieces and total_pieces > 0:
        total_option = next((option for option in options if option.quantity == total_pieces), None)
        if total_option is not None:
            without_total = [option for option in sorted_options if option.quantity != total_pieces]
            return [total_option, *without_total[: max(0, limit - 1)]]
    return sorted_options[:limit]


def suggest_cash_amounts(
    initial_ht: Decimal,
    desired_cash: Decimal,
    total_pieces: int = 0,
    limit: int = 8,
    min_unit_price_ht: Decimal = Decimal("0"),
    target_unit_price_ht: Decimal | None = None,
    target_quantity: int | None = None,
    target_quantity_flex: int = QUANTITY_FLEX,
    cash_flex_eur: Decimal = Decimal("15.00"),
) -> list[CashSuggestion]:
    initial = money(initial_ht)
    desired = money(desired_cash)
    cash_flex = max(Decimal("0.00"), money(cash_flex_eur))
    unit_target = target_unit_price_ht if target_unit_price_ht is not None else min_unit_price_ht
    if initial < 0 or desired < 0 or desired > initial:
        return []

    raw_candidates: set[Decimal] = {desired}
    for step in (Decimal("1"), Decimal("5"), Decimal("10"), Decimal("20"), Decimal("50")):
        for offset in range(-10, 11):
            raw_candidates.add(money(desired + step * offset))
    raw_candidates.update(money(value) for value in (Decimal("0"), initial))
    if target_quantity and target_quantity > 0 and unit_target > 0:
        for unit_price in unit_price_target_values(unit_target):
            for quantity in quantity_target_values(target_quantity, target_quantity_flex):
                raw_candidates.add(money(initial - money(unit_price * Decimal(quantity))))

    suggestions: list[CashSuggestion] = []
    for cash in raw_candidates:
        if cash < 0 or cash > initial:
            continue
        if abs(cash - desired) > cash_flex:
            continue
        remaining = money(initial - cash)
        if target_quantity and target_quantity > 0:
            options = [
                option
                for quantity in quantity_target_values(target_quantity, target_quantity_flex)
                for option in [quantity_option(remaining, quantity, "target_quantity")]
                if unit_price_matches_target(option.unit_price_ht, unit_target)
            ]
            if not options:
                continue
            options = sorted(options, key=lambda option: (abs(option.difference), abs(option.quantity - target_quantity), abs(option.unit_price_ht - unit_target)))[:1]
        else:
            options = simple_quantity_options(
                remaining,
                total_pieces=total_pieces,
                limit=1,
                keep_total_pieces=False,
                target_unit_price_ht=unit_target,
            )
        if not options:
            continue
        option = options[0]
        cash_cents = int((cash * 100).to_integral_value())
        easy_cash = cash_cents % 1000 == 0 or cash_cents % 500 == 0
        exact_text = "calcul exact" if option.exact else f"ecart {option.difference:+.2f}"
        reason = f"{'cash rond' if easy_cash else 'proche cash saisi'}, {option.quantity} x {option.unit_price_ht:.2f}, {exact_text}"
        suggestions.append(
            CashSuggestion(
                cash_amount=cash,
                remaining_ht=remaining,
                quantity=option.quantity,
                unit_price_ht=option.unit_price_ht,
                line_total_ht=option.line_total_ht,
                difference=option.difference,
                reason=reason,
            )
        )

    def score(suggestion: CashSuggestion) -> tuple[Decimal, Decimal, int, Decimal]:
        cash_cents = int((suggestion.cash_amount * 100).to_integral_value())
        easy_cash_penalty = 0 if cash_cents % 1000 == 0 else 1 if cash_cents % 500 == 0 else 2
        return (
            abs(suggestion.difference),
            abs(suggestion.cash_amount - desired),
            easy_cash_penalty,
            suggestion.unit_price_ht,
        )

    unique: dict[Decimal, CashSuggestion] = {}
    for suggestion in sorted(suggestions, key=score):
        unique.setdefault(suggestion.cash_amount, suggestion)
    return list(unique.values())[:limit]


def unit_price_target_values(target_unit_price_ht: Decimal) -> list[Decimal]:
    target = money(target_unit_price_ht)
    if target <= 0:
        return []
    start = max(Decimal("0.00"), target - UNIT_PRICE_FLEX)
    end = target + UNIT_PRICE_FLEX
    values: list[Decimal] = []
    current = (start / UNIT_PRICE_STEP).to_integral_value(rounding=ROUND_HALF_UP) * UNIT_PRICE_STEP
    while current <= end:
        values.append(money(current))
        current += UNIT_PRICE_STEP
    return values


def quantity_target_values(target_quantity: int, flex: int = QUANTITY_FLEX) -> list[int]:
    if target_quantity <= 0:
        return []
    safe_flex = max(0, flex)
    start = max(1, target_quantity - safe_flex)
    end = target_quantity + safe_flex
    return list(range(start, end + 1))


def unit_price_matches_target(unit_price_ht: Decimal, target_unit_price_ht: Decimal) -> bool:
    target = money(target_unit_price_ht)
    unit_price = money(unit_price_ht)
    if target <= 0:
        return True
    if unit_price < money(target - UNIT_PRICE_FLEX) or unit_price > money(target + UNIT_PRICE_FLEX):
        return False
    cents = int((unit_price * 100).to_integral_value())
    return cents % 10 == 0


def build_artdivers_line(
    original_lines: list[InvoiceLine],
    remaining_ht: Decimal,
    quantity: int,
    source: str = "manual",
    cash_reference_ht: Decimal | None = None,
    cash_amount: Decimal | None = None,
    cash_vat_rate: Decimal | None = None,
    cash_vat_enabled: bool | None = None,
    cash_quantity_mode: str = "",
    cash_original_refs: str = "",
) -> InvoiceLine:
    option = quantity_option(remaining_ht, quantity)
    if not option.exact:
        raise ValueError(f"Le total ARTDIVERS ne matche pas le HT restant ({option.difference:+.2f}).")
    refs = normalize_spaces(cash_original_refs or " ".join(line.ref for line in original_lines if line.ref))
    line = InvoiceLine(
        ref=ARTDIVERS_CODE,
        sage_code=ARTDIVERS_SAGE_CODE,
        description=refs or ARTDIVERS_CODE,
        quantity_pieces=option.quantity,
        package_count=None,
        package_size=None,
        unit_price_ht=option.unit_price_ht,
        catalog_unit_price_ht=None,
        order_unit_price_ht=option.unit_price_ht,
        price_confirmed=True,
        product_id=0,
        type_label="",
        source=source,
        cash_reference_ht=money(cash_reference_ht) if cash_reference_ht is not None else None,
        cash_amount=money(cash_amount) if cash_amount is not None else None,
        cash_vat_rate=cash_vat_rate,
        cash_vat_enabled=cash_vat_enabled,
        cash_target_quantity=quantity,
        cash_quantity_mode=cash_quantity_mode,
        cash_original_refs=refs,
    )
    line.validate()
    return line


def adjusted_cash_for_artdivers_match(initial_ht: Decimal, current_cash: Decimal, remaining_ht: Decimal, quantity: int) -> Decimal:
    option = quantity_option(remaining_ht, quantity)
    if option.exact:
        return money(current_cash)
    return money(initial_ht - option.line_total_ht)


def total_ht(lines: list[InvoiceLine]) -> Decimal:
    return money(sum((line.unit_price_ht or Decimal("0")) * Decimal(line.quantity_pieces or 0) for line in lines))


def total_pieces(lines: list[InvoiceLine]) -> int:
    return sum(line.quantity_pieces or 0 for line in lines)


def cash_calculator_allowed(source: str) -> bool:
    return source not in {"PFS", "eFashion"}
