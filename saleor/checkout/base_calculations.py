"""Contain functions which are base for calculating checkout properties.

It's recommended to use functions from calculations.py module to take in account plugin
manager.
"""

from decimal import Decimal
from typing import TYPE_CHECKING, Iterable, Optional

from prices import TaxedMoney, Money

from ..core.prices import quantize_price
from ..core.taxes import zero_money, zero_taxed_money
from ..discount import DiscountInfo
from ..order.interface import OrderTaxedPricesData
from .fetch import CheckoutInfo, CheckoutLineInfo

if TYPE_CHECKING:
    from ..channel.models import Channel
    from ..order.models import OrderLine
    from .fetch import ShippingMethodInfo


def _calculate_base_line_unit_price(
    line_info: "CheckoutLineInfo",
    channel: "Channel",
    discounts: Optional[Iterable[DiscountInfo]] = None,
    include_voucher=True,
) -> Money:
    """Calculate base line unit price without voucher applied once per order."""
    variant = line_info.variant
    variant_price = variant.get_price(
        line_info.product,
        line_info.collections,
        channel,
        line_info.channel_listing,
        discounts or [],
        line_info.line.price_override,
    )

    if not include_voucher:
        return quantize_price(variant_price, variant_price.currency)

    if line_info.voucher and not line_info.voucher.apply_once_per_order:
        unit_price = max(
            variant_price
            - line_info.voucher.get_discount_amount_for(variant_price, channel=channel),
            zero_money(variant_price.currency),
        )
    else:
        unit_price = variant_price

    return quantize_price(unit_price, unit_price.currency)


def calculate_base_line_unit_price(
    line_info: "CheckoutLineInfo",
    channel: "Channel",
    discounts: Optional[Iterable[DiscountInfo]] = None,
) -> Money:
    """Calculate line unit prices including discounts and vouchers."""
    prices_data = calculate_base_line_total_price(
        line_info=line_info, channel=channel, discounts=discounts
    )
    quantity = line_info.line.quantity
    currency = prices_data.currency
    return quantize_price(prices_data / quantity, currency)


def calculate_undiscounted_base_line_unit_price(
    line_info: "CheckoutLineInfo",
    channel: "Channel",
) -> Money:
    """Calculate line unit prices excluding discounts and vouchers."""
    return _calculate_base_line_unit_price(
        line_info=line_info, channel=channel, discounts=[], include_voucher=False
    )


def calculate_base_line_total_price(
    line_info: "CheckoutLineInfo",
    channel: "Channel",
    discounts: Optional[Iterable[DiscountInfo]] = None,
) -> Money:
    """Calculate line total prices including discounts and vouchers."""
    unit_price = _calculate_base_line_unit_price(
        line_info=line_info, channel=channel, discounts=discounts
    )
    if line_info.voucher and line_info.voucher.apply_once_per_order:
        variant_price_with_discounts = max(
            unit_price
            - line_info.voucher.get_discount_amount_for(unit_price, channel=channel),
            zero_money(unit_price.currency),
        )
        # we add -1 as we handle a case when voucher is applied only to single line
        # of the cheapest item
        quantity_without_voucher = line_info.line.quantity - 1
        total_price = (
            unit_price * quantity_without_voucher + variant_price_with_discounts
        )
    else:
        total_price = unit_price * line_info.line.quantity

    return quantize_price(total_price, total_price.currency)


def calculate_undiscounted_base_line_total_price(
    line_info: "CheckoutLineInfo",
    channel: "Channel",
) -> Money:
    """Calculate line total prices excluding discounts and vouchers."""
    unit_price = _calculate_base_line_unit_price(
        line_info=line_info, channel=channel, discounts=[], include_voucher=False
    )
    total_price = unit_price * line_info.line.quantity
    return quantize_price(total_price, total_price.currency)


def base_checkout_delivery_price(checkout_info: "CheckoutInfo", lines=None) -> Money:
    """Calculate base (untaxed) price for any kind of delivery method."""
    from .fetch import ShippingMethodInfo

    delivery_method_info = checkout_info.delivery_method_info

    if isinstance(delivery_method_info, ShippingMethodInfo):
        return calculate_base_price_for_shipping_method(
            checkout_info, delivery_method_info, lines
        )

    return zero_money(checkout_info.checkout.currency)


