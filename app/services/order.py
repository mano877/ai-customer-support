"""Order service: placing, listing, tracking, cancelling, and returning orders."""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.user import User
from app.repositories.customer import AddressRepository
from app.repositories.order import OrderRepository
from app.repositories.product import ProductRepository
from app.schemas.order import (
    OrderCreate,
    OrderListResponse,
    OrderResponse,
    OrderTrackingResponse,
    TrackingEventResponse,
)

_TWO_DECIMALS = Decimal("0.01")

# Statuses from which a customer may still cancel the order.
_CANCELLABLE_STATUSES = frozenset(
    {OrderStatus.PENDING, OrderStatus.PAID, OrderStatus.PROCESSING}
)


class OrderService:
    """Business logic for the orders module."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.orders = OrderRepository(db)
        self.products = ProductRepository(db)
        self.addresses = AddressRepository(db)
        self.settings = get_settings()

    # ------------------------------------------------------------------ #
    # Creation
    # ------------------------------------------------------------------ #
    def create_order(self, user: User, payload: OrderCreate) -> OrderResponse:
        """Place an order: validate products, reserve stock, snapshot the address.

        Raises NotFoundError for unknown/inactive products or foreign addresses,
        BadRequestError when the user has no shipping address, and ConflictError
        when stock is insufficient.
        """
        quantities: dict[uuid.UUID, int] = {}
        for item in payload.items:
            quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity

        products: dict[uuid.UUID, Product] = {}
        for product_id, quantity in quantities.items():
            product = self.products.get_public(product_id)
            if product is None:
                raise NotFoundError("Product not found")
            if product.stock_quantity < quantity:
                raise ConflictError(f"Insufficient stock for {product.name}")
            products[product_id] = product

        currency = self._validate_currency(products.values())
        address = self._resolve_shipping_address(user, payload.address_id)
        subtotal, shipping, tax, total = self._compute_totals(products, quantities)

        # Reserve stock atomically so concurrent orders cannot oversell. If the
        # guarded UPDATE touches no rows, stock dropped between check and reserve.
        for product_id, quantity in quantities.items():
            result = self.db.execute(
                update(Product)
                .where(Product.id == product_id, Product.stock_quantity >= quantity)
                .values(stock_quantity=Product.stock_quantity - quantity)
            )
            if result.rowcount != 1:
                self.db.rollback()
                raise ConflictError(f"Insufficient stock for {products[product_id].name}")

        order = Order(
            user_id=user.id,
            order_number=self._generate_order_number(),
            status=OrderStatus.PENDING,
            items_subtotal=subtotal,
            shipping_cost=shipping,
            tax_amount=tax,
            total_amount=total,
            currency=currency,
            shipping_address=self._snapshot_address(address),
            notes=payload.notes,
        )
        order.append_tracking_event(OrderStatus.PENDING.value, "Order placed")
        for product_id, quantity in quantities.items():
            product = products[product_id]
            line_total = (product.price * quantity).quantize(
                _TWO_DECIMALS, rounding=ROUND_HALF_UP
            )
            order.items.append(
                OrderItem(
                    product_id=product.id,
                    sku=product.sku,
                    name=product.name,
                    unit_price=product.price,
                    quantity=quantity,
                    line_total=line_total,
                )
            )

        self.orders.add(order)
        self.db.commit()
        self.db.refresh(order)
        return OrderResponse.model_validate(order)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def list_orders(
        self, user: User, *, limit: int = 20, offset: int = 0
    ) -> OrderListResponse:
        """Return a paginated list of the user's orders, newest first."""
        items = self.orders.list_for_user(user.id, limit=limit, offset=offset)
        total = self.orders.count_for_user(user.id)
        return OrderListResponse(
            items=[OrderResponse.model_validate(order) for order in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_order(self, user: User, order_id: uuid.UUID) -> OrderResponse:
        """Fetch one of the user's orders (404 for foreign or unknown orders)."""
        return OrderResponse.model_validate(self._get_owned_order(user, order_id))

    def get_tracking(self, user: User, order_id: uuid.UUID) -> OrderTrackingResponse:
        """Return the tracking status and event history of an order."""
        order = self._get_owned_order(user, order_id)
        events = [
            TrackingEventResponse.model_validate(event)
            for event in (order.tracking_events or [])
        ]
        return OrderTrackingResponse(
            order_id=order.id,
            order_number=order.order_number,
            status=order.status,
            tracking_number=order.tracking_number,
            carrier=order.carrier,
            estimated_delivery=order.estimated_delivery,
            events=events,
        )

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def cancel_order(self, user: User, order_id: uuid.UUID) -> OrderResponse:
        """Cancel a pending/paid/processing order and restore its stock."""
        order = self._get_owned_order(user, order_id)
        if order.status not in _CANCELLABLE_STATUSES:
            raise ConflictError(
                f"Order cannot be cancelled once it is {order.status.value}"
            )

        # Guarded transition so concurrent cancels cannot both restore stock.
        result = self.db.execute(
            update(Order)
            .where(
                Order.id == order.id,
                Order.status.in_([status.value for status in _CANCELLABLE_STATUSES]),
            )
            .values(status=OrderStatus.CANCELLED.value)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ConflictError("Order cannot be cancelled at this time")
        order.status = OrderStatus.CANCELLED  # keep the in-memory value in sync
        order.append_tracking_event(OrderStatus.CANCELLED.value, "Order cancelled")
        self._restore_stock(order)
        self.db.commit()
        self.db.refresh(order)
        return OrderResponse.model_validate(order)

    def return_order(self, user: User, order_id: uuid.UUID) -> OrderResponse:
        """Request a return for a delivered order."""
        order = self._get_owned_order(user, order_id)
        if order.status != OrderStatus.DELIVERED:
            raise ConflictError(
                "Only delivered orders can be returned "
                f"(current status: {order.status.value})"
            )

        # Guarded transition: only a currently-delivered order may flip.
        result = self.db.execute(
            update(Order)
            .where(Order.id == order.id, Order.status == OrderStatus.DELIVERED.value)
            .values(status=OrderStatus.RETURN_REQUESTED.value)
        )
        if result.rowcount != 1:
            self.db.rollback()
            raise ConflictError("Order cannot be returned at this time")
        order.status = OrderStatus.RETURN_REQUESTED  # keep the in-memory value in sync
        order.append_tracking_event(OrderStatus.RETURN_REQUESTED.value, "Return requested")
        self.db.commit()
        self.db.refresh(order)
        return OrderResponse.model_validate(order)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _get_owned_order(self, user: User, order_id: uuid.UUID) -> Order:
        """Fetch an order owned by the user or raise 404."""
        order = self.orders.get_for_user(order_id, user.id)
        if order is None:
            raise NotFoundError("Order not found")
        return order

    def _resolve_shipping_address(self, user: User, address_id: uuid.UUID | None):
        """Return the requested (owned) address or the user's default one."""
        if address_id is not None:
            address = self.addresses.get_for_user(address_id, user.id)
            if address is None:
                raise NotFoundError("Address not found")
            return address
        addresses = self.addresses.list_for_user(user.id)
        if not addresses:
            raise BadRequestError(
                "No shipping address on file; add one before placing an order"
            )
        return addresses[0]  # list_for_user returns the default first

    def _compute_totals(
        self,
        products: dict[uuid.UUID, Product],
        quantities: dict[uuid.UUID, int],
    ) -> tuple[Decimal, Decimal, Decimal, Decimal]:
        """Compute subtotal, shipping, tax, and total for an order."""
        subtotal = sum(
            products[product_id].price * quantity
            for product_id, quantity in quantities.items()
        )
        subtotal = subtotal.quantize(_TWO_DECIMALS, rounding=ROUND_HALF_UP)
        shipping = self.settings.ORDER_SHIPPING_COST.quantize(_TWO_DECIMALS)
        tax = (subtotal * self.settings.ORDER_TAX_RATE).quantize(
            _TWO_DECIMALS, rounding=ROUND_HALF_UP
        )
        total = (subtotal + shipping + tax).quantize(_TWO_DECIMALS)
        return subtotal, shipping, tax, total

    @staticmethod
    def _validate_currency(products) -> str:
        """Ensure every line shares a currency and return it."""
        currencies = {product.currency for product in products}
        if len(currencies) != 1:
            raise BadRequestError("All items in an order must share the same currency")
        return currencies.pop()

    @staticmethod
    def _snapshot_address(address) -> dict[str, str]:
        """Freeze the shipping details at purchase time."""
        return {
            "label": address.label,
            "recipient_name": address.recipient_name,
            "phone": address.phone or "",
            "street": address.street,
            "city": address.city,
            "state": address.state or "",
            "postal_code": address.postal_code,
            "country": address.country,
        }

    def _restore_stock(self, order: Order) -> None:
        """Return reserved stock to products when an order is cancelled."""
        for item in order.items:
            if item.product_id is not None:
                self.db.execute(
                    update(Product)
                    .where(Product.id == item.product_id)
                    .values(stock_quantity=Product.stock_quantity + item.quantity)
                )

    @staticmethod
    def _generate_order_number() -> str:
        """Generate a short, human-friendly, unique order number."""
        return f"ORD-{uuid.uuid4().hex[:10].upper()}"