def calculate_base_price_for_shipping_method(
    checkout_info: "CheckoutInfo",
    shipping_method_info: "ShippingMethodInfo",
    lines=None,
) -> Money:
    """Return checkout shipping price."""
    from .fetch import CheckoutLineInfo

    # FIXME: Optimize checkout.is_shipping_required
    shipping_method = shipping_method_info.delivery_method

    if lines is not None and all(isinstance(line, CheckoutLineInfo) for line in lines):
        from .utils import is_shipping_required

        shipping_required = is_shipping_required(lines)
    else:
        shipping_required = checkout_info.checkout.is_shipping_required()

    if not shipping_method or not shipping_required:
        return zero_money(checkout_info.checkout.currency)

    return quantize_price(
        shipping_method.price,
        checkout_info.checkout.currency,
    )


def base_checkout_total(
    checkout_info: "CheckoutInfo",
    discounts: Iterable[DiscountInfo],
    lines: Iterable["CheckoutLineInfo"],
) -> TaxedMoney:
    # TODO In separate PR:
    # Shouldn't return Money?
    """Return the total cost of the checkout."""
    currency = checkout_info.checkout.currency
    line_totals = [
        base_checkout_line_total(
            line_info,
            checkout_info.channel,
            discounts,
        )
        for line_info in lines
    ]
    subtotal = sum(line_totals, zero_taxed_money(currency))

    shipping_price = base_checkout_delivery_price(checkout_info, lines)
    discount = checkout_info.checkout.discount

    zero = zero_taxed_money(currency)
    # TODO In separate PR:
    # FIX, Voucher should be included in ShippingPrice or Subtotal, depends on voucher
    # type
    total = subtotal + shipping_price - discount
    # Discount is subtracted from both gross and net values, which may cause negative
    # net value if we are having a discount that covers whole price.
    # Comparing TaxedMoney objects works only on gross values. That is why we are
    # explicitly returning zero_taxed_money if total.gross is less or equal zero.
    if total.gross <= zero.gross:
        return zero
    return total


def base_checkout_subtotal(
    checkout_lines: Iterable["CheckoutLineInfo"],
    channel: "Channel",
    currency: str,
    discounts: Optional[Iterable[DiscountInfo]] = None,
) -> Money:
    line_totals = [
        calculate_base_line_total_price(
            line,
            channel,
            discounts,
        )
        for line in checkout_lines
    ]

    return sum(line_totals, zero_money(currency))


def base_checkout_line_total(
    checkout_line_info: "CheckoutLineInfo",
    channel: "Channel",
    discounts: Optional[Iterable[DiscountInfo]] = None,
) -> Money:
    """Return the total price of this line."""
    return calculate_base_line_total_price(
        line_info=checkout_line_info, channel=channel, discounts=discounts
    )


def base_order_line_total(order_line: "OrderLine") -> OrderTaxedPricesData:
    quantity = order_line.quantity
    price_with_discounts = (
        TaxedMoney(order_line.base_unit_price, order_line.base_unit_price) * quantity
    )
    undiscounted_price = (
        TaxedMoney(
            order_line.undiscounted_base_unit_price,
            order_line.undiscounted_base_unit_price,
        )
        * quantity
    )
    return OrderTaxedPricesData(
        undiscounted_price=undiscounted_price,
        price_with_discounts=price_with_discounts,
    )


def base_tax_rate(price: TaxedMoney):
    tax_rate = Decimal("0.0")
    # The condition will return False when unit_price.gross or unit_price.net is 0.0
    if not isinstance(price, Decimal) and all((price.gross, price.net)):
        tax_rate = price.tax / price.net
    return tax_rate


def base_checkout_line_unit_price(
    checkout_line_info: "CheckoutLineInfo",
    channel: "Channel",
    discounts: Optional[Iterable[DiscountInfo]] = None,
) -> Money:
    return calculate_base_line_unit_price(
        line_info=checkout_line_info, channel=channel, discounts=discounts
    )
